"""Claude's rules (VERSIONS.md: CL01 to CL15 v2, CL16 and CL17 v1) on the tastytrade feed. Of the fifteen, nine run
and six cannot (no single orders). CL16 and CL17 (Greg, 2026-11-24) are made for the print with no side and are
given every print; the fifteen are given sided prints only, and a close that a print with no side showed them is
made at the next sided print and names both (rule_base).

Written from the rule text. Stand-ins used, all named in RULES_ON_TASTY_FEED_20261123.md and the plan: S1 the feed's
own clock, S2 "decides at the feed message (print) that shows its trigger" and "in a later exchange event" read as
"at a later feed trade time", S3/G1 the quote from the prints' own bid and offer, S4 sided prints, S5 moments with no
print known at the next print, and CL13's own stand-in (prints of one side sharing one feed time at more than one
price). Rule 2: the same run, hump, level or leg never opens a second trade. Rule 5: a print with no side is never
given to a rule; an EMPTY or MISSING input opens nothing and is counted by name. Rule 6: nothing opens on a moment with
no print; a hump's end still closes CL07 and CL08 at the next print, known_late. Rules 3 and 4 are not in Part A.
Where two things that the rule orders show at one print, both are written, each as its own event (rule_base.both).
"""
from __future__ import annotations

from root.calcs.quote_runs import FURTHER, STARTED, STEP_BACK
from tasty.paper.lines import AT_MOMENT, CUT, EMPTY, MISSING, ON_TRADE, sign_of
from tasty.paper.rule_base import BUY, RIGHT, SELL, WRONG, Rule, Snapshot, both, side_of_run, when

NOT_SHOWN_TO_IT = 'trigger shown by a print with no side: not opened (rule 5)'

BID, ASK = 'best bid', 'best ask'
CUT_RUN = 'first quote run cut at the start'
MOVES = (STARTED, FURTHER)


def _moves(facts):
    return [f for f in facts if f['fact'] in MOVES]


def _read(rule, value, name):
    """The sign of a line, or None (counted) where it is EMPTY or MISSING."""
    s = sign_of(value)
    if s is None or value == EMPTY:
        rule.missing(name)
        return None
    return s


class Unrunnable(Rule):
    """Cannot run on this feed: it never opens, and names the input it lacks."""
    needs = 'single orders'

    def decide(self, snap):
        self.missing(self.needs)
        return []


def _unrunnable(code, title):
    return type(code, (Unrunnable,), dict(code=code, title=title))


CL01 = _unrunnable('CL01', 'the eaten level')
CL02 = _unrunnable('CL02', 'the abandoned level')
CL03 = _unrunnable('CL03', 'the first step made by a fill')
CL04 = _unrunnable('CL04', 'a level that has only been eaten')
CL09 = _unrunnable('CL09', 'the first step made by a cancel')
CL12 = _unrunnable('CL12', 'the reloaded level')


class CL05(Rule):
    code, title = 'CL05', "a trade leg's first step with the exhaustion line on its side"

    def decide(self, snap: Snapshot):
        out = []
        for f in snap.leg_facts:
            for tid, t in list(self.trades.items()):
                if f['leg_number'] != t['leg']:
                    continue
                if f['fact'] == 'new furthest price':
                    t['second'] = True
                elif f['fact'] == 'leg ended':
                    out += self.close(tid, 'the print that ends the leg', RIGHT if t['second'] else WRONG,
                                      fact=f)
        for f in snap.leg_facts:
            if f['fact'] != 'leg started':
                continue
            if not f['here']:
                self.passed(NOT_SHOWN_TO_IT)
                continue
            self.arm()
            if f['cut']:
                self.missing('first trade leg cut at the start')
                continue
            first = snap.quote_at_print(f['leg_first_trade_position'])
            now = (snap.print.bid, snap.print.offer)
            if None in first or None in now:
                self.missing('bid or offer at the leg first trade or now')
                continue
            if not (first[0] != now[0] or first[1] != now[1]):
                continue
            s = _read(self, snap.lines.exh, 'exhaustion line (20 s)')
            if s is None or s != (1 if f['direction'] == 'up' else -1):
                continue
            out += self.open(side_of_run(f['direction']), ('leg', f['leg_number']),
                             dict(leg=f['leg_number'], direction=f['direction'], exhaustion=snap.lines.exh,
                                  bid_at_leg_first_trade=first[0], offer_at_leg_first_trade=first[1]),
                             leg=f['leg_number'], second=False)
        return out


class CL06(Rule):
    code, title = 'CL06', 'both quotes have stepped the same way'

    def __init__(self):
        super().__init__()
        self.U, self.D = {}, {}                     # quote -> cut flag, since the last step the other way

    def decide(self, snap: Snapshot):
        out = []
        groups = {}
        for f in _moves(snap.run_facts):
            groups.setdefault(f['idx'], []).append(f)
        for idx in sorted(groups):
            g = groups[idx]
            ups = {f['quote']: f['cut'] for f in g if f['direction'] == 'up'}
            downs = {f['quote']: f['cut'] for f in g if f['direction'] == 'down'}
            here = all(f['here'] for f in g)
            for tid, t in list(self.trades.items()):         # closes: only prints after the opening one
                if idx <= t['idx']:
                    continue
                same, other = (ups, downs) if t['side'] == BUY else (downs, ups)
                if other:
                    v = RIGHT if t['further'] else (
                        both('a quote stepped further the same way', 'a quote stepped the other way') if same
                        else WRONG)
                    out += self.close(tid, 'the first step the other way by either quote', v, fact=g[0])
                elif same:
                    t['further'] = True
            if ups and downs:
                self.U, self.D = {}, {}
                continue
            if ups:
                self.D, before = {}, len(self.U)
                self.U.update(ups)
                begun, side, state = before < 2 and len(self.U) == 2, BUY, self.U
            else:
                self.U, before = {}, len(self.D)
                self.D.update(downs)
                begun, side, state = before < 2 and len(self.D) == 2, SELL, self.D
            if begun and not here:
                self.passed(NOT_SHOWN_TO_IT)
            if not (begun and here):
                continue
            if snap.print.bid is None or snap.print.offer is None or snap.print.offer <= snap.print.bid:
                continue                                        # not while the offer is at or below the bid
            self.arm()
            if any(state.values()):
                self.missing(CUT_RUN)
                continue
            out += self.open(side, ('both', side, idx), dict(bid=snap.print.bid, offer=snap.print.offer),
                             idx=idx, further=False)
        return out


class _Hump(Rule):
    """What CL07 and CL08 share: the quote a hump hits and the way it steps away."""

    @staticmethod
    def hit(side):
        return (ASK, 'up') if side > 0 else (BID, 'down')


class CL07(_Hump):
    code, title = 'CL07', "fresh trading on the dipole's side"

    def decide(self, snap: Snapshot):
        out = []
        for f in _moves(snap.run_facts):
            for t in self.trades.values():
                if f['quote'] == t['quote'] and f['direction'] == t['away'] and t['away_at'] is None \
                        and f['time_ns'] > t['time_ns'] and f['idx'] > t['idx']:
                    t['away_at'] = when(f)
        for e in snap.lines.hump_ends:
            end = (e['ended_ns'], snap.idx if e['at_print'] else -1)
            for tid, t in list(self.trades.items()):
                if t['key'] == ('hump', e['began_read']):
                    a = t['away_at']
                    v = WRONG if (a is None or a > end) else (
                        both('the quote the hump was hitting stepped away', 'the hump ended') if a == end
                        else RIGHT)
                    out += self.close(tid, 'the hump ended', v, known_late=e['known_late'],
                                      detail=dict(ended_ns=e['ended_ns']))
        h = snap.lines.hump
        if h and h['began_here']:
            if h['began_kind'] == CUT:
                self.missing('hump start cut at the start')
            elif h['began_kind'] == ON_TRADE:
                self.arm()
                s = _read(self, snap.lines.dip_before, 'dipole line (60 s) before the trade')
                if s == h['side']:
                    q, away = self.hit(h['side'])
                    out += self.open(BUY if h['side'] > 0 else SELL, ('hump', h['began_read']),
                                     dict(hump_side=h['side'], dipole_before=snap.lines.dip_before,
                                          exhaustion=snap.lines.exh),
                                     quote=q, away=away, away_at=None, time_ns=snap.print.time_ns, idx=snap.idx)
            else:
                self.counts['began_at_a_moment_not_opened'] = self.counts.get('began_at_a_moment_not_opened', 0) + 1
        return out


STEP_BACK_OF_IT = "the quote's first step back"


class CL08(_Hump):
    code, title = 'CL08', 'fresh trading against the dipole'

    def __init__(self):
        super().__init__()
        self.pending = {}                            # hump (began_read) -> what it waits for

    def decide(self, snap: Snapshot):
        out = []
        # trades under way: the hump's end and the quote's steps, the earliest decides
        for tid, t in list(self.trades.items()):
            ev = []
            for f in _moves(snap.run_facts):
                if f['quote'] != t['quote'] or f['idx'] <= t['idx']:
                    continue
                if f['direction'] == t['away']:
                    if t['second_at'] is None:
                        t['second_at'] = when(f)
                elif t['back'] is None:
                    t['back'] = when(f)
            for e in snap.lines.hump_ends:
                if e['began_read'] == t['key'][1]:
                    ev.append(((e['ended_ns'], snap.idx if e['at_print'] else -1), 'the hump ended',
                               e['known_late'], dict(ended_ns=e['ended_ns'])))
            if t['back'] is not None:
                ev.append((t['back'], STEP_BACK_OF_IT, False, None))
            if ev:
                at, why, late, detail = min(ev, key=lambda x: x[0])
                a = t['second_at']
                here = [x[1] for x in ev if x[0] == at] + (["that quote's second step"] if a == at else [])
                v = both(*here) if len(here) > 1 else (RIGHT if a is not None and a < at else WRONG)
                out += self.close(tid, why, v, known_late=late, detail=detail,
                                  shown_idx=at[1] if why == STEP_BACK_OF_IT else None)
        for e in snap.lines.hump_ends:
            self.pending.pop(e['began_read'], None)
        h = snap.lines.hump
        if h and h['began_here']:
            if h['began_kind'] == CUT:
                self.missing('hump start cut at the start')
            elif h['began_kind'] == ON_TRADE:
                s = _read(self, snap.lines.dip_before, 'dipole line (60 s) before the trade')
                if s == -h['side']:
                    self.arm()
                    q, away = self.hit(h['side'])
                    self.pending[h['began_read']] = dict(side=h['side'], quote=q, away=away, time_ns=snap.print.time_ns,
                                                         idx=snap.idx, dipole_before=snap.lines.dip_before)
            else:
                self.counts['began_at_a_moment_not_opened'] = self.counts.get('began_at_a_moment_not_opened', 0) + 1
        for f in _moves(snap.run_facts):
            for read, p in list(self.pending.items()):
                if f['quote'] == p['quote'] and f['direction'] == p['away'] and f['time_ns'] > p['time_ns'] \
                        and f['idx'] > p['idx']:
                    if not f['here']:
                        self.passed(NOT_SHOWN_TO_IT)
                        continue
                    del self.pending[read]
                    out += self.open(BUY if p['side'] > 0 else SELL, ('hump', read),
                                     dict(hump_side=p['side'], dipole_before=p['dipole_before'], quote=p['quote'],
                                          step_away_run=f['run_start_position']),
                                     quote=p['quote'], away=p['away'], second_at=None, back=None, idx=snap.idx)
        return out


class _SecondStep(Rule):
    """CL10 and CL11 read the same event: a run of either quote takes its second step with the dipole on the other
    side. CL10 trades with the price, CL11 against it."""
    with_price = True

    def decide(self, snap: Snapshot):
        out = []
        for f in snap.run_facts:
            for tid, t in list(self.trades.items()):
                if (f['quote'], f['run_start_position']) != t['run']:
                    continue
                if f['fact'] == FURTHER and f['moves'] == 3:
                    if self.with_price:
                        t['third'] = True
                    else:
                        out += self.close(tid, "the run's third step", WRONG, fact=f)
                elif f['fact'] == STEP_BACK:
                    right = t['third'] if self.with_price else True
                    out += self.close(tid, "the run's first step back", RIGHT if right else WRONG, fact=f)
        for f in snap.run_facts:
            if not (f['fact'] == FURTHER and f['moves'] == 2):
                continue
            if not f['here']:
                self.passed(NOT_SHOWN_TO_IT)
                continue
            self.arm()
            if f['cut']:
                self.missing(CUT_RUN)
                continue
            s = _read(self, snap.lines.dip, 'dipole line (60 s)')
            if s is None or s != (-1 if f['direction'] == 'up' else 1):
                continue
            run_side = side_of_run(f['direction'])
            side = run_side if self.with_price else (SELL if run_side == BUY else BUY)
            out += self.open(side, ('run', f['quote'], f['run_start_position']),
                             dict(quote=f['quote'], run_start_position=f['run_start_position'],
                                  direction=f['direction'], dipole=snap.lines.dip),
                             run=(f['quote'], f['run_start_position']), third=False)
        return out


class CL10(_SecondStep):
    code, title, with_price = 'CL10', 'the price runs against the dipole: with the price', True


class CL11(_SecondStep):
    code, title, with_price = 'CL11', 'the price runs against the dipole: against the price', False


class CL13(Rule):
    code, title = 'CL13', 'paying up through more than one level'

    def __init__(self):
        super().__init__()
        self.time, self.seen = None, {}

    def decide(self, snap: Snapshot):
        out = []
        for tid, t in list(self.trades.items()):
            for f in _moves(snap.run_facts):
                if f['quote'] != t['quote'] or f['idx'] <= t['idx']:
                    continue
                if f['direction'] == t['away']:
                    if f['time_ns'] > t['time_ns']:
                        t['further'] = True
                else:
                    out += self.close(tid, "the swept quote's first step back", RIGHT if t['further'] else WRONG,
                                      fact=f)
                    break
        p = snap.print
        if p.time_ns != self.time:
            self.time, self.seen = p.time_ns, {}
        if p.price is None:
            self.missing('print price')
            return out
        prices = self.seen.setdefault(p.side, set())
        before = len(prices)
        prices.add(p.price)
        if before == 1 and len(prices) == 2:
            self.arm()
            q, away = (ASK, 'up') if p.side == 'B' else (BID, 'down')
            out += self.open(BUY if p.side == 'B' else SELL, ('sweep', p.time_ns, p.side),
                             dict(feed_time_ns=p.time_ns, prices=sorted(prices)),
                             quote=q, away=away, further=False, time_ns=p.time_ns, idx=snap.idx)
        return out


class CL14(Rule):
    code, title = 'CL14', 'the other side arrives at the top step of a run'

    def __init__(self):
        super().__init__()
        self.st = {BID: None, ASK: None}

    def _apply(self, f):
        q = f['quote']
        if f['fact'] == STEP_BACK:
            self.st[q] = None
        elif f['fact'] in MOVES:
            self.st[q] = dict(dir=f['direction'], level=f['price_now'], run=f['run_start_position'], moves=f['moves'],
                              seen=False, cut=f['cut'])

    def decide(self, snap: Snapshot):
        out = []
        for f in snap.run_facts:
            for tid, t in list(self.trades.items()):
                if (f['quote'], f['run_start_position']) == t['run']:
                    if f['fact'] == STEP_BACK:
                        out += self.close(tid, "the run's first step back", RIGHT, fact=f)
                    elif f['fact'] == FURTHER:
                        out += self.close(tid, "the run's next step the same way", WRONG, fact=f)
        # The quote a print carries stood BEFORE its trade (on both 7e recordings every sell but one printed at its
        # own bid and every buy at its own ask), so a step this print's own quote shows came before this print's
        # trade: this print can be the first sell (or buy) since that step. Step 0 of 2026-11-24 found the earlier
        # code passed that print over and opened on the SECOND one.
        for f in snap.run_facts:
            self._apply(f)
        p = snap.print
        for q, want_dir, want_side, trade_side, own in ((BID, 'up', 'S', SELL, p.bid), (ASK, 'down', 'B', BUY, p.offer)):
            st = self.st[q]
            if p.side != want_side or st is None or st['dir'] != want_dir or st['seen']:
                continue
            st['seen'] = True                         # the first such print since the latest step
            if p.price != st['level']:
                continue
            self.arm()
            if st['cut']:
                self.missing(CUT_RUN)
                continue
            if own is None:
                self.missing('print bid or offer')
                continue
            if own != st['level']:
                continue                              # the level no longer stands
            out += self.open(trade_side, ('level', q, st['run'], st['moves']),
                             dict(quote=q, level=st['level'], run_start_position=st['run']),
                             run=(q, st['run']))
        return out


class CL15(Rule):
    code, title = 'CL15', 'the step back and the return'

    def __init__(self):
        super().__init__()
        self.ended = {BID: None, ASK: None}

    def decide(self, snap: Snapshot):
        out = []
        for f in snap.run_facts:
            for tid, t in list(self.trades.items()):
                if (f['quote'], f['run_start_position']) != t['run']:
                    continue
                if f['fact'] == FURTHER and f['moves'] == 2:
                    t['second'] = True
                elif f['fact'] == STEP_BACK:
                    out += self.close(tid, "the new run's first step back", RIGHT if t['second'] else WRONG,
                                      fact=f)
        for f in snap.run_facts:
            if f['fact'] != STEP_BACK:
                continue
            a, q = self.ended[f['quote']], f['quote']
            if f['moves'] == 1 and a is not None and a['moves'] >= 2 and not f['here']:
                self.passed(NOT_SHOWN_TO_IT)
            if f['here'] and f['moves'] == 1 and a is not None and a['moves'] >= 2:
                self.arm()
                if a['cut']:
                    self.missing(CUT_RUN)
                else:
                    out += self.open(side_of_run(a['direction']), ('return', q, f['idx']),
                                     dict(quote=q, first_run_moves=a['moves'], first_run_direction=a['direction']),
                                     run=(q, f['idx']), second=False)
            self.ended[q] = dict(direction=f['direction'], moves=f['moves'], cut=f['cut'])
        return out


class CL16(Rule):
    """Given every print. Its trigger is a print with NO side whose own quote shows a step of either quote."""
    code, title, reads_no_side = 'CL16', 'a step of the quote first shown by a print with no side', True

    def decide(self, snap: Snapshot):
        out = []
        for f in snap.run_facts:
            for tid, t in list(self.trades.items()):
                if (f['quote'], f['run_start_position']) != t['run']:
                    continue
                if f['fact'] == FURTHER:
                    t['further'] = True
                elif f['fact'] == STEP_BACK:
                    out += self.close(tid, "that quote's first step back", RIGHT if t['further'] else WRONG, fact=f)
        if snap.print.side is not None:
            return out
        for f in _moves(snap.run_facts):
            self.arm()
            if f['cut']:
                self.missing(CUT_RUN)
                continue
            out += self.open(side_of_run(f['direction']), ('run', f['quote'], f['run_start_position']),
                             dict(quote=f['quote'], direction=f['direction'], price_before=f['price_before_tick'],
                                  price_now=f['price_now'], run_start_position=f['run_start_position'],
                                  run_moves=f['moves']),
                             run=(f['quote'], f['run_start_position']), further=False)
        return out


class CL17(Rule):
    """Given every print. Its trigger is a print with NO side that trades at its own best offer (or best bid), the
    first such print there since that quote last stepped."""
    code, title, reads_no_side = 'CL17', 'the print with no side, taken at the quote it traded at', True

    def __init__(self):
        super().__init__()
        self.steps = {BID: 0, ASK: 0}               # how many steps each quote has made
        self.taken = {BID: None, ASK: None}         # the step count at which a trade was last opened there

    def decide(self, snap: Snapshot):
        out = []
        for f in _moves(snap.run_facts):
            self.steps[f['quote']] += 1
            for tid, t in list(self.trades.items()):
                if f['quote'] != t['quote']:
                    continue
                if f['direction'] == t['away']:
                    t['further'] = True
                else:
                    out += self.close(tid, 'the first step back of the quote it traded at',
                                      RIGHT if t['further'] else WRONG, fact=f)
        p = snap.print
        if p.side is not None:
            return out
        if p.price is None or p.bid is None or p.offer is None:
            self.missing('print price, bid or offer')
            return out
        at_offer, at_bid = p.price == p.offer, p.price == p.bid
        if at_offer and at_bid:
            self.missing('the print is at both its bid and its offer')
            return out
        if not (at_offer or at_bid):
            self.passed('the print with no side is at neither its bid nor its offer')
            return out
        q, away, side = (ASK, 'up', BUY) if at_offer else (BID, 'down', SELL)
        if self.taken[q] == self.steps[q]:
            return out                                # not the first one there since that quote last stepped
        self.arm()
        opened = self.open(side, ('there', q, self.steps[q]), dict(quote=q, level=p.price, print_price=p.price),
                           quote=q, away=away, further=False)
        if opened:
            self.taken[q] = self.steps[q]
        return out + opened


RULES = [CL01, CL02, CL03, CL04, CL05, CL06, CL07, CL08, CL09, CL10, CL11, CL12, CL13, CL14, CL15, CL16, CL17]


def all_rules() -> list:
    return [r() for r in RULES]
