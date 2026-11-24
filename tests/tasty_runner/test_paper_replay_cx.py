"""Codex's rules CX01 to CX15 on the REAL 7e recordings (sandbox feed). A missing recording is a skip, never invented
data. The parts of each rule that can be read off the raw messages (prices, bid, offer, aggressor side, feed time) are
checked on the opening print q and the print before it, p, from the raw messages and not from the engine's facts. The
two lines are not re-made here (test_paper_replay.py checks them against tick.reads)."""
import json
import os
from pathlib import Path

import pytest

from tasty.paper.replay import run

RECORDINGS = Path(os.environ.get('TASTY_7E_RECORDINGS',
                                 '/home/user/markets-data/tasks/family-research-20261022/research-union-current/'
                                 'paper-trading-20261123/7e'))
FILES = ['sandbox-stream-20261123T231452738619403Z.jsonl', 'sandbox-stream-20261123T233543011745306Z.jsonl']


def _raw_prints(path):
    out = []
    for line in open(path, encoding='utf-8'):
        o = json.loads(line)
        if o.get('kind') != 'received':
            continue
        try:
            frame = json.loads(o['raw'])
        except (TypeError, ValueError):
            continue
        if isinstance(frame, dict) and frame.get('type') == 'FEED_DATA':
            out += [((int(o['msg_no']), k), e) for k, e in enumerate(frame['data'])
                    if e.get('eventType') == 'TimeAndSale']
    return out


def _f(e):
    return dict(price=e['price'], bid=e['bidPrice'], offer=e['askPrice'], side=e.get('aggressorSide'), time=e['time'])


# per rule: what the raw p and q must show (p, q: dicts from _f; side: the side the trade was opened on)
def _cx01(p, q, s): return s == 'BUY' and q['price'] > p['price'] and q['bid'] > p['bid'] and q['time'] != p['time']
def _cx02(p, q, s):
    return s == 'SELL' and q['side'] == 'SELL' and q['price'] < p['price'] and q['bid'] < p['bid'] \
        and q['time'] != p['time']
def _cx03(p, q, s): return s == 'BUY' and q['side'] == 'BUY' and q['price'] == q['offer']
def _cx04(p, q, s):
    return s == 'SELL' and q['side'] == 'SELL' and q['price'] == q['bid'] and q['offer'] <= p['offer']
def _cx07(p, q, s):
    return s == 'BUY' and p['price'] == p['offer'] and q['side'] == 'BUY' and q['price'] == q['offer'] \
        and q['offer'] > p['offer'] and q['bid'] == p['bid']
def _cx08(p, q, s):
    return s == 'SELL' and p['price'] == p['bid'] and q['side'] == 'SELL' and q['price'] == q['bid'] \
        and q['bid'] < p['bid'] and q['offer'] == p['offer']
def _cx09(p, q, s):
    return s == 'SELL' and p['price'] == p['offer'] and q['offer'] == p['offer'] and q['price'] < p['price'] \
        and q['bid'] < p['bid']
def _cx10(p, q, s):
    return s == 'BUY' and p['price'] == p['bid'] and q['bid'] == p['bid'] and q['price'] > p['price'] \
        and q['offer'] > p['offer']
def _cx15(p, q, s):
    want = {'BUY': 'BUY', 'SELL': 'SELL'}[s]
    return q['price'] == p['price'] and q['bid'] == p['bid'] and q['offer'] == p['offer'] and q['side'] == want


ENTRY = dict(CX01=_cx01, CX02=_cx02, CX03=_cx03, CX04=_cx04, CX07=_cx07, CX08=_cx08, CX09=_cx09, CX10=_cx10,
             CX15=_cx15)


def _cx05(p, q): return q['bid'] < p['bid']
def _cx06(p, q): return q['offer'] > p['offer']
def _cx11(p, q): return q['bid'] > p['bid'] and q['offer'] == p['offer'] and q['side'] == 'SELL' and q['price'] == q['bid']
def _cx12(p, q): return q['offer'] < p['offer'] and q['bid'] == p['bid'] and q['side'] == 'BUY' and q['price'] == q['offer']
def _cx13(p, q): return q['side'] == 'BUY' and q['price'] > p['price'] and q['bid'] <= p['bid']
def _cx14(p, q): return q['side'] == 'SELL' and q['price'] < p['price'] and q['offer'] >= p['offer']


MANAGE_AT_Q = dict(CX05=_cx05, CX06=_cx06, CX11=_cx11, CX12=_cx12, CX13=_cx13, CX14=_cx14)
AT_Q_KINDS = ('maintain', 'suspend additions')


@pytest.fixture(scope='module', params=FILES)
def recorded(request):
    path = RECORDINGS / request.param
    if not path.exists():
        pytest.skip(f'the real recording is not here: {path}')
    raw = _raw_prints(path)
    return path, raw, {place: i for i, (place, _) in enumerate(raw)}, run(path)


def _p_q(raw, at, e_at):
    i = at[tuple(e_at['place'])]
    assert i > 0
    return _f(raw[i - 1][1]), _f(raw[i][1]), i


def test_cx_entries_meet_the_raw_messages(recorded):
    path, raw, at, eng = recorded
    opens = [e for e in eng.ledger.events if e['event'] == 'open' and e['strategy'] in ENTRY]
    if not opens:
        pytest.skip('no CX entry opened on this recording')
    seen = set()
    for e in opens:
        p, q, i = _p_q(raw, at, e['at'])
        seen.add(e['strategy'])
        assert ENTRY[e['strategy']](p, q, e['side']), f'{e["trade_id"]} at {e["at"]["place"]}: p {p} q {q}'
        # the engine's own p and q are the print before and the print itself, with the same quote
        tp, tq = e['trigger']['p'], e['trigger']['q']
        assert tp['place'] == list(raw[i - 1][0]) and tq['place'] == list(raw[i][0])
        for t, r in ((tp, p), (tq, q)):
            assert (t['price'], t['bid'], t['offer']) == (r['price'], r['bid'], r['offer'])
        assert tq['time'] == q['time'] * 1_000_000 and tp['time'] == p['time'] * 1_000_000
    assert seen


def test_cx_indications_at_q_meet_the_raw_messages(recorded):
    path, raw, at, eng = recorded
    inds = [e for e in eng.ledger.events if e['event'] == 'indication' and 'p' in e['trigger']]
    if not inds:
        pytest.skip('no CX indication at q on this recording')
    for e in inds:
        p, q, _ = _p_q(raw, at, e['at'])
        assert MANAGE_AT_Q[e['strategy']](p, q), f'{e["strategy"]} {e["trade_id"]} at {e["at"]["place"]}: p {p} q {q}'
        pos = eng.ledger.trades[e['trade_id']]
        assert pos['side'] == ('BUY' if e['strategy'] in ('CX05', 'CX11', 'CX13') else 'SELL')


def test_cx_one_open_trade_per_strategy_at_a_time(recorded):
    path, raw, at, eng = recorded
    live = {}
    for e in eng.ledger.events:
        if e['strategy'] not in ENTRY:
            continue
        if e['event'] == 'open':
            assert e['strategy'] not in live, f'{e["strategy"]} opened a second idea while one was active'
            live[e['strategy']] = e['trade_id']
        elif e['event'] == 'close':
            live.pop(e['strategy'], None)
