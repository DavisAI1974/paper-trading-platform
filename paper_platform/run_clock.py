"""The monotonic run clock shared by processes on one Actions runner."""

import os
import time


LIMIT_SECONDS = 5.75 * 3600
CLOCK_STOP = "flattened at the 5.75-hour stop"


def run_start_ns():
    now = time.monotonic_ns()
    start = int(os.environ.get("PAPER_RUN_STARTED_NS", now))
    if not 0 <= start <= now:
        raise ValueError("run start must be on this runner's monotonic clock")
    return start
