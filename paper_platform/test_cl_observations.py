"""Check rule outcomes and timer provenance on the first purchased NG file."""

from datetime import date
from itertools import islice
import os
from pathlib import Path

import numpy as np
import pytest

from crypto_ng.tick import LINE_NS, line, reads
from .engine import Engine
from .feed import HistoricalFeed
from .rulebook import catalogue


@pytest.fixture(scope="module")
def feed():
    return HistoricalFeed(Path(os.environ["MARKETS_DATA"]) / "data/nymex_mbo/ng_v0_oct2023", date(2023, 10, 2))


def test_cl01_records_which_step_came_first(feed):
    engine = Engine(feed.trade_day, "cl01-check", {"CL01": catalogue()["CL01"]})
    facts, closes = {}, {}
    # Both outcomes occur in the first thousand purchased records.
    for msg in islice(feed.messages(), 981):
        for event in engine.tick(msg):
            if event["action"] == "close":
                closes[event["position"]] = event
        if msg["in_session"]:
            t = engine.last
            facts[t["position"]] = (t["event"], t["quote_facts"])

    for close_pos, expected_third in [(482, None), (980, 948)]:
        event = closes[close_pos]
        entry = event["entry"]["position"]
        reference = event.get("third_step_reference")
        assert reference == ((feed.week[0].sha256, expected_third) if expected_third else None)
        quote_name = "best bid" if event["side"] == "sell" else "best ask"
        reversal = next(f for f in facts[close_pos][1] if f["quote"] == quote_name and f["fact"] == "first step back")
        qualifying = [
            pos for pos in range(entry + 1, close_pos) if facts[pos][0] != facts[entry][0]
            and any(f["quote"] == quote_name and f["run_start_position"] == reversal["run_start_position"]
                    and f["moves"] == 3 and f["fact"] == "new furthest price" for f in facts[pos][1])
        ]
        assert qualifying == ([expected_third] if expected_third else [])
        assert ("without a third step" in event["reason"]) == (expected_third is None)


def test_timer_and_tick_replay_close_on_the_same_root_expiries(feed):
    rules = {key: catalogue()[key] for key in ("CL07", "CL08")}
    paced = Engine(feed.trade_day, "expiry-check", rules)
    replay = Engine(feed.trade_day, "expiry-check", rules)
    source = feed.week[0]
    times, sides, sizes = [], [], []
    closes = []
    previous = None
    # Contains real timer-driven closes for both rules without reaching a file boundary.
    for msg in islice(feed.messages(), 4398):
        raw = msg["raw"]
        timers = paced.advance_clock(raw["ts_recv"] - 1) if msg["in_session"] else []
        assert timers + paced.tick(msg) == replay.tick(msg)
        for close in timers:
            assert close["received"] == close["expiry_ns"] < raw["ts_recv"]
            assert close["position"] is None and close["exchange"] is None
            assert previous["raw"]["ts_recv"] < close["received"]
            assert (close["bid"], close["ask"]) == (previous["best_bid"], previous["best_ask"])
            assert close["expiry_quote_reference"] == (source.sha256, previous["record_index"])
            for digest, index in close["expiry_references"]:
                record = source.records[index]
                assert digest == source.sha256 and record["action"] == b"T"
                assert int(record["ts_recv"]) + LINE_NS == close["received"]
            closes.append(close)
        if msg["in_session"] and raw["action"] == "T" and raw["side"] in ("A", "B"):
            times.append(raw["ts_recv"])
            sides.append(raw["side"] == "B")
            sizes.append(raw["size"])
        previous = msg

    assert {e["rule"] for e in closes} == set(rules)
    reference = reads(np.array(times), np.array(sides), np.array(sizes), LINE_NS, previous["raw"]["ts_recv"])
    values, empty = line(reference["bought"], reference["sold"])
    direction = np.where(empty, 0, np.sign(values))
    for close in closes:
        index = int(np.searchsorted(reference["moment"], close["expiry_ns"]))
        assert reference["moment"][index] == close["expiry_ns"] and reference["trade"][index] == -1
        assert direction[index - 1] == (1 if close["side"] == "buy" else -1)
        assert direction[index] != direction[index - 1]
