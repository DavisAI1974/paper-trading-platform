"""An independent table of the raw TimeAndSale messages of a 7e recording. Does not import the runner."""
import json, sys
path = sys.argv[1]
rows = []
for line in open(path):
    o = json.loads(line)
    if o.get('kind') != 'received':
        continue
    try:
        fr = json.loads(o['raw'])
    except Exception:
        continue
    if not isinstance(fr, dict) or fr.get('type') != 'FEED_DATA':
        continue
    for k, e in enumerate(fr['data']):
        if e.get('eventType') == 'TimeAndSale':
            rows.append(((o['msg_no'], k), e))
t0 = rows[0][1]['time']
def window(i, span_ms):
    t = rows[i][1]['time']; b = s = 0
    for j in range(i + 1):
        e = rows[j][1]
        if e.get('aggressorSide') in ('BUY', 'SELL') and e['time'] > t - span_ms:
            if e['aggressorSide'] == 'BUY': b += int(e['size'])
            else: s += int(e['size'])
    return b, s
print('idx place      t_ms(rel) seq    price size side  bid    ask    | 20s B/S line | 60s B/S line')
for i, (place, e) in enumerate(rows):
    b20, s20 = window(i, 20000); b60, s60 = window(i, 60000)
    l20 = '' if b20 + s20 == 0 else f'{(b20 - s20) / (b20 + s20):+.3f}'
    l60 = '' if b60 + s60 == 0 else f'{(b60 - s60) / (b60 + s60):+.3f}'
    side = {'BUY': 'B', 'SELL': 'S'}.get(e.get('aggressorSide'), '-')
    print(f"{i:3d} {str(place):10s} {e['time'] - t0:8d} {e['sequence']:5d} {e['price']:7} {e['size']:4} {side:4s} {e['bidPrice']:6} {e['askPrice']:6} | {b20:3d}/{s20:3d} {l20:7s} | {b60:3d}/{s60:3d} {l60:7s}")
