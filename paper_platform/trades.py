"""Project the relay's append-only paper orders into one row per strategy trade."""

import json


def trade_rows(db, run, rule, *, trade_id=None, through=None):
    # An entry rule owns its open. A manager's independent exit points back to
    # that open and never changes the parent position's closing order.
    where = ["a.run=?", "json_extract(a.body,'$.rule')=?",
             "json_extract(a.body,'$.kind')='paper order'",
             "json_extract(a.body,'$.action') IN ('open','exit')"]
    params = [run, rule]
    if trade_id is not None:
        where.append("json_extract(a.body,'$.trade_id')=?")
        params.append(trade_id)
    if through is not None:
        where.append("a.sequence<=?")
        params.append(through)
    sql = f"""
        SELECT a.body, b.body, (
            SELECT s.body FROM events s WHERE s.run=a.run
              AND json_extract(s.body,'$.trade_id')=json_extract(a.body,'$.trade_id')
              AND json_extract(s.body,'$.kind')='signal'
              AND json_extract(s.body,'$.action') IN ('not scored','observation interrupted')
              AND (? IS NULL OR s.sequence<=?)
            ORDER BY s.sequence DESC LIMIT 1
        ) FROM events a
        LEFT JOIN events b ON b.run=a.run
          AND json_extract(b.body,'$.kind')='paper order'
          AND json_extract(b.body,'$.trade_id')=CASE json_extract(a.body,'$.action')
              WHEN 'exit' THEN json_extract(a.body,'$.source_trade_id') ELSE json_extract(a.body,'$.trade_id') END
          AND json_extract(b.body,'$.action')=CASE json_extract(a.body,'$.action')
                                              WHEN 'exit' THEN 'open' ELSE 'close' END
          AND (? IS NULL OR b.sequence<=?)
          AND b.sequence=(SELECT MAX(last.sequence) FROM events last WHERE last.run=b.run
                AND json_extract(last.body,'$.kind')='paper order'
                AND json_extract(last.body,'$.trade_id')=json_extract(b.body,'$.trade_id')
                AND json_extract(last.body,'$.action')=json_extract(b.body,'$.action')
                AND (? IS NULL OR last.sequence<=?))
        WHERE {' AND '.join(where)}
        ORDER BY a.sequence DESC
    """
    rows = []
    for body, other, observation in db.execute(sql, (through, through, through, through, through, through, *params)):
        order = json.loads(body)
        paired = json.loads(other) if other else None
        entry, exit_order = (paired, order) if order["action"] == "exit" else (order, paired)
        fills = [
            json.loads(item[0]) for item in db.execute(
                "SELECT body FROM events WHERE run=? AND json_extract(body,'$.trade_id')=? "
                "AND json_extract(body,'$.rule')=? AND json_extract(body,'$.kind')='fill' "
                "AND (? IS NULL OR sequence<=?) ORDER BY sequence",
                (run, order["trade_id"], rule, through, through),
            )
        ]
        statuses = {}
        for leg in (entry, exit_order):
            if leg is None or not leg.get("order_id"):
                continue
            last = db.execute(
                "SELECT body FROM events WHERE run=? AND json_extract(body,'$.order_id')=? "
                "AND json_extract(body,'$.kind') IN ('order status','fill') "
                "AND (? IS NULL OR sequence<=?) ORDER BY sequence DESC LIMIT 1",
                (run, leg["order_id"], through, through),
            ).fetchone()
            if last:
                statuses[leg["order_id"]] = json.loads(last[0])
        rows.append(dict(order_states=statuses, fills=fills, trade_id=order["trade_id"], rule=rule, entry=entry, exit=exit_order,
                         observation=json.loads(observation) if observation else None))
    return rows
