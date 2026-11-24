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
        SELECT a.body, b.body FROM events a
        LEFT JOIN events b ON b.run=a.run
          AND json_extract(b.body,'$.kind')='paper order'
          AND json_extract(b.body,'$.trade_id')=coalesce(json_extract(a.body,'$.source_trade_id'),
                                                        json_extract(a.body,'$.trade_id'))
          AND json_extract(b.body,'$.action')=CASE json_extract(a.body,'$.action')
                                              WHEN 'exit' THEN 'open' ELSE 'close' END
          AND (? IS NULL OR b.sequence<=?)
        WHERE {' AND '.join(where)}
        ORDER BY a.sequence DESC
    """
    rows = []
    for body, other in db.execute(sql, (through, through, *params)):
        order = json.loads(body)
        paired = json.loads(other) if other else None
        entry, exit_order = (paired, order) if order["action"] == "exit" else (order, paired)
        rows.append(dict(trade_id=order["trade_id"], rule=rule, entry=entry, exit=exit_order))
    return rows
