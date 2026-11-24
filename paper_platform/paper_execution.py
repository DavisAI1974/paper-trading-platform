"""One native Nautilus L3 matching engine per agent-configured rule."""

from collections import defaultdict

from nautilus_trader.backtest.engine import OrderMatchingEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import BookOrder, OrderBookDelta, TradeTick
from nautilus_trader.model.enums import AccountType, AggressorSide, BookAction, BookType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, OrderSubmitted
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, StrategyId, TradeId, TraderId
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, MarketOrder

from .nautilus_book import DBN_TO_NATIVE


class RuleVenue:
    def __init__(self, rule, instrument, raw_id, emit):
        self.rule = rule
        self.instrument = instrument
        self.clock = TestClock()
        self.trader = TraderId("PAPER-001")
        self.strategy = StrategyId(rule + "-001")
        self.account = AccountId(str(instrument.id.venue) + "-PAPER")
        self.bus = MessageBus(trader_id=self.trader, clock=self.clock)
        self.cache = Cache()
        self.cache.add_instrument(instrument)
        self.engine = OrderMatchingEngine(
            instrument=instrument, raw_id=raw_id, fill_model=FillModel(), fee_model=MakerTakerFeeModel(),
            book_type=BookType.L3_MBO, oms_type=OmsType.HEDGING, account_type=AccountType.MARGIN,
            trade_execution=True, bar_execution=False, queue_position=True, liquidity_consumption=True,
            cache=self.cache, clock=self.clock, msgbus=self.bus,
        )
        self.bus.register("ExecEngine.process", self.on_event)
        self.emit = emit
        self.orders = {}
        self.inventory = defaultdict(int)
        self.reference = None
        self.received = None

    def on_event(self, event):
        order, key, action, administrative = self.orders[str(event.client_order_id)]
        order.apply(event)
        self.cache.update_order(order)
        data = dict(rule=self.rule, trade_id=key, order_id=str(event.client_order_id),
                    action=action, received=self.received, reference=self.reference,
                    trade_data=not administrative, execution_event=type(event).__name__)
        if isinstance(event, OrderFilled):
            qty = int(event.last_qty)
            self.inventory[key] += qty if event.order_side == OrderSide.BUY else -qty
            self.emit("fill", **data, fill_id=str(event.trade_id), quantity=qty,
                      fill_price=event.last_px.raw // DBN_TO_NATIVE,
                      side="buy" if event.order_side == OrderSide.BUY else "sell",
                      liquidity=event.liquidity_side.name)
        else:
            self.emit("order status", **data, reason=str(getattr(event, "reason", type(event).__name__)))

    def submit(self, event, style, quantity, order_id):
        key, action = event["trade_id"], event["action"]
        self.reference, self.received = event["reference"], event["received"]
        self.clock.set_time(self.received)
        if action != "open":
            for order, parent, _, _ in tuple(self.orders.values()):
                if parent == key and order.is_open:
                    self.engine.cancel_order(order)
            held = self.inventory[key]
            quantity = abs(held)
            if not quantity:
                return
            side = OrderSide.SELL if held > 0 else OrderSide.BUY
        else:
            side = OrderSide.BUY if event["side"] == "buy" else OrderSide.SELL
        kwargs = dict(trader_id=self.trader, strategy_id=self.strategy, instrument_id=self.instrument.id,
                      client_order_id=ClientOrderId(order_id), order_side=side, quantity=Quantity.from_int(quantity),
                      ts_init=self.received, init_id=UUID4())
        if style == "post":
            quote = event["bid" if side == OrderSide.BUY else "ask"]
            if quote is None:
                self.emit("order status", rule=self.rule, trade_id=key, order_id=order_id, action=action,
                          received=self.received, reference=self.reference, reason="No same-side quote to post")
                return
            order = LimitOrder(**kwargs, price=Price.from_raw(quote * DBN_TO_NATIVE, self.instrument.price_precision),
                               post_only=True)
        else:
            order = MarketOrder(**kwargs, time_in_force=TimeInForce.IOC)
        self.orders[order_id] = (order, key, action, event.get("trade_data") is False)
        self.cache.add_order(order)
        order.apply(OrderSubmitted(self.trader, self.strategy, self.instrument.id, order.client_order_id,
                                   self.account, UUID4(), self.received, self.received))
        self.cache.update_order(order)
        self.engine.process_order(order, self.account)


class PaperExecution:
    def __init__(self, plan, rules, emit):
        self.instrument = FuturesContract.from_dict(plan["instrument"])
        self.raw_id = plan["databento_instrument_id"]
        self.policies = plan["rules"]
        if not self.policies or not self.policies.keys() <= rules.keys():
            raise ValueError("execution plan contains missing or unknown strategy IDs")
        self.venues = {}
        for rule, policy in self.policies.items():
            if (type(policy["contracts"]) is not int or policy["contracts"] <= 0
                    or any(policy[k] not in ("post", "cross") for k in ("open", "close"))):
                raise ValueError(f"invalid contracts or placement for {rule}")
            self.venues[rule] = RuleVenue(rule, self.instrument, self.raw_id, emit)

    def market(self, msg):
        if msg["period"] == "vendor snapshot":
            return
        raw = msg["raw"]
        if raw["instrument_id"] != self.raw_id:
            raise ValueError("agent execution instrument differs from purchased MBO")
        iid = self.instrument.id
        stamp, received = raw["ts_event"], raw["ts_recv"]
        def price(value):
            return Price.from_raw(value * DBN_TO_NATIVE, self.instrument.price_precision)
        updates = []
        changes = msg["order_change"]
        old, new = changes["old"], changes["new"]
        def delta(action, row):
            order = None
            if row is not None:
                px, qty, side, _ = row
                order = BookOrder(OrderSide.BUY if side == "B" else OrderSide.SELL,
                                  price(px), Quantity.from_int(qty), raw["order_id"])
            updates.append(OrderBookDelta(iid, action, order, raw["flags"], raw["sequence"], stamp, received))
        if raw["action"] == "R":
            delta(BookAction.CLEAR, None)
        elif old != new:
            replaced = old and new and (old[0] != new[0] or old[2:] != new[2:])
            if old and (not new or replaced):
                delta(BookAction.DELETE, old)
            if new:
                delta(BookAction.ADD if not old or replaced else BookAction.UPDATE, new)
        trade = None
        if raw["action"] == "T" and raw["side"] in ("A","B") and raw["size"] > 0:
            aggressor = AggressorSide.BUYER if raw["side"] == "B" else AggressorSide.SELLER
            trade = TradeTick(iid, price(raw["price"]), Quantity.from_int(raw["size"]), aggressor,
                              TradeId(str(msg["cursor"])), stamp, received)
        for venue in self.venues.values():
            venue.reference = (msg["source_sha256"], msg["record_index"])
            venue.received = received
            venue.clock.set_time(received)
            for update in updates:
                venue.engine.process_order_book_delta(update)
            if trade is not None:
                venue.engine.process_trade_tick(trade)

    def submit(self, event, order_id):
        rule = event["rule"]
        if rule not in self.venues:
            return
        action = event["action"]
        style = self.policies[rule]["open" if action == "open" else "close"]
        if event.get("trade_data") is False:
            style = "cross"
        self.venues[rule].submit(event, style, self.policies[rule]["contracts"], order_id)

    def flatten(self, last, reason, run):
        for rule, venue in self.venues.items():
            for order, _, _, _ in tuple(venue.orders.values()):
                if order.is_open:
                    venue.engine.cancel_order(order)
            for key, held in list(venue.inventory.items()):
                if held:
                    identifier = f"{run}-flatten-{rule}-{key}"
                    event = dict(rule=rule, trade_id=key, action="close", trade_data=False, reason=reason,
                                 received=last["raw"]["ts_recv"], reference=(last["source_sha256"], last["record_index"]),
                                 bid=last["best_bid"], ask=last["best_ask"])
                    venue.emit("paper order", **event, order_id=identifier, pricing="native L3")
                    venue.submit(event, "cross", abs(held), identifier)
                    if venue.inventory[key]:
                        venue.emit("status", **event, remaining_contracts=venue.inventory[key],
                                   execution_event="unfilled administrative remainder")
