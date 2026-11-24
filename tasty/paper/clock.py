"""The exchange's clock for the paper runner: the settlement window on the feed's own time. No I/O.

The window is ROOT's own (`root/calendar.py`: SETTLE_FROM_EASTERN, SETTLE_TO_EASTERN), bounded as ROOT's rows mark
it (`root/rows.py`: from 2:28:00.000 pm Eastern, up to but not including 2:30:00.000 pm). Nothing is typed again here.
NOT YET PROVEN ON REAL DATA: no recording crosses 2:28 pm Eastern.
"""
from __future__ import annotations

import datetime as dt

from root.calendar import EASTERN, SETTLE_FROM_EASTERN, SETTLE_TO_EASTERN

NS = 10 ** 9


def eastern_time(time_ns: int) -> dt.time:
    """The Eastern wall-clock time of a feed time in nanoseconds (whole microseconds are enough for a bound)."""
    moment = dt.datetime.fromtimestamp(time_ns // NS, dt.timezone.utc).replace(microsecond=(time_ns % NS) // 1000)
    return moment.astimezone(EASTERN).time()


def in_settlement_window(time_ns: int) -> bool:
    return SETTLE_FROM_EASTERN <= eastern_time(time_ns) < SETTLE_TO_EASTERN
