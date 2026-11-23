"""Replay the purchased Sunday file inside the Oct 2 trade day."""

import os
from datetime import date
from itertools import islice
from pathlib import Path

import pytest

from .engine import Engine
from .feed import HistoricalFeed


@pytest.fixture(scope="module")
def sunday():
    source = Path(os.environ["MARKETS_DATA"]) / "data/nymex_mbo/ng_v0_oct2023"
    feed = HistoricalFeed(source, date(2023, 10, 2))
    first = feed.week[0]
    engine = Engine(feed.trade_day, "cx-contract")
    events = [e for message in islice(feed.messages(), len(first.records)) for e in engine.tick(message)]
    return first, events, engine


def test_indeterminate_assessments_never_issue_a_strategy_close_or_reopen(sunday):
    _, events, engine = sunday
    interrupted = {
        e["trade_id"]: e
        for e in events
        if e["action"] == "not scored" and e["trade_id"] and not e.get("management_only")
    }
    assert interrupted
    unscored = {(e["trade_id"], e["reference"]) for e in events if e["action"] == "not scored"}
    observed = {(e["trade_id"], e["reference"]) for e in events if e["action"] == "print observed"}
    assert unscored.isdisjoint(observed)
    for key, event in interrupted.items():
        assert event["reason"].startswith("not scored: the feed lacks ")
        assert event["trade_data"] is False and key in engine.unmonitored
        assert not any(
            later["position"] is not None
            and later["position"] >= event["position"]
            and later["rule"] == event["rule"]
            and later["action"] in ("open", "close")
            for later in events
        )
    snapshot = engine.begin_decision_batch()
    closed = engine.stop("verification ended")
    administrative = {e["trade_id"] for e in closed if e["action"] == "close" and not e["trade_data"]}
    assert administrative >= interrupted.keys()
    engine.rollback_decision_batch(snapshot)


def test_exit_warning_observes_each_equal_print_then_first_distinct(sunday):
    first, events, _ = sunday
    exit_signal = next(e for e in events if e["rule"] == "CX06" and e["action"] == "exit")
    q = exit_signal["reference"][1]
    q_price = int(first.records[q]["price"])
    expected = []
    for i in range(q + 1, len(first.records)):
        row = first.records[i]
        if row["action"] == b"T":
            expected.append(i)
            if row["price"] != q_price:
                break
    observed = [
        e for e in events if e["action"] == "print observed" and e["trade_id"] == exit_signal["trade_id"]
    ]
    assert len(expected) > 1
    assert [e["reference"][1] for e in observed] == expected
    assert all(e["price_response"] == "equal" for e in observed[:-1])
    assert observed[-1]["price_response"] == ("with" if observed[-1]["print_price"] > q_price else "against")
    for event in observed:
        assert event["q_reference"] == exit_signal["reference"] and event["reference_price"] == q_price
        assert event["print_price"] == int(first.records[event["reference"][1]]["price"])
        assert event["bid"] is not None and event["ask"] is not None


def test_quote_follow_is_recorded_once_at_the_observed_quote(sunday):
    _, events, _ = sunday
    opens = {e["trade_id"]: e for e in events if e["action"] == "open"}
    followed = [e for e in events if e["action"] == "quote followed"]
    assert {e["rule"] for e in followed} == {"CX07", "CX08"}
    assert len({e["trade_id"] for e in followed}) == len(followed)
    for event in followed:
        entry = opens[event["trade_id"]]
        assert event["q_reference"] == entry["reference"] and event["position"] > entry["position"]
        if event["rule"] == "CX07":
            assert event["bid"] > entry["bid"] == event["decision_quote"]
        else:
            assert event["ask"] < entry["ask"] == event["decision_quote"]
