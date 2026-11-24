"""Codex's fifteen rules CX01 to CX15, from his text (CODEX_VERSIONS.md, lines 1 to 136), on the rule interface.

Every rule reads EVERY print (`reads_no_side`). p is the print before q, as the rule was given them. The quote is the
print's own bid and offer (point G1); no quote message is read. A line value that is EMPTY or MISSING is a missing
input here, never zero. An input a condition needs, missing at p or q, lets the arm expire (counted under `missing`).
A missing monitoring input is counted and nothing is closed on it, nothing is made up.

NOT CARRIED (left out and counted, listed in the builder's report): what an interruption (a gap, a settlement row)
does to an open paper trade, a hold or a warning; the "indeterminate" assessment; the "what would contradict it"
record after q; the later higher bid of CX07; the quote messages ("every tape row" is every print on this feed).
Where an interruption is met only the ARM is reset.
"""
from __future__ import annotations

from tasty.paper.lines import EMPTY, MISSING
from tasty.paper.rule_base import BUY, EXIT, INDICATION, SELL, Rule, both

INTERRUPTED_ARM = 'interrupted (gap or settlement row): the arm was reset'
INTERRUPTED_OPEN = 'interrupted (gap or settlement row): an open trade, hold or warning left as it was, not carried'


def _line(v):
    """-1, 0, +1; None where the line is EMPTY or MISSING (a missing input, never zero)."""
    if v == EMPTY or v == MISSING:
        return None
    return (v > 0) - (v < 0)


def _view(p, exh, dip) -> dict:
    return dict(place=list(p.place), price=p.price, bid=p.bid, offer=p.offer, side=p.side, time=p.time_ns,
                exh=_line(exh), dip=_line(dip), exh_value=exh, dip_value=dip)


def _lt(a, b): return a is not None and b is not None and a < b
def _gt(a, b): return a is not None and b is not None and a > b
def _le(a, b): return a is not None and b is not None and a <= b
def _ge(a, b): return a is not None and b is not None and a >= b
def _eq(a, b): return a is not None and b is not None and a == b


def _first_none(d, names):
    for n in names:
        if d.get(n) is None:
            return n
    return None


def _late_view(m) -> dict:
    """A moment at which trades only left a span: only the two lines are known there."""
    return dict(place=None, price=None, bid=None, offer=None, side=None, time=m['moment_ns'],
                exh=_line(m['exh']), dip=_line(m['dip']), exh_value=m['exh'], dip_value=m['dip'])


class CXEntry(Rule):
    reads_no_side = True
    side = None
    need_p, need_q, watch = (), (), ()

    def __init__(self):
        super().__init__()
        self.armed = None

    def arm_p(self, p) -> bool: raise NotImplementedError
    def at_q(self, p, q): raise NotImplementedError          # BUY, SELL or None
    def breach(self, t, d): raise NotImplementedError        # a reason string or None

    def decide(self, snap):
        d = _view(snap.print, snap.lines.exh, snap.lines.dip)
        out = []
        for tid in list(self.trades):                        # a trade opened at an earlier print
            t = self.trades[tid]
            for n in self.watch:
                if d[n] is None:
                    self.missing(f'monitoring input {n}')
            done = False
            for m in snap.lines.late:
                r = self.breach(t, _late_view(m))
                if r:
                    out += self.close(tid, r, known_late=True, detail=dict(moment_ns=m['moment_ns']))
                    done = True
                    break
            if not done:
                r = self.breach(t, d)
                if r:
                    out += self.close(tid, r)
        interrupted = snap.gap or snap.in_settlement
        if interrupted:
            if self.armed is not None:
                self.passed(INTERRUPTED_ARM)
            if self.trades:
                self.passed(INTERRUPTED_OPEN)
        a, self.armed = self.armed, None
        if a is not None and not interrupted:
            bad = _first_none(d, self.need_q)
            if bad:
                self.missing(f'{bad} at q')
            else:
                side = self.at_q(a, d)
                if side:
                    out += self.open(side, ('q', snap.idx), dict(p=a, q=d), q=d, p=a)
        if not self.trades and not snap.in_settlement:
            bad = _first_none(d, self.need_p)
            if bad:
                self.missing(f'{bad} at p')
            elif self.arm_p(d):
                self.arm()
                self.armed = d
        return out


class CX01(CXEntry):
    code, title, side = 'CX01', 'buying that carries into a new event', BUY
    need_p, need_q, watch = ('price', 'bid', 'exh', 'dip'), ('price', 'bid', 'exh', 'dip'), ('bid', 'exh', 'dip')

    def arm_p(self, p): return p['exh'] == 1 and p['dip'] == 1

    def at_q(self, p, q):
        ok = q['exh'] == 1 and q['dip'] == 1 and q['price'] > p['price'] and q['bid'] > p['bid'] \
            and q['time'] != p['time']
        return BUY if ok else None

    def breach(self, t, d):
        if _lt(d['bid'], t['q']['bid']): return 'the bid fell below the bid at q'
        if _le(d['exh'], 0) or _le(d['dip'], 0): return 'a line is no longer positive'


class CX02(CXEntry):
    code, title, side = 'CX02', 'selling that moves the bid down', SELL
    need_p, need_q, watch = ('price', 'bid', 'exh', 'dip'), ('price', 'bid', 'exh', 'dip', 'side'), ('bid', 'exh', 'dip')

    def arm_p(self, p): return p['exh'] == -1 and p['dip'] == -1

    def at_q(self, p, q):
        ok = q['exh'] == -1 and q['dip'] == -1 and q['side'] == 'S' and q['price'] < p['price'] \
            and q['time'] != p['time'] and q['bid'] < p['bid']
        return SELL if ok else None

    def breach(self, t, d):
        if _gt(d['bid'], t['q']['bid']): return 'the bid rose above the bid at q'
        if _ge(d['exh'], 0) or _ge(d['dip'], 0): return 'a line reached zero or turned positive'


class CX03(CXEntry):
    code, title, side = 'CX03', 'upward re-entry after a pullback', BUY
    need_p, need_q, watch = ('exh', 'dip'), ('exh', 'dip', 'side', 'price', 'offer'), ('offer', 'exh', 'dip')

    def arm_p(self, p): return p['dip'] == 1 and p['exh'] <= 0

    def at_q(self, p, q):
        ok = q['dip'] == 1 and q['exh'] == 1 and q['side'] == 'B' and q['price'] == q['offer']
        return BUY if ok else None

    def breach(self, t, d):
        if _lt(d['offer'], t['q']['offer']): return 'the offer fell below the offer at q'
        if _le(d['exh'], 0) or _le(d['dip'], 0): return 'a line is no longer positive'


class CX04(CXEntry):
    code, title, side = 'CX04', 'downward re-entry after a bounce', SELL
    need_p, need_q, watch = ('exh', 'dip', 'offer'), ('exh', 'dip', 'side', 'price', 'bid', 'offer'), \
        ('offer', 'exh', 'dip')

    def arm_p(self, p): return p['dip'] == -1 and p['exh'] >= 0

    def at_q(self, p, q):
        ok = q['dip'] == -1 and q['exh'] == -1 and q['side'] == 'S' and q['price'] == q['bid'] \
            and q['offer'] <= p['offer']
        return SELL if ok else None

    def breach(self, t, d):
        if _gt(d['offer'], t['q']['offer']): return 'the offer rose above the offer at q'
        if _ge(d['exh'], 0) or _ge(d['dip'], 0): return 'a line reached zero or turned positive'


class CX07(CXEntry):
    code, title, side = 'CX07', 'a buyer to a higher offer', BUY
    need_p, need_q, watch = ('price', 'offer', 'bid'), ('side', 'price', 'offer', 'bid'), ('bid', 'offer')

    def arm_p(self, p): return p['price'] == p['offer']

    def at_q(self, p, q):
        ok = q['side'] == 'B' and q['price'] == q['offer'] and q['offer'] > p['offer'] and q['bid'] == p['bid']
        return BUY if ok else None

    def breach(self, t, d):
        if _lt(d['offer'], t['q']['offer']): return 'the offer fell below the offer at q'
        if _lt(d['bid'], t['q']['bid']): return 'the bid fell below the bid at q'


class CX08(CXEntry):
    code, title, side = 'CX08', 'a seller to a lower bid', SELL
    need_p, need_q, watch = ('price', 'bid', 'offer'), ('side', 'price', 'bid', 'offer'), ('bid', 'offer')

    def arm_p(self, p): return p['price'] == p['bid']

    def at_q(self, p, q):
        ok = q['side'] == 'S' and q['price'] == q['bid'] and q['bid'] < p['bid'] and q['offer'] == p['offer']
        return SELL if ok else None

    def breach(self, t, d):
        if _gt(d['bid'], t['q']['bid']): return 'the bid rose above the bid at q'
        if _gt(d['offer'], t['q']['offer']): return 'the offer rose above the offer at q'


class CX09(CXEntry):
    code, title, side = 'CX09', 'reverse down from a stationary offer', SELL
    need_p, need_q, watch = ('price', 'offer', 'bid', 'exh'), ('price', 'offer', 'bid'), ('price', 'bid')

    def arm_p(self, p): return p['price'] == p['offer'] and p['exh'] == 1

    def at_q(self, p, q):
        ok = q['offer'] == p['offer'] and q['price'] < p['price'] and q['bid'] < p['bid']
        return SELL if ok else None

    def breach(self, t, d):
        if _ge(d['price'], t['p']['price']): return "a print at or above p's print"
        if _gt(d['bid'], t['q']['bid']): return 'the bid rose above the bid at q'


class CX10(CXEntry):
    code, title, side = 'CX10', 'reverse up from a stationary bid', BUY
    need_p, need_q, watch = ('price', 'bid', 'offer', 'exh'), ('price', 'bid', 'offer'), ('price', 'offer')

    def arm_p(self, p): return p['price'] == p['bid'] and p['exh'] == -1

    def at_q(self, p, q):
        ok = q['bid'] == p['bid'] and q['price'] > p['price'] and q['offer'] > p['offer']
        return BUY if ok else None

    def breach(self, t, d):
        if _le(d['price'], t['p']['price']): return "a print at or below p's print"
        if _lt(d['offer'], t['q']['offer']): return 'the offer fell below the offer at q'


class CX15(CXEntry):
    code, title = 'CX15', 'a pressure turn at an unchanged price'
    need_p, need_q, watch = ('price', 'bid', 'offer', 'exh'), ('price', 'bid', 'offer', 'exh', 'side'), \
        ('exh', 'bid', 'offer')

    def arm_p(self, p): return p['exh'] in (1, -1)

    def at_q(self, p, q):
        if not (q['price'] == p['price'] and q['bid'] == p['bid'] and q['offer'] == p['offer']):
            return None
        if q['exh'] == 1 and p['exh'] == -1 and q['side'] == 'B':
            return BUY
        if q['exh'] == -1 and p['exh'] == 1 and q['side'] == 'S':
            return SELL

    def breach(self, t, d):
        if t['side'] == BUY:
            if _le(d['exh'], 0): return 'exhaustion reached zero or turned negative'
            if _lt(d['bid'], t['q']['bid']): return 'the bid fell below the bid at q'
        else:
            if _ge(d['exh'], 0): return 'exhaustion reached zero or turned positive'
            if _gt(d['offer'], t['q']['offer']): return 'the offer rose above the offer at q'


# ---- management rules -------------------------------------------------------------------------------------------

class CXManage(Rule):
    """One arm per eligible position. kind EXIT: an exit indication at q. Otherwise q gives the rule's own word and
    a hold or warning is kept for that position (its q reference unchanged) until `react` ends it."""
    reads_no_side = True
    manages = 'CX'
    want = None
    kind = EXIT
    need_p, need_q = (), ()

    def __init__(self):
        super().__init__()
        self.armed = {}         # trade id -> the view at p
        self.held = {}          # trade id -> the view at q, while a hold or warning is active

    def arm_pos(self, p) -> bool: raise NotImplementedError
    def at_q(self, p, q) -> bool: raise NotImplementedError
    def react(self, h, d) -> list: return []                 # kinds, in order; EXIT last

    def _ind(self, tid, kind, trig, late=False):
        self.passed(f'indication: {kind}' + (' (known late)' if late else ''))
        return INDICATION(self.code, tid, kind, trig, late)

    def decide(self, snap):
        d = _view(snap.print, snap.lines.exh, snap.lines.dip)
        live = {x['trade_id'] for x in snap.positions}
        mine = {x['trade_id'] for x in snap.positions if x['side'] == self.want}
        for tid in [k for k in self.held if k not in live]:
            del self.held[tid]                               # the position closed: its warning is released
        for tid in [k for k in self.armed if k not in live]:
            del self.armed[tid]
        out = []
        for tid in list(self.held):
            h = self.held[tid]
            steps = [(_late_view(m), True, dict(moment_ns=m['moment_ns'])) for m in snap.lines.late] + [(d, False, {})]
            for view, late, extra in steps:
                kinds = self.react(h, view)
                if kinds:
                    trig = dict(extra, reference_q=h, now=view)
                    if len(kinds) > 1:
                        trig['both'] = both(*kinds)
                    out += [self._ind(tid, k, trig, late) for k in kinds]
                    del self.held[tid]
                    break
        interrupted = snap.gap or snap.in_settlement
        if interrupted:
            if self.armed:
                self.passed(INTERRUPTED_ARM)
            if self.held:
                self.passed(INTERRUPTED_OPEN)
        armed, self.armed = self.armed, {}
        if not interrupted:
            for tid, a in armed.items():
                if tid not in mine:
                    continue
                bad = _first_none(d, self.need_q)
                if bad:
                    self.missing(f'{bad} at q')
                elif self.at_q(a, d):
                    out.append(self._ind(tid, self.kind, dict(p=a, q=d)))
                    if self.kind != EXIT:
                        self.held[tid] = d
        if not snap.in_settlement:
            gone = {i.trade_id for i in out if i.kind == EXIT}
            for tid in sorted(mine - gone):
                if tid in self.held:
                    continue
                bad = _first_none(d, self.need_p)
                if bad:
                    self.missing(f'{bad} at p')
                elif self.arm_pos(d):
                    self.arm()
                    self.armed[tid] = d
        return out


class CX05(CXManage):
    code, title, want = 'CX05', 'exit an upward position when the bid gives way', BUY
    need_p, need_q = ('exh', 'dip', 'bid'), ('exh', 'dip', 'bid')

    def arm_pos(self, p): return p['exh'] == 1 and p['dip'] == 1
    def at_q(self, p, q): return q['dip'] == 1 and q['exh'] == -1 and q['bid'] < p['bid']


class CX06(CXManage):
    code, title, want = 'CX06', 'exit a downward position as the offer lifts', SELL
    need_p, need_q = ('exh', 'dip', 'offer'), ('exh', 'dip', 'offer')

    def arm_pos(self, p): return p['exh'] == -1 and p['dip'] == -1
    def at_q(self, p, q): return q['dip'] == -1 and q['exh'] == 1 and q['offer'] > p['offer']


class CX11(CXManage):
    code, title, want, kind = 'CX11', 'stay in an upward position when a seller tests a higher bid', BUY, 'maintain'
    need_p, need_q = ('dip', 'bid', 'offer'), ('dip', 'bid', 'offer', 'side', 'price')

    def arm_pos(self, p): return p['dip'] == 1

    def at_q(self, p, q):
        return q['dip'] == 1 and q['bid'] > p['bid'] and q['offer'] == p['offer'] and q['side'] == 'S' \
            and q['price'] == q['bid']

    def react(self, h, d):
        if _lt(d['bid'], h['bid']) or _le(d['dip'], 0):
            return ['withdraw hold', EXIT]
        return []


class CX12(CXManage):
    code, title, want, kind = 'CX12', 'stay in a downward position when a buyer tests a lower offer', SELL, 'maintain'
    need_p, need_q = ('dip', 'bid', 'offer'), ('dip', 'bid', 'offer', 'side', 'price')

    def arm_pos(self, p): return p['dip'] == -1

    def at_q(self, p, q):
        return q['dip'] == -1 and q['offer'] < p['offer'] and q['bid'] == p['bid'] and q['side'] == 'B' \
            and q['price'] == q['offer']

    def react(self, h, d):
        if _gt(d['offer'], h['offer']) or _ge(d['dip'], 0):
            return ['withdraw hold', EXIT]
        return []


class CX13(CXManage):
    code, title, want, kind = 'CX13', 'question a rise that the bid does not follow', BUY, 'suspend additions'
    need_p, need_q = ('dip', 'price', 'bid'), ('side', 'price', 'exh', 'bid')

    def arm_pos(self, p): return p['dip'] == -1

    def at_q(self, p, q):
        return q['side'] == 'B' and q['price'] > p['price'] and q['exh'] == -1 and q['bid'] <= p['bid']

    def react(self, h, d):
        if _lt(d['price'], h['price']):
            return [EXIT]                                    # the exit has priority over a release on the same row
        if _gt(d['bid'], h['bid']) or _ge(d['exh'], 0):
            return ['release']
        return []


class CX14(CXManage):
    code, title, want, kind = 'CX14', 'question a decline that the offer does not follow', SELL, 'suspend additions'
    need_p, need_q = ('dip', 'price', 'offer'), ('side', 'price', 'exh', 'offer')

    def arm_pos(self, p): return p['dip'] == 1

    def at_q(self, p, q):
        return q['side'] == 'S' and q['price'] < p['price'] and q['exh'] == 1 and q['offer'] >= p['offer']

    def react(self, h, d):
        if _gt(d['price'], h['price']):
            return [EXIT]
        if _lt(d['offer'], h['offer']) or _le(d['exh'], 0):
            return ['release']
        return []


RULES = [CX01, CX02, CX03, CX04, CX07, CX08, CX09, CX10, CX15, CX05, CX06, CX11, CX12, CX13, CX14]


def all_rules() -> list:
    return [r() for r in RULES]
