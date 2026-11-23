"""Offline journal contract check. No workflow, notification, publication or remote order."""

import hashlib
import json
import os
from datetime import date
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from .run import pace_to_record
from .session import Session


@pytest.fixture
def archive_copy(tmp_path):
    source = Path(os.environ["MARKETS_DATA"]).resolve()
    folder = Path("data/nymex_mbo/ng_v0_oct2023")
    root = tmp_path / "markets-data"
    (root / folder).mkdir(parents=True)
    names = ["MANIFEST.json", "NG_v0_mbo_20231001.dbn.zst", "NG_v0_mbo_20231002.dbn.zst"]
    import shutil

    for name in names:
        shutil.copyfile(source / folder / name, root / folder / name)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    git("init", "--quiet")
    git(
        "remote",
        "add",
        "origin",
        subprocess.check_output(["git", "-C", str(source), "remote", "get-url", "origin"], text=True).strip(),
    )
    git("add", str(folder / "MANIFEST.json"))
    git(
        "-c",
        "user.name=Offline verification",
        "-c",
        "user.email=verify@localhost",
        "commit",
        "-qm",
        "Actual archive manifest",
    )
    return root


def test_local_step_retry_and_forced_close_use_the_actual_tape(archive_copy):
    root = archive_copy
    from .feed import HistoricalFeed
    from .engine import Engine

    day = date(2023, 10, 2)
    engine = Engine(day, "reference")
    folder = Path("data/nymex_mbo/ng_v0_oct2023")
    feed = HistoricalFeed(root / folder, day)
    cutoff = next(m["cursor"] for m in feed.messages() if any(e["action"] == "open" for e in engine.tick(m)))
    with (root / folder / "MANIFEST.json").open(encoding="utf8") as f:
        records = sum(x["records"] for x in json.load(f)["files"])
    session = Session(root, day, message_budget=cutoff + 1, output_budget=records)
    while session.state == "active":
        previous = session.cursor
        session.step(previous)
        if session.state == "active":
            with pytest.raises(ValueError, match="cursor changed"):
                session.step(previous)
    receipt = json.loads((session.folder / "receipt.json").read_text(encoding="utf8"))
    assert receipt["status"] == "budget reached" and not session.engine.open
    assert (session.folder / "receive-ns.u64le").stat().st_size == 8 * (cutoff + 1)
    for name, checksum in receipt["files"].items():
        assert hashlib.sha256((session.folder / name).read_bytes()).hexdigest() == checksum
    events = [json.loads(row) for row in (session.folder / "events.jsonl").read_text().splitlines()]
    orders = [e for e in events if e["kind"] == "paper order"]
    assert orders and len({e["order_id"] for e in orders}) == len(orders)
    assert all(e["fill_price"] is None for e in orders)
    assert any(e.get("trade_data") is False and e["reason"] == "budget reached" for e in orders)


def test_paced_expiry_reaches_journal_before_next_message(archive_copy):
    from .market import markets
    from .views import card

    day = date(2023, 10, 2)
    events = []
    folder = archive_copy / "data/nymex_mbo/ng_v0_oct2023"
    budget = sum(row["records"] for row in json.loads((folder / "MANIFEST.json").read_bytes())["files"])
    session = Session(archive_copy, day, message_budget=budget, output_budget=budget, on_event=events.append)
    stopped = threading.Event()
    try:
        source = session.feed.week[0].records
        for row in source:
            received = int(row["ts_recv"])
            expiry = session.engine.next_expiry_ns
            if expiry is not None and expiry < received:
                cursor = session.cursor
                events.clear()
                # The host is already caught up to this purchased record. The scheduler must
                # still publish all earlier clock decisions before admitting the record.
                base = time.monotonic_ns() - received
                publication = SimpleNamespace(last_push=time.monotonic())
                pace_to_record(session, received, base, stopped, publication, lambda: None)
                assert session.cursor == cursor
                for event in events:
                    if event["kind"] == "paper order" and event.get("expiry_references"):
                        assert event["received"] < received and event["exchange"] is None
                        assert "Window expiry (ns)" in card(event, markets()["nymex-ng"])
                        return
            session.step(session.cursor)
        pytest.fail("the purchased prefix did not exercise an expiry-driven order")
    finally:
        session.stop("end of offline verification")
