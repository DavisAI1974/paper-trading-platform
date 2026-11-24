"""What the prints with no side look like, counted from the raw TimeAndSale messages. Does not import the runner."""
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
side = lambda e: {'BUY': 'B', 'SELL': 'S'}.get(e.get('aggressorSide'))
c = collections.Counter()
# fields whose values differ between sided and no-side prints
vals = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for _, e in rows:
    g = 'sided' if side(e) else 'no side'
    for k, v in e.items():
        if k in ('time', 'sequence', 'price', 'size', 'bidPrice', 'askPrice', 'eventSymbol', 'index', 'aggressorSide', 'timeNanoPart', 'eventTime'): continue
        vals[k][g][json.dumps(v)] += 1
for k in sorted(vals):
    print('field', k, {g: dict(v) for g, v in vals[k].items()})
prev = None
for i, (_, e) in enumerate(rows):
    s = side(e)
    if s is None:
        c['no side'] += 1
        c['no side: price at its offer' if e['price'] == e['askPrice'] else ('no side: price at its bid' if e['price'] == e['bidPrice'] else 'no side: at neither')] += 1
        c[f"no side: size {e['size']}"] += 1
    if prev is not None:
        bid_step, ask_step = e['bidPrice'] != prev['bidPrice'], e['askPrice'] != prev['askPrice']
        price_step = e['price'] != prev['price']
        tag = 'no-side print' if s is None else 'sided print'
        if bid_step or ask_step: c[f'a quote step first shown by a {tag}'] += 1
        if price_step: c[f'a new price first shown by a {tag}'] += 1
        if s is None:
            c['no side: same feed time as the print before' if e['time'] == prev['time'] else 'no side: later feed time than the print before'] += 1
    prev = e
# what stands at the next sided print after a quote step shown by a no-side print: is the step still there?
for i in range(1, len(rows)):
    e, p = rows[i][1], rows[i - 1][1]
    if side(e) is None and (e['bidPrice'] != p['bidPrice'] or e['askPrice'] != p['askPrice']):
        j = i + 1
        while j < len(rows) and side(rows[j][1]) is None: j += 1
        if j == len(rows): c['step on a no-side print: no sided print after it']+=1; continue
        n = rows[j][1]
        c['step on a no-side print: the next sided print shows the same two quotes' if (n['bidPrice'], n['askPrice']) == (e['bidPrice'], e['askPrice']) else 'step on a no-side print: the next sided print shows other quotes'] += 1
for k in sorted(c): print(f'{k}: {c[k]}')
print('prints:', len(rows))
