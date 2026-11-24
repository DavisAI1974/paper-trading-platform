"""Sequence numbers of the TimeAndSale prints of a 7e recording: the step from each print to the next, counted
beside whether the two share a feed time, a side and a price; and the day volume beside the print sizes.
Does not import the runner."""
import json, sys, collections
rows, events = [], []
for line in open(sys.argv[1]):
    o = json.loads(line)
    if o.get('kind') != 'received': continue
    try: fr = json.loads(o['raw'])
    except Exception: continue
    if not isinstance(fr, dict) or fr.get('type') != 'FEED_DATA': continue
    for k, e in enumerate(fr['data']):
        if e.get('eventType') == 'TimeAndSale':
            rows.append(e); events.append(('P', e))
        elif e.get('eventType') == 'Trade':
            events.append(('T', e))
c = collections.Counter()
for a, b in zip(rows, rows[1:]):
    d = b['sequence'] - a['sequence']
    same_t = a['time'] == b['time']; same_side = a.get('aggressorSide') == b.get('aggressorSide'); same_px = a['price'] == b['price']
    c[(d, 'same feed time' if same_t else 'later feed time', 'same side' if same_side else 'other side', 'same price' if same_px else 'other price')] += 1
for k in sorted(c): print(k, c[k])
# day volume against print sizes, message by message
seen = 0.0; base = None; prev_dv = None; n = collections.Counter()
for kind, e in events:
    if kind == 'P': seen += float(e['size'])
    else:
        dv = e.get('dayVolume')
        if dv in (None, 'NaN'): continue
        if base is None: base = (dv, seen); prev = 0.0; continue
        un = (dv - base[0]) - (seen - base[1])
        n['day volume messages'] += 1
        if un > prev: n['unexplained grew'] += 1
        elif un < prev: n['unexplained shrank'] += 1
        else: n['unexplained unchanged'] += 1
        prev = un
print(dict(n), 'unexplained at the end', prev)
