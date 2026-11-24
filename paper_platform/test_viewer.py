"""Exercise the phone view and event API with an actual decision from a purchased tape prefix."""

import json
import os
from datetime import date
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from . import relay
from .api import wire
from .engine import Engine
from .feed import HistoricalFeed


@pytest.fixture
def viewer(tmp_path, monkeypatch):
    view, ingest = secrets.token_urlsafe(), secrets.token_urlsafe()
    monkeypatch.setenv("PAPER_VIEW_PASSWORD", view)
    monkeypatch.setenv("PAPER_RELAY_TOKEN", ingest)
    monkeypatch.setenv("PAPER_RELAY_DB", str(tmp_path / "relay.sqlite"))
    return TestClient(relay.relay()), ("greg", view), {"Authorization": "Bearer " + ingest}


@pytest.fixture(scope="module")
def opening():
    source = HistoricalFeed(
        Path(os.environ["MARKETS_DATA"]) / "data/nymex_mbo/ng_v0_oct2023", date(2023, 10, 2)
    )
    run = uuid.uuid4().hex
    engine = Engine(source.trade_day, run)
    decision = next(e for m in source.messages() for e in engine.tick(m) if e["action"] == "open")
    event = wire(
        dict(run=run, market="nymex-ng", sequence=0, recorded_ns=time.time_ns(), kind="signal", **decision)
    )
    return dict(event=event, status=dict(run=run, market=event["market"], events=1, status="active"))


def finish(client, headers, run, sequence):
    reason = "end of offline verification"
    event = wire(dict(
        run=run, market="nymex-ng", sequence=sequence, kind="status",
        recorded_ns=time.time_ns(), reason=reason, trade_data=False,
    ))
    status = dict(run=run, market=event["market"], events=sequence + 1, status=reason)
    response = client.post("/ingest", json=dict(event=event, status=status), headers=headers)
    assert response.json() == {"next": sequence + 1}


def test_html_api_and_stream_share_one_recorded_event(viewer, opening):
    client, auth, headers = viewer
    packet, event = opening, opening["event"]
    assert client.get("/").status_code == 401
    assert "Waiting for a session" in client.get("/", auth=auth).text
    assert client.post("/ingest", json=packet, auth=auth).status_code == 403
    for _ in range(2):
        assert client.post("/ingest", json=packet, headers=headers).json() == {"next": 1}
    rows = client.get("/api/events", params={"rule": event["rule"]}, auth=auth).text.splitlines()
    assert len(rows) == 1
    assert str(event["received"]) in rows[0]
    html = client.get("/", params={"rule": event["rule"]}, auth=auth).text
    assert "Henry Hub Natural Gas" in html and event["trade_id"] in html and "USD/MMBtu" in html
    assert "Rule definition" in html and "EventSource" in html
    assert {e["id"] for e in client.get("/api/markets", auth=auth).json().values()} == {"nymex-ng"}
    assert client.get("/api/events", params={"market": "unregistered"}, auth=auth).status_code == 404


def test_waiting_dashboard_keeps_requested_run(viewer, opening):
    client, auth, _ = viewer
    run = opening["event"]["run"]
    html = client.get("/", params={"run": run}, auth=auth).text
    assert f"run={run}" in html
    assert f'name="run" value="{run}"' in html


def test_follow_waits_for_the_requested_run(viewer, opening, monkeypatch):
    client, auth, headers = viewer
    run = opening["event"]["run"]
    published = False

    async def publish_during_wait(_delay):
        nonlocal published
        assert not published, "the stream missed the requested run"
        published = True
        assert client.post("/ingest", json=opening, headers=headers).json() == {"next": 1}
        finish(client, headers, run, 1)
        # The other run is now the most recent one for this market.
        finish(client, headers, uuid.uuid4().hex, 0)

    monkeypatch.setattr(relay, "asyncio", SimpleNamespace(sleep=publish_during_wait))
    response = client.get("/api/events", params={"run": run, "follow": True}, auth=auth)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [(row["run"], row["sequence"]) for row in rows] == [(run, 0), (run, 1)]


def test_follow_delivers_a_close_committed_during_a_poll(viewer, opening, monkeypatch):
    client, auth, headers = viewer
    run = opening["event"]["run"]
    assert client.post("/ingest", json=opening, headers=headers).json() == {"next": 1}
    connect = sqlite3.connect
    with connect(os.environ["PAPER_RELAY_DB"]) as db:
        db.execute("PRAGMA journal_mode=WAL")
    closed = False

    def close_while_reading(cursor, row):
        nonlocal closed
        if not closed and [column[0] for column in cursor.description] == ["sequence", "body"]:
            closed = True
            finish(client, headers, run, 1)
        return row

    def connect_reader(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.row_factory = close_while_reading
        return db

    monkeypatch.setattr(relay, "sqlite3", SimpleNamespace(connect=connect_reader))
    async def next_poll(_delay):
        assert closed, "expected the writer to commit during the first read"

    monkeypatch.setattr(relay, "asyncio", SimpleNamespace(sleep=next_poll))
    response = client.get("/api/events", params={"run": run, "follow": True}, auth=auth)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert closed and [row["sequence"] for row in rows] == [0, 1]
    assert rows[-1]["reason"] == "end of offline verification"


def test_filtered_stream_reports_closure_after_the_last_matching_event(viewer, opening):
    client, auth, headers = viewer
    event = opening["event"]
    run = event["run"]
    assert client.post("/ingest", json=opening, headers=headers).json() == {"next": 1}
    finish(client, headers, run, 1)

    params = {"run": run, "rule": event["rule"]}
    stream = client.get("/stream", params=params, auth=auth)
    message, finished = stream.text.strip().split("\n\n")
    assert message.startswith(f"id: {run}/0\nevent: message\n")
    kind, data = finished.splitlines()
    assert kind == "event: finished"
    assert json.loads(data.removeprefix("data: "))["status"] == "end of offline verification"

    resumed = client.get("/stream", params=params, headers={"Last-Event-ID": f"{run}/0"}, auth=auth)
    assert resumed.text.strip() == finished


def test_strategy_trade_list_pairs_only_its_own_orders(viewer):
    from itertools import islice

    client, auth, headers = viewer
    source = HistoricalFeed(
        Path(os.environ["MARKETS_DATA"]) / "data/nymex_mbo/ng_v0_oct2023", date(2023, 10, 2)
    )
    run = uuid.uuid4().hex
    engine = Engine(source.trade_day, run)
    decisions = [d for message in islice(source.messages(), len(source.week[0].records))
                 for d in engine.tick(message)]
    decisions.extend(engine.stop("end of offline verification"))
    closed = next(d for d in decisions if d["action"] == "close" and d["trade_data"])
    forced = next(d for d in decisions if d["action"] == "close" and not d["trade_data"])
    manager = next(d for d in decisions if d["action"] == "exit" and d.get("management_only"))
    openings = {d["trade_id"]: d for d in decisions if d["action"] == "open"}
    keys = {closed["trade_id"], forced["trade_id"], manager["trade_id"], manager["source_trade_id"]}
    sequence = 0
    expected = {}
    params = dict(rule=closed["rule"], run=run)
    assert client.get("/api/trades", params=params).status_code == 401
    for decision in decisions:
        if decision["trade_id"] not in keys:
            continue
        for kind in ("signal", "paper order") if decision["action"] in ("open", "exit", "close") else ("signal",):
            event = wire(dict(run=run, market="nymex-ng", sequence=sequence, recorded_ns=time.time_ns(),
                              kind=kind, **decision))
            packet = dict(event=event, status=dict(run=run, market="nymex-ng", status="active"))
            assert client.post("/ingest", json=packet, headers=headers).json() == {"next": sequence + 1}
            sequence += 1
            if kind == "paper order" and decision["action"] == "open":
                expected[decision["trade_id"]] = event
                rows = client.get("/api/trades", params=dict(params, rule=decision["rule"]), auth=auth).json()
                row = next(r for r in rows if r["trade_id"] == decision["trade_id"])
                assert row["entry"] == event and row["exit"] is None
    finish(client, headers, run, sequence)
    for exit_order in (closed, forced, manager):
        params["rule"] = exit_order["rule"]
        rows = client.get("/api/trades", params=params, auth=auth).json()
        row, = [r for r in rows if r["trade_id"] == exit_order["trade_id"]]
        source_id = exit_order.get("source_trade_id", exit_order["trade_id"])
        assert row["entry"] == expected[source_id]
        for field in ("rule", "received", "reason", "bid", "ask", "reference"):
            assert row["exit"][field] == wire(exit_order[field])
        html = client.get("/", params=params, auth=auth).text
        assert f'id="trade-{exit_order["trade_id"]}"' in html
        assert str(openings[source_id]["received"]) in html and str(exit_order["received"]) in html
        stream = client.get("/stream", params=params, auth=auth).text
        frames = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
        matching = [f["trade_html"] for f in frames if f.get("trade_id") == exit_order["trade_id"]]
        assert matching and exit_order["reason"] in matching[-1]
        if exit_order is forced:
            assert "Administrative close" in matching[-1]
        if exit_order is manager:
            assert "Manager exit" in matching[-1]
    assert client.get("/api/trades", params=dict(params, run=uuid.uuid4().hex), auth=auth).json() == []
