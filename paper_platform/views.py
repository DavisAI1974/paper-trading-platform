"""One HTML template for every market and transport. Only the server formats trade events."""

from datetime import datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .market import markets
from .rulebook import catalogue


def e(value):
    return escape(str(value), quote=True)


def price(value, profile):
    return "unavailable" if value is None else format(Decimal(str(value)) / profile["price_scale"], "f")


def prices(row, profile):
    return (f"bid {price(row.get('bid'), profile)} · ask {price(row.get('ask'), profile)} "
            f"{e(profile['price_unit'])}")


def trade_card(trade, profile):
    def time_at(event):
        seconds, nanos = divmod(int(event["received"]), 1_000_000_000)
        moment = datetime.fromtimestamp(seconds, ZoneInfo("America/New_York"))
        return f"{moment:%Y-%m-%d %H:%M:%S}.{nanos:09d} {moment:%Z}"

    def leg(label, order):
        at = time_at(order)
        detail = ""
        if order.get("placement"):
            limit = (" @ " + price(order["limit_price"], profile)) if order.get("limit_price") is not None else ""
            detail = (f"<small>{e(order['order_side'])} {e(order['quantity'])} · "
                      f"{e(order['placement'])}{limit}</small>")
        state = trade["order_states"].get(order.get("order_id"))
        if state:
            status = state.get("order_status", state.get("execution_event", ""))
            remaining = state.get("remaining_quantity")
            detail += f"<small>{e(status)}" + (f" · {e(remaining)} remaining" if remaining is not None else "") + "</small>"
        return (f"<p><b>{label}</b> · {e(order['rule'])} · {at}</p>{detail}"
                f"<small>{prices(order, profile)}</small><p>{e(order['reason'])}</p>")

    entry = trade["entry"] or trade["entry_signal"]
    exit_event = trade["exit"] or trade["exit_signal"]
    first = entry or exit_event
    rule, key = trade["rule"], trade["trade_id"]
    side = first.get("side") or first.get("order_side", "")
    fields = [f'<details id="trade-{e(rule)}-{e(key)}" class="trade-row">'
              f'<summary>{e(rule)} · {e(side)} · {time_at(first)}</summary><small>Trade · {e(key)}</small>']
    if entry and (entry["rule"] != rule or entry["kind"] == "signal"):
        label = "Parent entry" if entry["rule"] != rule else "Entry"
        fields.append(leg(label + (" order" if entry["kind"] == "paper order" else " signal"), entry))
    if not entry:
        fields.append("<p>Entry record unavailable</p>")
    if entry and entry.get("entry_rule"):
        fields.append(f"<small>Entry signal from {e(entry['entry_rule'])} · "
                      f"{e(entry.get('source_trade_id', ''))}</small>")
    for order in trade["orders"]:
        if order["action"] == "open":
            label = "Entry order"
        elif order.get("trade_data") is False:
            label = "Administrative close · excluded from trade data"
        else:
            label = "Manager exit" if order.get("management_only") else "Exit order"
        fields.append(leg(label, order))
    if trade["observation"]:
        fields.append(f"<p>{e(trade['observation']['reason'])}</p>")
    if trade["exit"] is None:
        if trade["exit_signal"]:
            fields.append(leg("Exit signal", trade["exit_signal"]))
        fields.append("<p>No exit order recorded</p>")
    if trade["fills"] or any(order.get("pricing") == "native L3" for order in trade["orders"]):
        fields.append("<p><b>Paper fills</b></p>")
        if not trade["fills"]:
            fields.append("<small>No execution recorded</small>")
        for fill in trade["fills"]:
            fields.append(f"<small>{e(fill['action'])} · {e(fill['side'])} {e(fill['quantity'])} @ "
                          f"{price(fill['fill_price'], profile)} {e(profile['price_unit'])} · "
                          f"{e(fill['liquidity'])} · {time_at(fill)} · Order {e(fill['order_id'])}</small>")
    return "".join(fields) + "</details>"


def card(event, profile):
    fields = [
        f"<strong>{e(' · '.join(str(event[k]) for k in ('rule', 'kind', 'action') if event.get(k)))}</strong>",
        f"<p>{e(event.get('reason', ''))}</p>",
    ]
    if event.get("kind") == "fill":
        fields.append(f"<small>{e(event['side'])} {e(event['quantity'])} @ "
                      f"{price(event['fill_price'], profile)} {e(profile['price_unit'])} · {e(event['liquidity'])}</small>")
        fields.append(f"<small>Order {e(event['order_id'])} · Fill {e(event['fill_id'])}</small>")
    if event["kind"] == "paper order" and event.get("placement"):
        detail = f"{e(event['order_side'])} {e(event['quantity'])} · {e(event['placement'])}"
        if event.get("limit_price") is not None:
            detail += f" · Limit {price(event['limit_price'], profile)} {e(profile['price_unit'])}"
        fields.append(f"<small>{detail}</small>")
    if event.get("order_id") and event["kind"] != "fill":
        fields.append(f"<small>Order {e(event['order_id'])}</small>")
    if event.get("order_status"):
        fields.append(f"<small>Order status · {e(event['order_status'])}</small>")
    elif event.get("execution_event") and event["execution_event"] != event.get("reason"):
        fields.append(f"<small>{e(event['execution_event'])}</small>")
    for key, label in (("remaining_quantity", "Order contracts remaining"),
                       ("remaining_contracts", "Trade inventory remaining")):
        if event.get(key) is not None:
            fields.append(f"<small>{label} · {e(event[key])}</small>")
    if event.get("administrative_reason"):
        fields.append(f"<small>{e(event['administrative_reason'])}</small>")
    if event.get("recorded_ns") is not None:
        seconds, nanos = divmod(int(event["recorded_ns"]), 1_000_000_000)
        recorded = datetime.fromtimestamp(seconds, ZoneInfo("America/New_York"))
        fields.append(f"<small>Journal · {recorded:%Y-%m-%d %H:%M:%S}.{nanos:09d} {recorded:%Z}</small>")
    if event.get("entry"):
        fields.append(f"<small>Entry · {prices(event['entry'], profile)} · {e(event['entry']['received'])} ns</small>")
    if "bid" in event or "ask" in event:
        fields.append(f"<small>At decision · {prices(event, profile)}</small>")
    if event.get("print_price") is not None:
        fields.append(
            f"<small>Print {price(event['print_price'], profile)} · Reference print {price(event.get('reference_price'), profile)}"
            f" {e(profile['price_unit'])}</small>"
        )
    is_clock_event = event.get("expiry_references") and event.get("exchange") is None
    time_label = "Window expiry (ns)" if is_clock_event else "Tape receive time (ns)"
    for field, label in [
        ("price_response", "Print response"),
        ("entry_rule", "Opened by"),
        ("trade_id", "Trade"),
        ("received", time_label),
    ]:
        if event.get(field) is not None:
            fields.append(f"<small>{label} · {e(event[field])}</small>")
    return "<article>" + "".join(fields) + "</article>"


def stop_details(state, rule=""):
    if state.get("status") == "active":
        return ""
    fields = []
    source = state.get("last_source") or {}
    end = state.get("capture_end") or {}
    if end:
        fields.append(f"<p>Recorder · {e(end.get('status', 'unknown'))}</p>")
    for label, value in (("Capture connection", end.get("connection_id")),
                         ("Last recorded MBO position", end.get("last_mbo_position")),
                         ("Last processed tape position", source.get("cursor")),
                         ("Last recorder receive time (ns)", end.get("last_received_ns"))):
        if value is not None:
            fields.append(f"<small>{label} · {e(value)}</small>")
    shutdown = state.get("shutdown") or {}
    for owner, trades in shutdown.get("remaining_working_orders", {}).items():
        if rule and rule != owner:
            continue
        for trade_id, tags in trades.items():
            for tag in tags:
                fields.append(f"<p>{e(owner)} · Working order {e(tag)}</p><small>Trade · {e(trade_id)}</small>")
    for issue in shutdown.get("errors", ()):
        if not rule or issue.get("rule") in (None, rule):
            fields.append(f"<p>Shutdown error · {e(issue['stage'])} · {e(issue['error'])}</p>")
            if issue.get("order_id"):
                fields.append(f"<small>{e(issue['rule'])} · Order {e(issue['order_id'])}</small>")
    for owner, positions in (state.get("paper_inventory") or {}).items():
        if rule and rule != owner:
            continue
        for trade_id, qty in positions.items():
            fields.append(f"<p>{e(owner)} · {e(qty)} contracts remaining</p><small>Trade · {e(trade_id)}</small>")
    return "".join(fields)


def run_history(market, rule, runs):
    return '<details><summary>Recent runs</summary><ul>' + "".join(
        '<li><a href="/?' + e(urlencode(dict(market=market, rule=rule, run=identifier))) + '">'
        + (e(day) + " · " if day else "") + e(identifier) + "</a> · " + e(status) + "</li>"
        for identifier, day, status in runs
    ) + "</ul></details>" if runs else ""


def older_events_link(market, rule, run, before):
    if before is None:
        return ""
    query = e(urlencode(dict(market=market, rule=rule, run=run, before=before)))
    return (f'<div><a href="/?{query}" hx-get="/event-history?{query}" '
            'hx-target="closest div" hx-swap="outerHTML">Earlier events</a></div>')


def page(market, rule, state, history=(), *, live=True, run="", trades=(), runs=(), earlier=None, archived_page=False, journal_cursor=None):
    profiles = markets()
    profile = profiles[market]
    rules = catalogue()
    if rule and rule not in profile["rules"]:
        raise ValueError("unknown strategy for this market")

    def options(items, selected):
        return "".join(
            f'<option value="{e(k)}"' + (" selected" if k == selected else "") + f">{e(v)}</option>"
            for k, v in items
        )

    definition = (
        (
            '<details><summary>Rule definition</summary><p class="spec">'
            + e(rules[rule]["specification"])
            + "</p></details>"
        )
        if rule
        else ""
    )
    if not live and not rule:
        definition = "".join(
            "<details><summary>"
            + e(key)
            + '</summary><p class="spec">'
            + e(rules[key]["specification"])
            + "</p></details>"
            for key in profile["rules"]
        )
    history = list(history)
    displayed = state.get("run", "")
    if journal_cursor is None:
        journal_cursor = history[-1]["sequence"] if history else -1
    stream_params = dict(market=market, rule=rule or "", after=journal_cursor)
    if run:
        stream_params["run"] = run
    else:
        stream_params.update(latest=1, displayed=displayed)
    query = urlencode(stream_params)
    latest_url = "/?" + urlencode(dict(market=market, rule=rule))
    return Template(Path(__file__).with_name("monitor.html").read_text(encoding="utf8")).substitute(
        title=e(profile["name"]),
        asset=e(profile["asset"]),
        venue=e(profile["venue"]),
        status=e(state["status"]),
        stop_details=stop_details(state, rule),
        markets=options([(k, v["name"]) for k, v in profiles.items()], market),
        rules=options([("", "All strategies")] + [(k, k) for k in profile["rules"]], rule or ""),
        run=e(run),
        displayed_run=e(displayed or run),
        run_url=e("/?" + urlencode(dict(market=market, rule=rule, run=displayed or run))),
        hide_run="" if displayed or run else "hidden",
        earlier=older_events_link(market, rule, displayed, earlier),
        trade_day=e(state.get("trade_day") or ""),
        latest_link=f'<a href="{e(latest_url)}">Follow latest run</a>' if run and live else "",
        run_history=run_history(market, rule, runs) if live else "",
        hide_filters="" if live else 'style="display:none"',
        definition=definition,
        trade_section=(
            '<section aria-labelledby="trades-title"><h2 id="trades-title">Trades</h2><div id="trades">'
            + "".join(trade_card(trade, profile) for trade in trades)
            + '</div><p class="empty-trades">No trades recorded.</p></section>'
            '<h2>Events</h2>' if live or rule else ""
        ),
        cards="".join(card(row, profile) for row in reversed(history)),
        stream=e("/stream?" + query) if live and not archived_page else "",
    )
