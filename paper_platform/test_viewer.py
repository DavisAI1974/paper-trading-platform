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
