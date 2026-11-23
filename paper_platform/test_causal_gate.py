"""The preflight exercises the selected purchased archive, without publishing a session."""

from datetime import date
import os
from pathlib import Path
import time

import pytest

from . import causal_gate
from .feed import HistoricalFeed
from .rulebook import catalogue
from .session import LIMIT_SECONDS


@pytest.fixture(scope="module")
def purchased_feed():
    folder = Path(os.environ["MARKETS_DATA"]) / "data/nymex_mbo/ng_v0_oct2023"
    return HistoricalFeed(folder, date(2023, 10, 2))


def test_gate_compares_real_decisions_on_a_strict_prefix(purchased_feed):
    result = causal_gate.check_prefix(purchased_feed, catalogue(), time.monotonic() + LIMIT_SECONDS)
    cut = result["cutoff"]
    assert result["status"] == "passed" and result["decisions"] >= result["opens"] > 0
    assert result["trade_day_records"] >= causal_gate.SAMPLE_RECORDS
    short = causal_gate._through_cutoff(purchased_feed, cut)
    assert sum(len(f.records) for f in short.week) == cut["cursor"] + 1
    assert short.week[-1].records.base is None
    assert sum(len(f.records) for f in purchased_feed.week) > cut["cursor"] + 1
    assert short.week[-1].sha256 == cut["source_sha256"]
    assert int(short.week[-1].records[-1]["ts_recv"]) == cut["tape_receive_ns"]


def test_gate_rejects_a_missing_real_decision(purchased_feed, monkeypatch):
    truncate = causal_gate._through_cutoff

    class OmitOpening(causal_gate.Engine):
        def tick(self, message):
            return [event for event in super().tick(message) if event["action"] != "open"]

    def lose_openings_on_short_input(feed, cut):
        monkeypatch.setattr(causal_gate, "Engine", OmitOpening)
        return truncate(feed, cut)

    monkeypatch.setattr(causal_gate, "_through_cutoff", lose_openings_on_short_input)
    with pytest.raises(ValueError, match="causal preflight failed"):
        causal_gate.check_prefix(purchased_feed, catalogue(), time.monotonic() + LIMIT_SECONDS)


def test_gate_honors_existing_wall_deadline(purchased_feed):
    with pytest.raises(TimeoutError, match="5.75-hour budget"):
        causal_gate.check_prefix(purchased_feed, catalogue(), time.monotonic())
