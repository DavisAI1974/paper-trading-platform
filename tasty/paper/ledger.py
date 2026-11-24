"""One record per paper trade (Part A). No result figure, no mid price, no cost.

Both the events (an open and a close, each written when it happens) and the per-trade records are kept. Bid and
offer at each end stay each on its own.
"""
from __future__ import annotations

from tasty.paper.events import PRINT

NOT_SCORED = 'not scored: tastytrade sandbox feed'
STAND_INS = {
    'S1': 'the clock: the lines are made on the feed trade time in ms (as ns) and sequence',
    'S2': 'the event mark: a rule decides at the feed message (print) that shows its trigger; "a later exchange '
          'event" is read as "a later feed trade time"',
    'S3': 'the quotes: the print\'s own bid and offer only (G1); quote messages are recorded, never read by a rule',
    'S4': 'the trades: the lines and the rules use prints that carry a side; prints with no side are counted',
    'S5': 'a moment at which trades only leave a span is known at the next print; a close that depends on it is '
          'made then and marked known late',
    'S6': 'the end-of-trade-day close cannot be sent; the flatten at the stop takes its place (not in Part A)',
    'S7': 'CL01 to CL15 are not given a print with no side (their rule 5); a step of the quote or a trade leg\'s end '
          'that such a print shows closes their trade at the next print that has a side: the line names the print '
          'that showed it ("closed") and the print acted at ("acted_at") and is marked known late',
    'CL13': 'prints of one side sharing one feed time at more than one price',
    'CL16-CL17': 'made for the print with no side (Greg, 2026-11-24): given every print; rule 5 does not hold for them',
}


def _end(p: PRINT) -> dict:
    return dict(place=list(p.place), feed_time_ns=p.time_ns, sequence=p.sequence, receive_t_ns=p.t_ns,
                bid=p.bid, offer=p.offer, print_side=p.side)


class Ledger:
    def __init__(self):
        self.events = []        # an open or a close, in the order made
        self.trades = {}        # trade id -> record

    def open(self, sig, p: PRINT, gaps: int = 0):
        stands = ['S1', 'S2', 'S3', 'S4'] + (['CL13'] if sig.rule == 'CL13' else []) + \
            (['CL16-CL17'] if sig.rule in ('CL16', 'CL17') else [])
        rec = dict(strategy=sig.rule, trade_id=sig.trade_id, side=sig.side, opened=_end(p),
                   trigger=sig.trigger, closed=None, stand_ins=stands, note=NOT_SCORED,
                   feed_gaps_before_it_opened=gaps, feed_gaps_while_open=None,
                   held_through_the_settlement_window=False)
        self.trades[sig.trade_id] = rec
        self.events.append(dict(event='open', strategy=sig.rule, trade_id=sig.trade_id, side=sig.side,
                                at=_end(p), trigger=sig.trigger))

    def close(self, sig, p: PRINT, shown: PRINT = None, gaps: int = 0):
        """`p`: the print the close is acted at. `shown`: the earlier print that showed what closed it, where there
        is one (S7); the line's "closed" is then that print and "acted_at" is `p`."""
        rec = self.trades[sig.trade_id]
        rec['closed'] = dict(_end(shown or p), reason=sig.reason, verdict=sig.verdict, known_late=sig.known_late,
                             detail=sig.detail)
        if shown is not None:
            rec['closed'].update(acted_at=_end(p), late_because=sig.late_because)
        stand_in = 'S7' if shown is not None else ('S5' if sig.known_late else None)
        if stand_in and stand_in not in rec['stand_ins']:
            rec['stand_ins'].append(stand_in)
        rec['feed_gaps_while_open'] = gaps - rec['feed_gaps_before_it_opened']
        self.events.append(dict(event='close', strategy=sig.rule, trade_id=sig.trade_id, at=_end(p),
                                shown_at=None if shown is None else _end(shown), late_because=sig.late_because,
                                reason=sig.reason, verdict=sig.verdict, known_late=sig.known_late, detail=sig.detail))

    def indicate(self, sig, p: PRINT):
        """A management rule's indication about a position another rule opened: its own event, and a note beside
        the position. It closes nothing here."""
        note = dict(by=sig.rule, kind=sig.kind, at=_end(p), trigger=sig.trigger, known_late=sig.known_late)
        self.trades[sig.trade_id].setdefault('indications', []).append(note)
        self.events.append(dict(event='indication', strategy=sig.rule, trade_id=sig.trade_id, kind=sig.kind,
                                at=_end(p), trigger=sig.trigger, known_late=sig.known_late))

    def settlement_window_began(self):
        for rec in self.trades.values():
            if rec['closed'] is None:
                rec['held_through_the_settlement_window'] = True

    def records(self) -> list:
        return list(self.trades.values())
