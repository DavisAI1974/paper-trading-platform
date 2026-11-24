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


def _endpoint(event, reason):
    if event is None:
        return None
    return dict(received=event.get("fill_received_ns", event["received"]), rule=event["rule"],
                bid=event.get("bid"), ask=event.get("ask"), reason=reason,
                order_id=event.get("order_id"), fill_id=event.get("fill_id"),
                quote_reference=event.get("quote_reference") if event["kind"] == "fill"
                else event.get("expiry_quote_reference", event.get("reference")),
                trade_data=event.get("trade_data", True))


def _timing(row):
    orders = {order["order_id"]: order for order in row["orders"] if order.get("order_id")}
    native = (row["fills"] or row["unsubmitted"] or any(order.get("pricing") == "native L3" for order in orders.values())
              or any(state["rule"] == row["rule"] for state in row["order_states"].values()))
    if not native:
        entry = row["entry_signal"] or row["entry"]
        exit_event = row["exit_signal"] or row["exit"]
        return dict(basis="decision", time_in=_endpoint(entry, entry.get("reason") if entry else None),
                    time_out=_endpoint(exit_event, exit_event.get("reason") if exit_event else None),
                    open_contracts=None)

    entered = exited = None
    held = 0
    for fill in row["fills"]:
        previous = held
        qty = int(fill["quantity"])
        held += qty if fill["side"] == "buy" else -qty
        order = orders.get(fill["order_id"], {})
        reason = order.get("reason", fill.get("administrative_reason"))
        if fill["action"] == "open" and entered is None:
            entered = _endpoint(fill, reason)
        if held:
            exited = None
        elif previous and entered is not None and fill["action"] in ("close", "exit"):
            exited = _endpoint(fill, reason)
    return dict(basis="fill", time_in=entered, time_out=exited,
                open_contracts=abs(held) if entered is not None or not row["fills"] else None)


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
            if action not in ("open", "close", "exit") or (kind == "order status" and event.get("submitted") is not False):
                continue
            row = rows[key] = dict(rule=key[0], trade_id=key[1], entry=None, exit=None,
                                   entry_signal=None, exit_signal=None, orders=[], fills=[],
                                   order_states={}, unsubmitted=[], observation=None, interruption_exposure=None)
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
            elif event.get("submitted") is False:
                row["unsubmitted"].append(event)
            elif event.get("execution_event") == "observation ended with paper exposure":
                row["interruption_exposure"] = event
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
    ordered = list(reversed(rows.values()))
    for row in ordered:
        row["timing"] = _timing(row)
    return ordered
