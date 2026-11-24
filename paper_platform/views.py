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
    def leg(label, order):
        seconds, nanos = divmod(int(order["received"]), 1_000_000_000)
        moment = datetime.fromtimestamp(seconds, ZoneInfo("America/New_York"))
        at = f"{moment:%Y-%m-%d %H:%M:%S}.{nanos:09d} {moment:%Z}"
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

    entry, exit_order = trade["entry"], trade["exit"]
    fields = [f'<article id="trade-{e(trade["trade_id"])}"><strong>{e(trade["rule"])} · '
              f'{e((entry or exit_order).get("side", ""))}</strong>']
    fields.append(leg("Entry order", entry) if entry else "<p>Entry record unavailable</p>")
    if entry and entry.get("entry_rule"):
        fields.append(f"<small>Entry signal from {e(entry['entry_rule'])} · "
                      f"{e(entry.get('source_trade_id', ''))}</small>")
    if trade["observation"]:
        fields.append(f"<p>{e(trade['observation']['reason'])}</p>")
    if exit_order is None:
        fields.append("<p>No exit order recorded</p>")
    else:
        label = "Exit order"
        if exit_order.get("trade_data") is False:
            label = "Administrative close · excluded from trade data"
        elif exit_order.get("management_only"):
            label = "Manager exit"
        fields.append(leg(label, exit_order))
    if entry and entry.get("pricing") == "native L3":
        fields.append("<p><b>Paper fills</b></p>")
        if not trade["fills"]:
            fields.append("<small>No execution recorded</small>")
        for fill in trade["fills"]:
            fields.append(f"<small>{e(fill['action'])} · {e(fill['side'])} {e(fill['quantity'])} @ "
                          f"{price(fill['fill_price'], profile)} {e(profile['price_unit'])} · "
                          f"{e(fill['liquidity'])} · {e(fill['received'])} ns</small>")
    return "".join(fields) + "</article>"


def card(event, profile):
    fields = [
        f"<strong>{e(' · '.join(str(event[k]) for k in ('rule', 'kind', 'action') if event.get(k)))}</strong>",
        f"<p>{e(event.get('reason', ''))}</p>",
    ]
    if event.get("kind") == "fill":
        fields.append(f"<small>{e(event['side'])} {e(event['quantity'])} @ "
                      f"{price(event['fill_price'], profile)} {e(profile['price_unit'])} · {e(event['liquidity'])}</small>")
        fields.append(f"<small>Order {e(event['order_id'])} · Fill {e(event['fill_id'])}</small>")
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


def page(market, rule, state, history=(), *, live=True, run="", trades=()):
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
    query = urlencode(
        dict(
            market=market,
            rule=rule or "",
            after=history[-1]["sequence"] if history else -1,
            run=run or state.get("run", ""),
        )
    )
    return Template(Path(__file__).with_name("monitor.html").read_text(encoding="utf8")).substitute(
        title=e(profile["name"]),
        asset=e(profile["asset"]),
        venue=e(profile["venue"]),
        status=e(state["status"]),
        markets=options([(k, v["name"]) for k, v in profiles.items()], market),
        rules=options([("", "All strategies")] + [(k, k) for k in profile["rules"]], rule or ""),
        run=e(run),
        hide_filters="" if live else 'style="display:none"',
        definition=definition,
        trade_section=(
            '<section aria-labelledby="trades-title"><h2 id="trades-title">Trades</h2><div id="trades">'
            + "".join(trade_card(trade, profile) for trade in trades)
            + '</div><p class="empty-trades">No trades recorded for this strategy.</p></section>'
            '<h2>Events</h2>' if rule else ""
        ),
        cards="".join(card(row, profile) for row in reversed(history)),
        stream=e("/stream?" + query) if live else "",
    )
