"""Part A checks on the REAL 7e recording only. A missing recording is a skip, never invented data."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from crypto_ng import tick
from tasty.paper import feed_tasty
from tasty.paper.events import PRINT, QUOTE
from tasty.paper.lines import EMPTY, MISSING
from tasty.paper.replay import Engine, run
from tasty.paper.rule_base import Rule

# The real recording lives in a markets-data checkout (branch ccr-349e2ffe-07p8od). TASTY_7E_RECORDINGS names the
# folder that holds it; the default is the session's own checkout.
RECORDINGS = Path(os.environ.get('TASTY_7E_RECORDINGS',
                                 '/home/user/markets-data/tasks/family-research-20261022/research-union-current/'
                                 'paper-trading-20261123/7e'))
RECORDING = RECORDINGS / 'sandbox-stream-20261123T231452738619403Z.jsonl'


@pytest.fixture(scope='module')
def recording():
    if not RECORDING.exists():
        pytest.skip(f'the real recording is not here: {RECORDING}')
    return RECORDING


def _msg_nos(path):
    out = []
    for line in open(path, encoding='utf-8'):
        o = json.loads(line)
        if o.get('kind') == 'received':
            out.append(int(o['msg_no']))
    return out


def _dump(events):
    return json.dumps(events, sort_keys=True, default=str)


def test_cut_equals_whole(recording):
    msgs = _msg_nos(recording)
    whole = run(recording)
    assert whole.ledger.events, 'the whole recording made no ledger event: the check would prove nothing'
    ks = sorted({int(k) for k in np.linspace(10, len(msgs), 16)})
    assert len(ks) >= 12
    for k in ks:
        last = msgs[k - 1]
        cut = run(recording, cut=k)
        expected = [e for e in whole.ledger.events if e['at']['place'][0] <= last]
        assert _dump(cut.ledger.events) == _dump(expected), f'cut at {k} differs from the whole up to {k}'
        assert cut.counts()['prints'] == sum(1 for e in feed_tasty.events_from_recording(recording, k)
                                             if isinstance(e, PRINT))


def test_live_lines_equal_one_pass(recording):
    eng = run(recording)
    prints = [e for e in feed_tasty.events_from_recording(recording) if isinstance(e, PRINT)]
    sided = [p for p in prints if p.side is not None]
    assert len(eng.lines.log) == len(sided) > 0
    t = np.array([p.time_ns for p in sided], np.int64)
    buy = np.array([p.side == 'B' for p in sided])
    size = np.array([int(p.size) for p in sided], np.int64)
    until = int(t[-1]) + 1
    first = prints[0].time_ns
    for span, col in ((tick.LINE_NS, 1), (tick.LEAN_NS, 2)):
        r = tick.reads(t, buy, size, span, until)
        flow, quiet = tick.line(r['bought'], r['sold'])
        at = {int(tr): k for k, tr in enumerate(r['trade']) if tr != tick.NONE}
        assert len(at) == len(sided)
        for row in eng.lines.log:
            k = at[row[0]]
            if int(r['moment'][k]) - first < span:
                want = MISSING
            else:
                want = EMPTY if quiet[k] else float(flow[k])
            assert row[col] == want, f'sided print {row[0]}, span {span}: live {row[col]} one pass {want}'


class _Spy(Rule):
    code = 'SPY'

    def __init__(self):
        super().__init__()
        self.places = set()

    def decide(self, snap):
        self.places |= {f['place'] for f in snap.run_facts}
        return []


def test_quote_read_by_rules_never_from_a_quote_event(recording):
    events = list(feed_tasty.events_from_recording(recording))
    print_places = {e.place for e in events if isinstance(e, PRINT)}
    quote_places = {e.place for e in events if isinstance(e, QUOTE)}
    assert quote_places and not (quote_places & print_places)
    spy = _Spy()
    eng = Engine(rules=[spy])
    for e in events:
        eng.on_event(e)
    assert spy.places and spy.places <= print_places
    # the answer of every rule is the same with every QUOTE event taken away
    with_quotes = run(recording)
    eng2 = Engine()
    for e in events:
        if not isinstance(e, QUOTE):
            eng2.on_event(e)
    assert _dump(with_quotes.ledger.events) == _dump(eng2.ledger.events)
    assert eng.quotes.n_quote_messages == len(quote_places)


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


def test_cl14_opens_on_the_first_print_of_the_other_side_since_the_step(recording):
    """CL14's text: the FIRST sell since the best bid's latest step up prints at the level that step made (mirror:
    the first buy since the best offer's latest step down). Checked from the raw messages, not from the engine's
    own facts: between the print whose quote first shows that level and the opening print there is no earlier
    print of that side."""
    raw = _raw_prints(recording)
    at = {place: i for i, (place, _) in enumerate(raw)}
    opens = [e for e in run(recording).ledger.events if e['event'] == 'open' and e['strategy'] == 'CL14']
    assert opens, 'CL14 opened nothing on the recording: the check would prove nothing'
    for e in opens:
        i = at[tuple(e['at']['place'])]
        side, field = ('SELL', 'bidPrice') if e['side'] == 'SELL' else ('BUY', 'askPrice')
        level = raw[i][1][field]
        assert raw[i][1]['aggressorSide'] == side and raw[i][1]['price'] == level
        j = i
        while j > 0 and raw[j - 1][1][field] == level:
            j -= 1                                    # j: the print whose own quote first shows the level
        assert j > 0, 'the level stood from the first print: there is no step to read'
        if e['side'] == 'SELL':
            assert raw[j - 1][1][field] < level       # the bid stepped up to it
        else:
            assert raw[j - 1][1][field] > level       # the offer stepped down to it
        earlier = [raw[k][0] for k in range(j, i) if raw[k][1].get('aggressorSide') == side]
        assert not earlier, f'{e["trade_id"]} opened at {raw[i][0]} but {earlier[0]} was the first {side} since the step'
