"""Close out the sandbox account by hand after a run whose flatten was refused. SANDBOX ONLY.

    python3 -m tasty.paper.close_out --out DIR --account A --symbol /NGF7 [--send]

It reads the positions and the live-orders list, asks the sandbox to cancel every order that is not terminal by its
status (a filled order whose status stayed "Live" is one of them: the run of 2026-11-24 showed the sandbox refusing
every buy while such sell orders stood), reads again, and then flattens the net position of the symbol with ONE
order through `Broker.send` (a fresh sign-in, a dry run, one submit, never re-sent). Without --send it only reads
and dry-runs nothing: it shows what it would cancel and flatten. Everything is written to DIR/orders.jsonl.
The flatten is not trade data.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tasty.paper import broker_tasty as bt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--account', required=True)
    ap.add_argument('--symbol', required=True)
    ap.add_argument('--send', action='store_true', help='without it nothing is cancelled and nothing is sent')
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    bt.check_out_dir(out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = bt.make_ctx(out)
    ctx['rec'] = rec = bt.LockedRecorder(ctx, 'orders.jsonl')
    ctx['creds'] = bt.read_creds('sandbox')
    bt.mint(ctx)
    broker = bt.Broker(ctx, a.account, a.symbol)
    run_id = bt.stamp(time.time_ns())

    def state(label):
        pos, live = broker.pull('positions'), broker.pull('live')
        positions = bt.read_positions(pos) if pos is not None else None
        unfinished = [dict(order_id=it.get('id'), status_as_said=it.get('status'),
                           action=((it.get('legs') or [{}])[0]).get('action'),
                           symbol=((it.get('legs') or [{}])[0]).get('symbol'))
                      for it in (bt.dget(live, 'data', 'items') or [])
                      if str(it.get('status')).lower() not in bt.TERMINAL] if live is not None else None
        rec.write(t_ns=time.time_ns(), kind='state', label=label, positions=positions, orders_not_terminal=unfinished)
        bt.say(f'{label}: positions {positions}; orders not terminal {unfinished}')
        return positions, unfinished

    positions, unfinished = state('before')
    if positions is None or unfinished is None:
        bt.say('could not read the account: nothing done')
        return 1
    if not a.send:
        bt.say('no --send: nothing cancelled, nothing sent')
        return 0
    for o in unfinished:
        if o['symbol'] == a.symbol:
            rec.write(t_ns=time.time_ns(), kind='cancel', order=o, answer=broker.cancel(o['order_id']))
    positions, unfinished = state('after the cancels')
    if positions is None:
        bt.say('could not read the positions after the cancels: nothing sent')
        return 1
    net = bt.net_position(positions, a.symbol)
    if net is None:
        bt.say('a position could not be signed: nothing sent')
        return 1
    if net == 0:
        bt.say('the account is flat in the symbol: nothing to send')
        return 0
    intent = dict(role='flatten', strategy=None, trade_id=None, action='Sell' if net > 0 else 'Buy', quantity=abs(net),
                  symbol=a.symbol, note=bt.FLATTEN_NOTE + ' (closed out by hand after the run)',
                  external_identifier=f'{run_id[:15]}-closeout', event_at=None)
    res = broker.send(intent)
    bt.say(f"flatten {intent['action']} {intent['quantity']}: sent {res['sent']}, {res['reason']}; outcome "
           f"{ {k: v for k, v in (res['outcome'] or {}).items() if k != 'fills'} }")
    state('at the end')
    return 0


if __name__ == '__main__':
    sys.exit(main())
