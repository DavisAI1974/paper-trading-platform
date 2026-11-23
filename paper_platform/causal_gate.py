"""Compare decisions at a source-record cutoff using two independently seated books."""

from copy import copy
from dataclasses import replace
import hashlib
import json
import time

from root.calendar import trade_day_bounds

from .engine import Engine


# Verification sample size, measured in records of the selected trade day.
# A quiet sample is extended through its first opening or the end of the trade day.
SAMPLE_RECORDS = 4096


class PreflightDeadline(TimeoutError):
    pass


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise PreflightDeadline("5.75-hour budget reached during the causal preflight")


def _through_cutoff(feed, cutoff):
    short = copy(feed)
    short.week = []
    short.sources = []
    for file, source in zip(feed.week, feed.sources, strict=True):
        if file.sha256 == cutoff["source_sha256"]:
            count = cutoff["record_index"] + 1
            # A slice alone retains the future rows in its NumPy backing array.
            short.week.append(replace(file, records=file.records[:count].copy()))
            short.sources.append(dict(source, records=count))
            return short
        short.week.append(file)
        short.sources.append(source)
    raise ValueError("causal check cutoff is absent from the purchased archive")


def _trace(feed, rules, deadline, *, choose_cutoff):
    engine = Engine(feed.trade_day, "causal-preflight", rules)
    trace = hashlib.sha256()
    total = sum(len(file.records) for file in feed.week)
    halt = trade_day_bounds(feed.trade_day)[1]
    count = decisions = opens = 0
    last = None
    for message in feed.messages():
        _check_deadline(deadline)
        cursor = message["cursor"]
        if choose_cutoff and (
            cursor == total - 1
            or (message["period"] != "vendor snapshot" and message["raw"]["ts_event"] >= halt)
        ):
            break  # The full input must extend strictly past the selected source record.
        batch = engine.tick(message)
        if batch:
            # Freeze each decision in the order it would reach the journal, before any later tick.
            trace.update(json.dumps([cursor, batch], sort_keys=True, separators=(",", ":")).encode())
            trace.update(b"\n")
            decisions += len(batch)
            opens += sum(event["action"] == "open" for event in batch)
        if message["in_session"]:
            count += 1
        last = message
        if choose_cutoff and count >= SAMPLE_RECORDS and opens:
            break
    if not count:
        raise ValueError("causal check requires a trade-day record with a real suffix")
    return {
        "cutoff": {
            "cursor": last["cursor"],
            "source_sha256": last["source_sha256"],
            "record_index": last["record_index"],
            "tape_receive_ns": last["raw"]["ts_recv"],
        },
        "trade_day_records": count,
        "decisions": decisions,
        "opens": opens,
        "decisions_sha256": trace.hexdigest(),
    }


def check_prefix(feed, rules, deadline):
    """On the full source advance through the cutoff, then replay physically truncated arrays to EOF."""
    _check_deadline(deadline)
    expected = _trace(feed, rules, deadline, choose_cutoff=True)
    short = _through_cutoff(feed, expected["cutoff"])
    actual = _trace(short, rules, deadline, choose_cutoff=False)
    _check_deadline(deadline)
    if actual != expected:
        raise ValueError(f"causal preflight failed at source cursor {expected['cutoff']['cursor']}")
    return {"status": "passed", **actual}
