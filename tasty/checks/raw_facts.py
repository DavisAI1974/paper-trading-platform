"""Raw facts of a 7e recording, counted from the TimeAndSale JSON only. Does not import the runner."""
import json, sys, collections
rows = []
for line in open(sys.argv[1]):
    o = json.loads(line)
    if o.get('kind') != 'received': continue
    try: fr = json.loads(o['raw'])
    except Exception: continue
    if not isinstance(fr, dict) or fr.get('type') != 'FEED_DATA': continue
    for k, e in enumerate(fr['data']):
        if e.get('eventType') == 'TimeAndSale': rows.append(((o['msg_no'], k), e))
c = collections.Counter()
for place, e in rows:
    s = e.get('aggressorSide')
    c['prints'] += 1; c[f'side {s}'] += 1
    if s == 'BUY': c['buy at its own ask' if e['price'] == e['askPrice'] else 'buy NOT at its own ask'] += 1
    elif s == 'SELL': c['sell at its own bid' if e['price'] == e['bidPrice'] else 'sell NOT at its own bid'] += 1
    else:
        c['no side: at its ask' if e['price'] == e['askPrice'] else ('no side: at its bid' if e['price'] == e['bidPrice'] else 'no side: at neither')] += 1
order = [(e['time'], e['sequence']) for _, e in rows]
c['out of feed order'] = sum(1 for a, b in zip(order, order[1:]) if b < a)
import datetime
first, last = rows[0][1]['time'], rows[-1][1]['time']
print('first print feed time (UTC):', datetime.datetime.fromtimestamp(first / 1000, datetime.timezone.utc).isoformat(), ' last:', datetime.datetime.fromtimestamp(last / 1000, datetime.timezone.utc).isoformat())
for k in sorted(c): print(f'{k}: {c[k]}')
