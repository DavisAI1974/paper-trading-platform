"""Read each strategy trade from the run journal in sequence order."""

import json


def _parent_entry(db, run, exit_event):
    """Older manager exits refer to a parent entry instead of placing an entry order."""
    parent = exit_event.get("source_trade_id")
    if not parent:
        return None
    owner = exit_event.get("entry_rule")
    entries = db.execute(
        "SELECT body FROM events WHERE run=? AND sequence<=? "
        "AND json_extract(body,'$.trade_id')=? AND json_extract(body,'$.action')='open' "
        "AND json_extract(body,'$.kind') IN ('paper order','signal') "
        "AND (? IS NULL OR json_extract(body,'$.rule')=?) ORDER BY sequence",
        (run, exit_event["sequence"], parent, owner, owner),
    )
    selected = None
    for (body,) in entries:
        entry = json.loads(body)
        if selected is not None and selected["rule"] != entry["rule"]:
            return None
        if selected is None or selected["kind"] == "signal":
            selected = entry
    return selected


def trade_rows(db, run, rule=None, *, trade_id=None, through=None):
    where = ["run=?", "json_extract(body,'$.trade_id') IS NOT NULL"]
    params = [run]
    if rule:
        where.append("json_extract(body,'$.rule')=?")
        params.append(rule)
    if trade_id is not None:
        where.append("json_extract(body,'$.trade_id')=?")
        params.append(trade_id)
    if through is not None:
        where.append("sequence<=?")
        params.append(through)
    where.append("""(json_extract(body,'$.kind') IN ('paper order','order status','fill')
        OR (json_extract(body,'$.kind')='signal' AND json_extract(body,'$.action')
            IN ('open','close','exit','not scored','observation interrupted')))""")
    events = db.execute(f"SELECT body FROM events WHERE {' AND '.join(where)} ORDER BY sequence", params)
    rows = {}
    for (body,) in events:
        event = json.loads(body)
        kind, action = event["kind"], event.get("action")
        key = event["rule"], event["trade_id"]
        row = rows.get(key)
        if row is None:
            if kind not in ("signal", "paper order") or action not in ("open", "close", "exit"):
                continue
            row = rows[key] = dict(rule=key[0], trade_id=key[1], entry=None, exit=None,
                                   entry_signal=None, exit_signal=None, orders=[], fills=[],
                                   order_states={}, observation=None)
        if kind == "paper order":
            row["orders"].append(event)
            if action == "open" and row["entry"] is None:
                row["entry"] = event
            elif action in ("close", "exit"):
                row["exit"] = event
        elif kind == "signal":
            if action == "open":
                row["entry_signal"] = event
            elif action in ("close", "exit"):
                row["exit_signal"] = event
            else:
                row["observation"] = event
        else:
            if kind == "fill":
                row["fills"].append(event)
            if event.get("order_id"):
                row["order_states"][event["order_id"]] = event

    for row in rows.values():
        exit_event = row["exit"] or row["exit_signal"]
        if row["entry"] or row["entry_signal"] or not exit_event or exit_event["action"] != "exit":
            continue
        parent = _parent_entry(db, run, exit_event)
        if parent:
            if parent["kind"] == "paper order":
                row["entry"] = parent
                last = db.execute(
                    "SELECT body FROM events WHERE run=? AND json_extract(body,'$.rule')=? "
                    "AND json_extract(body,'$.order_id')=? AND json_extract(body,'$.kind') IN ('order status','fill') "
                    "AND (? IS NULL OR sequence<=?) ORDER BY sequence DESC LIMIT 1",
                    (run, parent["rule"], parent["order_id"], through, through),
                ).fetchone()
                if last:
                    row["order_states"][parent["order_id"]] = json.loads(last[0])
            else:
                row["entry_signal"] = parent
    return list(reversed(rows.values()))
