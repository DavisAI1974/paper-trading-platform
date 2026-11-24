"""The rule interface (Part A). No I/O, no clock, no tastytrade knowledge.

A rule is given a read-only Snapshot and returns a list of signals: OPEN or CLOSE.

TWO KINDS OF RULE (Greg, 2026-11-24: the print with no side gets strategies of its own, CL16 and CL17; the fifteen
stay as written).
- `reads_no_side = False` (CL01 to CL15): the rule is given SIDED prints only; a print with no side is never given
  to it (its rule 5). Facts of the runs and legs that such a print showed come with the next snapshot marked
  here=False, each naming the print that showed it: they may close a trade, LATE, and never open one. A close made
  from such a fact names that print (`Rule.close(..., fact=f)`), so the ledger line carries both prints.
- `reads_no_side = True` (CL16, CL17, Codex's fifteen): the rule is given EVERY print; `snap.sided` says whether
  this one has a side. Every fact it is given was made at this print (here=True).
S5 moments come in `lines.late` and hump ends known late in `lines.hump_ends` (known_late=True); a rule opens
nothing on them (rule 6). Rule 3: nothing opens on a print inside the settlement window (`snap.in_settlement`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Optional

from tasty.paper.events import PRINT
from tasty.paper.lines import EMPTY, MISSING, LineState

BUY, SELL = 'BUY', 'SELL'
RIGHT, WRONG, NEITHER = 'right', 'wrong', 'neither'


def both(*events) -> str:
    """Two things a rule orders showed at one print (Greg, 2026-11-24: "Can't we make both decisions at the same
    time? They're different events right?"). Each is written as its own event at that print; no "first" is
    claimed, so the line carries this in the place of right or wrong."""
    return 'both at one print: ' + '; '.join(events)


NO_SIDE_LATE = 'shown by a print with no side: acted on at the next print that has a side'
SETTLEMENT = 'inside the settlement window: not opened (rule 3)'


@dataclass(frozen=True)
class Snapshot:
    print: PRINT
    idx: int
    sided: bool                    # this print has a side and a size
    lines: LineState
    run_facts: tuple               # dicts; here=True for those made at this print
    leg_facts: tuple
    quote_missing: bool            # this print had no bid or no offer
    quote_at_print: Callable       # idx -> (bid, offer) of an earlier print
    in_settlement: bool = False    # this print's feed time is inside the settlement window (rule 3)
    own_open: MappingProxyType = MappingProxyType({})


@dataclass(frozen=True)
class OPEN:
    rule: str
    trade_id: str
    side: str
    trigger: dict


@dataclass(frozen=True)
class CLOSE:
    rule: str
    trade_id: str
    reason: str
    verdict: Optional[str] = None  # right / wrong / neither, only where the rule says so
    known_late: bool = False
    detail: Optional[dict] = None
    shown_idx: Optional[int] = None    # the print that showed the closing fact, where it is not the print acted at
    late_because: Optional[str] = None


class Rule:
    code = '??'
    title = ''
    runs = True
    reads_no_side = False          # True: the rule is given every print (see the module text)

    def __init__(self):
        self.counts = dict(armed=0, opened=0, closed=0, same_key_skipped=0)
        self.missing_counts = {}
        self.trades = {}           # trade id -> its state
        self.keys = set()          # rule 2: what a trade was opened on
        self._n = 0
        self._idx, self._in_settlement = None, False

    # counting
    def arm(self):
        self.counts['armed'] += 1

    def missing(self, name):
        self.missing_counts[name] = self.missing_counts.get(name, 0) + 1

    def passed(self, why):
        """A trigger that was shown and not acted on, counted by its reason."""
        self.counts[why] = self.counts.get(why, 0) + 1

    def open(self, side, key, trigger, **state):
        if self._in_settlement:
            self.passed(SETTLEMENT)
            return []
        if key in self.keys:
            self.counts['same_key_skipped'] += 1
            return []
        self.keys.add(key)
        self._n += 1
        tid = f'{self.code}-{self._n:04d}'
        self.trades[tid] = dict(state, side=side, key=key)
        self.counts['opened'] += 1
        return [OPEN(self.code, tid, side, dict(trigger))]

    def close(self, tid, reason, verdict=None, known_late=False, detail=None, fact=None, shown_idx=None):
        """`fact` (or `shown_idx`): what closed it and the print that showed it. Where that is an earlier print (one
        with no side, for a rule that is not given such prints) the close is late and names it."""
        self.trades.pop(tid)
        self.counts['closed'] += 1
        if fact is not None:
            shown_idx = fact['idx']
        why = None
        if shown_idx is not None and shown_idx != self._idx:
            known_late, why = True, NO_SIDE_LATE
            self.counts['closed late: shown by a print with no side'] = \
                self.counts.get('closed late: shown by a print with no side', 0) + 1
        else:
            shown_idx = None
        return [CLOSE(self.code, tid, reason, verdict, known_late, detail, shown_idx, why)]

    def step(self, snap: Snapshot) -> list:
        self._idx, self._in_settlement = snap.idx, snap.in_settlement
        return self.decide(snap)

    def decide(self, snap: Snapshot) -> list:
        raise NotImplementedError


def side_of_run(direction: str) -> str:
    return BUY if direction == 'up' else SELL


def when(f: dict) -> tuple:
    """Order of two moments: feed time, then print index (a late moment has idx -1)."""
    return (f['time_ns'], f['idx'])
