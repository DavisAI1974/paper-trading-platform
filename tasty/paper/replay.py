"""Replay a recording through lines, quotes, legs and the rules. Sends nothing anywhere.

Every print is one step. Rules that read every print (`reads_no_side`: CL16, CL17, Codex's fifteen) are given it.
The fifteen (CL01 to CL15) are given sided prints only; what a print with no side showed reaches them at the next
sided print, marked as shown earlier. A GAP (Greg, 2026-11-24: "Only a real break") is a print whose sequence number
steps by anything but 1 or 2 from the print before; the day volume ahead of the prints is a number on the receipt.

    python3 -m tasty.paper.replay --recording FILE --out DIR [--cut K]

DIR must not be inside this code repository. Writes ledger.jsonl (one line per paper trade) and receipt.json.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from types import MappingProxyType

from tasty.paper import clock, feed_tasty
from tasty.paper.events import DAYVOLUME, PRINT, QUOTE
from tasty.paper.ledger import NOT_SCORED, STAND_INS, Ledger
from tasty.paper.lines import LiveLines
from tasty.paper.quotes_live import LiveLegs, LiveQuotes
from tasty.paper.rule_base import CLOSE, OPEN, Snapshot
from tasty.paper.rules_cl import all_rules

CODE_REPO = Path(__file__).resolve().parents[2]


class Engine:
    def __init__(self, rules=None):
        self.lines, self.quotes, self.legs, self.ledger = LiveLines(), LiveQuotes(), LiveLegs(), Ledger()
        self.lines_every_print = LiveLines(every_print=True)     # for the rules that read every print
        self.rules = all_rules() if rules is None else rules
        self.n_prints = self.n_unsided = self.n_quote_messages = 0
        self.prints = []
        self.pending_runs, self.pending_legs = [], []
        self.printed_size = 0.0
        self.dv0 = None                 # (day volume, printed size) at the first Trade message with a day volume
        self.unexplained_last, self.gaps, self.day_volume_messages = 0.0, 0, 0
        self.gap_places = []
        self.in_settlement = False
        self.prints_in_settlement = 0
        self.snapshots_seen = 0

    def on_event(self, ev):
        if isinstance(ev, PRINT):
            self._print(ev)
        elif isinstance(ev, QUOTE):
            self.n_quote_messages += 1
            self.quotes.on_quote(ev)
        elif isinstance(ev, DAYVOLUME):
            self._day_volume(ev)

    def _day_volume(self, ev):
        self.day_volume_messages += 1
        if ev.day_volume is None:
            return
        if self.dv0 is None:
            self.dv0 = (ev.day_volume, self.printed_size)
        self.unexplained_last = (ev.day_volume - self.dv0[0]) - (self.printed_size - self.dv0[1])

    def _print(self, p: PRINT):
        idx = self.n_prints
        self.n_prints += 1
        if self.prints and p.sequence - self.prints[-1].sequence not in (1, 2):
            self.gaps += 1                              # a real break: the sequence did not step by 1 or 2
            self.gap_places.append(list(p.place))
        self.prints.append(p)
        if p.size is not None:
            self.printed_size += p.size
        settle = clock.in_settlement_window(p.time_ns)
        if settle:
            self.prints_in_settlement += 1
            if not self.in_settlement:
                self.ledger.settlement_window_began()   # rule 3: a trade open when it begins is held and marked
        self.in_settlement = settle
        run_facts, quote_missing = self.quotes.on_print(p, idx)
        leg_facts = self.legs.on_print(p, idx)
        for f in run_facts + leg_facts:
            f['here'] = True
        every = self.lines_every_print.on_print(p, idx)
        state = self.lines.on_print(p, idx)             # None at a print with no side
        if state is None:
            self.n_unsided += 1
        at = lambda i: self.quotes.at_print[i]
        snap_every = Snapshot(p, idx, every.sided, every, tuple(run_facts), tuple(leg_facts), quote_missing, at,
                              settle)
        snap_sided = None
        if state is not None:
            snap_sided = Snapshot(p, idx, True, state, tuple(self.pending_runs + run_facts),
                                  tuple(self.pending_legs + leg_facts), quote_missing, at, settle)
            self.pending_runs, self.pending_legs = [], []
        else:                                           # kept for the fifteen: shown earlier, not here
            self.pending_runs += [dict(f, here=False) for f in run_facts]
            self.pending_legs += [dict(f, here=False) for f in leg_facts]
        for rule in self.rules:
            snap = snap_every if rule.reads_no_side else snap_sided
            if snap is None:
                continue
            own = dataclasses.replace(snap, own_open=MappingProxyType({k: dict(v) for k, v in rule.trades.items()}))
            for sig in rule.step(own):
                if isinstance(sig, OPEN):
                    self.ledger.open(sig, p, self.gaps)
                elif isinstance(sig, CLOSE):
                    shown = None if sig.shown_idx is None else self.prints[sig.shown_idx]
                    self.ledger.close(sig, p, shown, self.gaps)
        self.snapshots_seen += 1

    def counts(self) -> dict:
        return dict(prints=self.n_prints, prints_with_no_side=self.n_unsided, quote_messages=self.n_quote_messages,
                    day_volume_messages=self.day_volume_messages, gaps=self.gaps, gap_places=self.gap_places,
                    day_volume_ahead_of_the_prints_at_end=self.unexplained_last, print_size_seen=self.printed_size,
                    prints_inside_the_settlement_window=self.prints_in_settlement,
                    prints_missing_bid_or_offer=self.quotes.n_print_missing,
                    quote_messages_missing_bid_or_offer=self.quotes.n_quote_missing,
                    prints_with_no_size=self.lines.n_no_size, prints_with_no_price=self.legs.n_price_missing,
                    rules={r.code: dict(r.counts, missing=dict(r.missing_counts), title=r.title) for r in self.rules})


def run(path, cut=None) -> Engine:
    eng = Engine()
    for ev in feed_tasty.events_from_recording(path, cut):
        eng.on_event(ev)
    return eng


def _sha(path) -> tuple:
    b = Path(path).read_bytes()
    return len(b), hashlib.sha256(b).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--recording', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cut', type=int, default=None)
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    if out == CODE_REPO or CODE_REPO in out.parents:
        print(f'refused: --out {out} is inside {CODE_REPO}; outputs go to a markets-data checkout', file=sys.stderr)
        return 2
    eng = run(a.recording, a.cut)
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / 'ledger.jsonl'
    ledger_path.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in eng.ledger.records()))
    size, sha = _sha(ledger_path)
    rec_size, rec_sha = _sha(a.recording)
    receipt = dict(
        what='replay of a 7e recording through the paper runner: sends nothing', note=NOT_SCORED,
        a_gap_is='a print whose sequence number steps by anything but 1 or 2 from the print before (Greg, 2026-11-24: '
                 'only a real break); the day volume ahead of the prints is a number here and resets nothing',
        recording=dict(path=str(a.recording), bytes=rec_size, sha256=rec_sha, cut_first_k_messages=a.cut),
        counts=eng.counts(), trades_in_ledger=len(eng.ledger.records()), stand_ins=STAND_INS,
        leakage_gate='the cut-equals-whole check stands in for odcore/leakage.py on a replay; nothing here is a '
                     'backtest result',
        not_testable_on_real_data='the 4:44 pm stop, the halt hour and the settlement window (the recordings cover '
                                  'about 6:00 to 6:26 pm on the feed)',
        manifest_entries=[dict(path='ledger.jsonl', bytes=size, sha256=sha, source=str(a.recording))])
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True, default=str) + '\n')
    c = eng.counts()
    print(f"prints {c['prints']}  prints with no side {c['prints_with_no_side']}  quote messages "
          f"{c['quote_messages']}  gaps {c['gaps']}  (day volume ahead of the prints at the end "
          f"{c['day_volume_ahead_of_the_prints_at_end']}, print sizes seen {c['print_size_seen']})")
    for code, r in c['rules'].items():
        other = {k: v for k, v in r.items() if k not in ('armed', 'opened', 'closed', 'missing', 'title',
                                                             'same_key_skipped') and v}
        print(f"{code}  armed {r['armed']}  opened {r['opened']}  closed {r['closed']}  missing {r['missing']}"
              + (f"  {other}" if other else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
