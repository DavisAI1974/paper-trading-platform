"""Offline checks of the runner on REAL stored files only (skipped when absent). No network, no sign-in, no order,
no invented broker response: the order-path readers run over responses recorded by the 7e check in the sandbox."""
import json
import os
from pathlib import Path

import pytest

from tasty.paper import broker_tasty as bt
from tasty.paper import runner
from tasty.paper.replay import run

RECORDINGS = Path(os.environ.get('TASTY_7E_RECORDINGS',
                                 '/home/user/markets-data/tasks/family-research-20261022/research-union-current/'
                                 'paper-trading-20261123/7e'))
STREAM = RECORDINGS / 'sandbox-stream-20261123T231452738619403Z.jsonl'
ORDER_FILLED = RECORDINGS / 'sandbox-order-20261123T230217484820112Z.jsonl'
ORDER_LIVE = RECORDINGS / 'sandbox-order-20261123T232512063886931Z.jsonl'


def need(p):
    if not p.exists():
        pytest.skip(f'the real file is not here: {p}')
    return p


def lines(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]


def jl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]


def trades_of(path):
    return [{k: v for k, v in r.items() if k != 'orders'} for r in jl(path)]


@pytest.fixture(scope='module')
def replayed(tmp_path_factory):
    need(STREAM)
    out = tmp_path_factory.mktemp('replay')
    assert runner.main(['--out', str(out), '--replay', str(STREAM)]) == 0
    return out


def test_replay_ledger_equals_replay_run(replayed):
    expected = [json.loads(json.dumps(r, sort_keys=True, default=str)) for r in run(STREAM).ledger.records()]
    assert trades_of(replayed / 'ledger.jsonl') == expected


def test_replay_sends_nothing_and_intends_every_open_and_close(replayed):
    orders = jl(replayed / 'orders.jsonl')
    assert not any(o.get('sent') is True for o in orders)
    intents = [o for o in orders if o['kind'] == 'intent']
    ev = [e['event']['event'] for e in jl(replayed / 'events.jsonl')]
    assert len(intents) == ev.count('open') + ev.count('close')
    assert all(o['sent'] is False for o in intents)
    assert not [o for o in orders if o['kind'] in ('request', 'response')]
    assert (replayed / 'receipt.json').exists()


def test_replay_replays_its_own_feed(replayed, tmp_path):
    assert runner.main(['--out', str(tmp_path), '--replay', str(replayed / 'feed.jsonl')]) == 0
    assert trades_of(tmp_path / 'ledger.jsonl') == trades_of(replayed / 'ledger.jsonl')


def response_for(path, order_id, pick):
    got = [o for o in lines(path) if o.get('kind') == 'response' and o.get('status') == 200
           and o.get('path') == f'/accounts/5WY79188/orders/{order_id}']
    return pick(got)['body']


def test_fill_reader_on_the_filled_order():
    need(ORDER_FILLED)
    first = response_for(ORDER_FILLED, 2207792, lambda g: g[0])      # recorded as Routed, no fills yet
    last = response_for(ORDER_FILLED, 2207792, lambda g: g[-1])      # recorded as Filled
    assert bt.read_outcome(first)['confirmed'] is False
    out = bt.read_outcome(last)
    assert out['confirmed'] is True and out['filled_quantity'] == 1
    assert [f['fill_price'] for f in out['fills']] == ['3.534']
    pos = [o for o in lines(ORDER_FILLED) if o.get('kind') == 'response' and o.get('path', '').endswith('/positions')][0]['body']
    p = bt.read_positions(pos)
    assert [(x['symbol'], x['signed_quantity'], x['average_open_price']) for x in p] == [('/NGF7', 1, '3.534')]
    assert bt.net_position(p, '/NGF7') == 1


def test_a_live_status_with_a_fill_is_not_taken_as_unfilled():
    need(ORDER_LIVE)
    live = response_for(ORDER_LIVE, 2207911, lambda g: g[0])
    out = bt.read_outcome(live)
    assert out['status_as_said'] == 'Live'
    assert out['confirmed'] is True and out['fills'][0]['fill_price'] == '3.534'
    pos = [o for o in lines(ORDER_LIVE) if o.get('kind') == 'response' and o.get('path', '').endswith('/positions')][-1]['body']
    assert bt.read_positions(pos) == [] and bt.net_position(bt.read_positions(pos), '/NGF7') == 0


def test_out_inside_the_code_repository_is_refused(tmp_path):
    inside = runner.CODE_REPO / 'tests' / 'tasty_runner' / 'not_here'
    with pytest.raises(SystemExit):
        runner.main(['--out', str(inside), '--replay', str(STREAM)])
    assert not inside.exists()


def test_a_non_sandbox_host_is_refused_for_orders():
    for base in ('https://api.tastyworks.com', 'http://api.cert.tastyworks.com', 'https://api.cert.tastyworks.com.evil.example'):
        with pytest.raises(bt.HostRefused):
            bt.make_ctx('/nonexistent', base)
    bt.check_order_host(bt.BASES['sandbox'])


def test_halt_hour_refuses_by_the_wall_clock():
    # 2026-11-23 22:30:00 UTC is 5:30 pm Eastern; 21:30:00 UTC is 4:30 pm
    assert bt.in_halt_hour(1795473000 * 10**9) is True
    assert bt.in_halt_hour(1795469400 * 10**9) is False
