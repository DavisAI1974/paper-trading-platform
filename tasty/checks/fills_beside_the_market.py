"""Each sandbox fill of a run set beside the feed's own prints at the market time it filled. Reads the run's
orders.jsonl and a later feed recording. One line per fill; nothing is combined."""
import json, sys, datetime
run, feed = sys.argv[1], sys.argv[2]
prints = []
for l in open(feed + '/feed.jsonl'):
    o = json.loads(l)
    if o.get('kind') != 'received': continue
    try: fr = json.loads(o['raw'])
    except Exception: continue
    if isinstance(fr, dict) and fr.get('type') == 'FEED_DATA':
        for e in fr['data']:
            if e.get('eventType') == 'TimeAndSale':
                prints.append((e['time'], e['sequence'], e['price'], e['bidPrice'], e['askPrice'], e.get('aggressorSide'), o['t_ns']))
prints.sort()
def hms(ms): return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%H:%M:%S.%f')[:-3]
print('feed-only recording: prints', len(prints), 'market time', hms(prints[0][0]), 'to', hms(prints[-1][0]), '| behind by (s)', round((prints[0][6] / 1e6 - prints[0][0]) / 1000, 1), 'to', round((prints[-1][6] / 1e6 - prints[-1][0]) / 1000, 1))
rows = []
intents = {}
for l in open(run + '/orders.jsonl'):
    o = json.loads(l)
    if o.get('kind') == 'intent' and o.get('sent'): intents[o['external_identifier']] = o
    if o.get('kind') == 'fill':
        f = o['fill']; t = datetime.datetime.fromisoformat(f['filled_at']).timestamp() * 1000
        before = [p for p in prints if p[0] <= t]; after = [p for p in prints if p[0] > t]
        b, a = (before[-1] if before else None), (after[0] if after else None)
        i = intents[o['external_identifier']]
        rows.append(dict(trade_id=i['trade_id'], action=i['action'], order_id=o['order_id'], fill_price=f['fill_price'], filled_at_utc=f['filled_at'],
                         print_acted_on=dict(bid=o['bid_at_print'], offer=o['offer_at_print']),
                         feed_print_before_the_fill=None if b is None else dict(market_time_utc=hms(b[0]), price=b[2], bid=b[3], offer=b[4], side=b[5]),
                         feed_print_after_the_fill=None if a is None else dict(market_time_utc=hms(a[0]), price=a[2], bid=a[3], offer=a[4], side=a[5])))
for r in rows:
    b, a = r['feed_print_before_the_fill'], r['feed_print_after_the_fill']
    print(r['trade_id'], r['action'], 'filled', r['fill_price'], 'at', r['filled_at_utc'][11:23], '| acted on bid/offer', r['print_acted_on']['bid'], r['print_acted_on']['offer'],
          '| market before:', b and (b['market_time_utc'], b['bid'], b['offer']), '| after:', a and (a['market_time_utc'], a['bid'], a['offer']))
json.dump(rows, open(sys.argv[3], 'w'), indent=1)
