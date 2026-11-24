"""Prints and Quote messages of a 7e recording in arrival order, raw. Does not import the runner."""
import json, sys
path, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
t0 = None
for line in open(path):
    o = json.loads(line)
    if o.get('kind') != 'received': continue
    try: fr = json.loads(o['raw'])
    except Exception: continue
    if not isinstance(fr, dict) or fr.get('type') != 'FEED_DATA': continue
    if t0 is None: t0 = o['t_ns']
    if not (lo <= o['msg_no'] <= hi): continue
    for k, e in enumerate(fr['data']):
        rx = (o['t_ns'] - t0) / 1e6
        if e.get('eventType') == 'TimeAndSale':
            print(f"msg {o['msg_no']:4d}.{k} rx {rx:9.0f}ms  PRINT feed_ms {e['time']} seq {e['sequence']} px {e['price']} sz {e['size']} {e.get('aggressorSide')} bid {e['bidPrice']} ask {e['askPrice']}  exch {e.get('exchangeCode')} cond {e.get('exchangeSaleConditions')} spread {e.get('spreadLeg')} ext {e.get('extendedTradingHours')}")
        elif e.get('eventType') == 'Quote':
            print(f"msg {o['msg_no']:4d}.{k} rx {rx:9.0f}ms  QUOTE bid {e['bidPrice']} x{e.get('bidSize')} ask {e['askPrice']} x{e.get('askSize')}")
        elif e.get('eventType') == 'Trade':
            print(f"msg {o['msg_no']:4d}.{k} rx {rx:9.0f}ms  TRADE px {e.get('price')} sz {e.get('size')} dayVol {e.get('dayVolume')}")
