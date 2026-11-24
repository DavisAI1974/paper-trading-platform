"""The playback speed of a stored tape that is fed like live.

Speed 2 delivers two seconds of tape in one second on the runner; speed 0.5 takes two seconds over one.
Every strategy timer and every decision is read on the tape's own nanosecond times, so the speed moves
when a record is delivered and never what is decided. A live-following run has no speed: its feed is the clock.
"""

import time

# The steps of the bar on the phone page. Claude's choice of steps, Greg's to change.
PLAYBACK_SPEEDS = (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64)


def speed_label(speed):
    return f"{speed:g}x"


class Pace:
    """Where the tape clock stands against the runner's clock, at a speed that can be moved during the run."""

    def __init__(self, speed=1):
        if speed not in PLAYBACK_SPEEDS:
            raise ValueError("playback speed must be one of " + ", ".join(map(speed_label, PLAYBACK_SPEEDS)))
        self.speed = speed
        self.requested = None  # written by the thread that asks the phone page; read between records
        self.clock_ns = self.tape_ns = None

    def remaining_ns(self, target_ns):
        """Runner nanoseconds still to wait before the tape time `target_ns` is due."""
        if self.clock_ns is None:
            self.clock_ns, self.tape_ns = time.monotonic_ns(), target_ns
        # Differences only: at speed 1 this is the runner's wait as it was before there was a speed.
        return self.clock_ns + round((target_ns - self.tape_ns) / self.speed) - time.monotonic_ns()

    def take_request(self, limit_ns):
        """Move to the requested speed. Returns (old, new, tape time of the change) or None.

        The tape clock is read at the old speed and held at `limit_ns`, the next thing due, so a change
        never skips over a record or a timer. From that point the tape runs at the new speed.
        """
        new = self.requested
        if new is None or new == self.speed or self.clock_ns is None or new not in PLAYBACK_SPEEDS:
            return None
        now = time.monotonic_ns()
        at = min(self.tape_ns + round((now - self.clock_ns) * self.speed), limit_ns)
        old, self.speed = self.speed, new
        self.clock_ns, self.tape_ns = now, at
        return old, new, at
