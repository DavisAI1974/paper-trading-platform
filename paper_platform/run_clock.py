"""The monotonic run clock shared by processes on one Actions runner."""

import datetime as dt
import os
import time

from root.calendar import EASTERN, HALT_EASTERN, STRETCH, eastern, label, to_ns


LIMIT_SECONDS = 5.75 * 3600
CLOCK_STOP = "flattened at the 5.75-hour stop"
# Greg, 2026-11-24: "If 5.75 is going to land inside of the hour, flatten before 5 and end at 5".
HALT_STOP = "flattened before 5 pm Eastern: the 5.75-hour stop would have landed in the 5 pm to 6 pm hour"
CLOCK_STOPS = (CLOCK_STOP, HALT_STOP)
# How long before 5 pm Eastern the flattening orders are sent. His words give no figure; this one
# is Ccode's, and his to change.
FLATTEN_BEFORE_HALT_SECONDS = 60


def clock_stop(started, *, wall_ns=None, monotonic=None):
    """The seconds from a live run's start to its clock stop, and the words of that stop.

    5.75 hours. When that would land inside the 5 pm to 6 pm Eastern hour, which belongs to no trade
    day and has no trades, the run stops FLATTEN_BEFORE_HALT_SECONDS before 5 pm Eastern instead, so
    every position is flattened before 5 and the run is over at 5. `started` is on the monotonic
    clock, in seconds. This is for a run that follows the live feed: only there do the wall clock and
    the tape keep the same time.
    """
    wall_ns = time.time_ns() if wall_ns is None else wall_ns
    monotonic = time.monotonic() if monotonic is None else monotonic
    landing_ns = wall_ns + round((started + LIMIT_SECONDS - monotonic) * 1_000_000_000)
    try:
        name, _ = label(landing_ns)
    except ValueError:  # a weekend moment that belongs to no trade day
        return LIMIT_SECONDS, CLOCK_STOP
    if name != STRETCH:
        return LIMIT_SECONDS, CLOCK_STOP
    landing = dt.datetime.fromtimestamp(landing_ns // 1_000_000_000, EASTERN)
    halt_ns = to_ns(eastern(landing.date(), HALT_EASTERN))
    return LIMIT_SECONDS - (landing_ns - halt_ns) / 1_000_000_000 - FLATTEN_BEFORE_HALT_SECONDS, HALT_STOP


def run_start_ns():
    now = time.monotonic_ns()
    start = int(os.environ.get("PAPER_RUN_STARTED_NS", now))
    if not 0 <= start <= now:
        raise ValueError("run start must be on this runner's monotonic clock")
    return start
