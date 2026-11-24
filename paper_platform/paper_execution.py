"""One native Nautilus L3 matching engine per agent-configured rule."""

from collections import defaultdict
from fractions import Fraction
from threading import Lock

from nautilus_trader.backtest.engine import OrderMatchingEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import BookOrder, OrderBookDelta, TradeTick
from nautilus_trader.model.enums import AccountType, AggressorSide, BookAction, BookType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.enums import liquidity_side_to_str, order_status_to_str
from nautilus_trader.model.events import OrderFilled, OrderSubmitted
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, StrategyId, TradeId, TraderId
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, MarketOrder

from .execution_policy import normalize_order_legs
from .nautilus_book import DBN_TO_NATIVE


class OrderTags:
    """Keep every attempted tag for the life of one paper session."""

    def __init__(self):
        self.seen = set()
        self.lock = Lock()

    def claim(self, tag):
        with self.lock:
            if tag in self.seen:
                return False
            self.seen.add(tag)
            return True


class RuleVenue:
    def __init__(self, rule, instrument, raw_id, emit, tags):
        self.tags = tags
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
        self.active_orders = defaultdict(set)
        self.inventory = defaultdict(int)
        # For the give-back exit only: what each trade's own fills paid and took, and its highest open profit.
        self.cash = defaultdict(int)
        self.peak = {}
        self.exit_attempts = defaultdict(int)
        self.holding = set()
        self.reference = None
        self.received = None
        self.known_received_ns = None
        self.book_quote = {}

    def on_event(self, event):
        order, key, action, administrative_reason = self.orders[str(event.client_order_id)]
        order.apply(event)
        self.cache.update_order(order)
        if order.is_closed:
            self.active_orders[key].discard(str(order.client_order_id))
            if not self.active_orders[key]:
                del self.active_orders[key]
        data = dict(rule=self.rule, trade_id=key, order_id=str(event.client_order_id),
                    action=action, received=self.received, reference=self.reference,
                    trade_data=administrative_reason is None, execution_event=type(event).__name__,
                    order_status=order_status_to_str(order.status), remaining_quantity=int(order.leaves_qty))
        if administrative_reason is not None:
            data["administrative_reason"] = administrative_reason
        if isinstance(event, OrderFilled):
            qty = int(event.last_qty)
            self.inventory[key] += qty if event.order_side == OrderSide.BUY else -qty
            paid = (event.last_px.raw // DBN_TO_NATIVE) * qty
            self.cash[key] += -paid if event.order_side == OrderSide.BUY else paid
            if self.inventory[key]:
                self.holding.add(key)
            else:
                self.holding.discard(key)
                self.peak.pop(key, None)
            self.emit("fill", **data, **self.book_quote, fill_received_ns=self.known_received_ns,
                      fill_id=str(event.trade_id), quantity=qty,
                      fill_price=event.last_px.raw // DBN_TO_NATIVE,
                      side="buy" if event.order_side == OrderSide.BUY else "sell",
                      liquidity=liquidity_side_to_str(event.liquidity_side))
        else:
            self.emit("order status", **data, reason=str(getattr(event, "reason", type(event).__name__)))

    def submit(self, event, placement, quantity, order_id, *, executable=True):
        # A close may cancel orders even if it finds zero filled contracts.
        if not self.tags.claim(order_id):
            # A tag is never sent twice. The refusal is journaled so that it leaves a trace.
            self.emit("order status", **event, order_id=order_id, pricing="native L3", submitted=False,
                      execution_event="order tag already used; not sent")
            return
        style = placement["style"]
        offset = placement["offset_ticks"]
        key, action = event["trade_id"], event["action"]
        self.reference, self.received = event["reference"], event["received"]
        self.known_received_ns = event.get("known_received_ns", self.received)
        self.clock.set_time(self.known_received_ns)
        if action != "open":
            cancellation_error = None
            working = sorted(self.active_orders.get(key, ()))
            for identifier in working:
                try:
                    self.engine.cancel_order(self.orders[identifier][0])
                except Exception as error:
                    if not event.get("administrative_flatten"):
                        raise
                    cancellation_error = error
            if cancellation_error is not None:
                raise cancellation_error
            if event.get("administrative_flatten") and self.active_orders.get(key):
                raise RuntimeError("working paper order still awaiting cancellation")
            held = self.inventory[key]
            quantity = abs(held)
            if quantity and not executable:
                self.emit("order status", **event, order_id=order_id, remaining_contracts=held,
                          pricing="native L3", submitted=False,
                          execution_event="liquidation unavailable after feed interruption")
                return
            if not quantity:
                if working:
                    self.emit("order status", **event, order_id=order_id, remaining_contracts=0,
                              pricing="native L3", submitted=False, execution_event="no filled contracts to close")
                return
            side = OrderSide.SELL if held > 0 else OrderSide.BUY
        else:
            side = OrderSide.BUY if event["side"] == "buy" else OrderSide.SELL
        kwargs = dict(trader_id=self.trader, strategy_id=self.strategy, instrument_id=self.instrument.id,
                      client_order_id=ClientOrderId(order_id), order_side=side, quantity=Quantity.from_int(quantity),
                      ts_init=self.known_received_ns, init_id=UUID4())
        limit = None
        if style in ("post", "cross_limit"):
            buy = side == OrderSide.BUY
            same_side = style == "post"
            field = "bid" if buy == same_side else "ask"
            quote = event[field]
            if quote is None:
                self.emit("order status", **dict(event, reason=f"No {field} for {style} order"),
                          decision_reason=event["reason"], order_id=order_id, pricing="native L3", submitted=False)
                return
            tick_raw, remainder = divmod(self.instrument.price_increment.raw, DBN_TO_NATIVE)
            if remainder or tick_raw <= 0:
                raise ValueError("instrument tick cannot be expressed in DBN units")
            # Posts step away from the opposite side. An IOC limit sets the furthest
            # price the agent permits the matcher to take through the displayed book.
            sign = -1 if buy == same_side else 1
            limit = quote + sign * offset * tick_raw
            order = LimitOrder(**kwargs, price=Price.from_raw(limit * DBN_TO_NATIVE, self.instrument.price_precision),
                               post_only=same_side, time_in_force=TimeInForce.GTC if same_side else TimeInForce.IOC)
        else:
            order = MarketOrder(**kwargs, time_in_force=TimeInForce.IOC)
        administrative_reason = event["reason"] if event.get("trade_data") is False else None
        self.orders[order_id] = (order, key, action, administrative_reason)
        self.active_orders[key].add(order_id)
        self.emit("paper order", **event, order_id=order_id, pricing="native L3",
                  order_side="buy" if side == OrderSide.BUY else "sell", quantity=quantity,
                  placement=style, limit_price=limit, submitted_ns=self.known_received_ns, fill_price=None)
        self.cache.add_order(order)
        order.apply(OrderSubmitted(self.trader, self.strategy, self.instrument.id, order.client_order_id,
                                   self.account, UUID4(), self.known_received_ns, self.known_received_ns))
        self.cache.update_order(order)
        self.engine.process_order(order, self.account)
        if event.get("administrative_flatten") and self.inventory[key]:
            self.emit("order status", **event, order_id=order_id, remaining_contracts=self.inventory[key],
                      execution_event="unfilled administrative remainder")


class PaperExecution:
    def __init__(self, plan, rules, emit, run=None):
        self.run = run
        # Greg, 2026-11-24: "We should try a simple exit strategy. Sell when we give 20% from highest profit
        # value back once we enter trade." A plan that carries {"exit": {"give_back_fraction": "0.2"}} closes
        # each paper trade that way and no longer closes it on its rule's own close. A plan without it is
        # executed as before.
        exit_policy = plan.get("exit")
        self.give_back = None
        if exit_policy is not None:
            if (not isinstance(exit_policy, dict) or "give_back_fraction" not in exit_policy or run is None
                    or exit_policy.keys() - {"give_back_fraction", "also_before_profit"}
                    or type(exit_policy.get("also_before_profit", False)) is not bool):
                raise ValueError("the plan's exit takes give_back_fraction and also_before_profit, "
                                 "and the run's ID for its order tags")
            self.give_back = Fraction(str(exit_policy["give_back_fraction"]))
            # Greg, 2026-11-24: "make same 20% for trades that never get positive".
            self.also_before_profit = exit_policy.get("also_before_profit", False)
            if not 0 < self.give_back < 1:
                raise ValueError("give_back_fraction is a part of the highest profit, above 0 and below 1")
        self.instrument = FuturesContract.from_dict(plan["instrument"])
        self.raw_id = plan["databento_instrument_id"]
        planned = plan["rules"]
        if not planned or not planned.keys() <= rules.keys():
            raise ValueError("execution plan contains missing or unknown strategy IDs")
        if any(not rule.isalnum() for rule in planned):
            # Order tags join the run, the rule and the trade with "-", so a rule ID holds letters and digits only.
            raise ValueError("strategy IDs used in order tags must be letters and digits only")
        self.policies = {rule: normalize_order_legs(rule, policy) for rule, policy in planned.items()}
        self.venues = {}
        self.followers = defaultdict(list)
        self.rules = rules
        tags = OrderTags()
        for rule, policy in self.policies.items():
            parents = policy.get("entry_rules", [])
            if not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents):
                raise ValueError(f"entry_rules must be a list of rule IDs for {rule}")
            if len(set(parents)) != len(parents):
                raise ValueError(f"duplicate entry rule for {rule}")
            if parents and (rules[rule]["role"] != "manager" or
                            policy.get("on_parent_close") not in ("exit", "hold")):
                raise ValueError(f"{rule} needs a manager rule and an explicit on_parent_close instruction")
            for parent in parents:
                if parent not in rules or rules[parent]["role"] == "manager":
                    raise ValueError(f"{rule} needs a catalogue entry rule, got {parent}")
                self.followers[parent].append(rule)
            self.venues[rule] = RuleVenue(rule, self.instrument, self.raw_id, emit, tags)

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
        reference = (msg["source_sha256"], msg["record_index"])
        book_quote = dict(bid=msg["best_bid"], ask=msg["best_ask"], quote_reference=reference, quote_received_ns=received)
        for venue in self.venues.values():
            # These are the quotes on the observed source book, including when a
            # resting paper order fills on a later message than its entry decision.
            venue.book_quote = book_quote
            venue.known_received_ns = received
            venue.reference = reference
            venue.received = received
            venue.clock.set_time(received)
            for update in updates:
                venue.engine.process_order_book_delta(update)
            if trade is not None:
                venue.engine.process_trade_tick(trade)
        if self.give_back is not None:
            self.exit_on_give_back(msg, reference, received)

    def exit_on_give_back(self, msg, reference, received):
        """Close a paper trade once its open profit has fallen back by the plan's fraction of its highest.

        The open profit of a trade is what its own fills paid and took, plus what its contracts would fetch
        now on the side they would be closed at: the best bid for contracts held long, the best ask for
        contracts held short. No mid. It is read at each message, on that message's own book, and the order
        goes at that message's time. Without `also_before_profit` the exit is armed only once the highest open
        profit is above zero, and a trade that never shows a profit is held until the run stops. With it the
        same part is taken off the trade's best open result whatever its sign: a trade whose best was a loss
        of 0.004 is closed when its open result reaches that loss and a fifth more.
        """
        for rule, venue in self.venues.items():
            for key in sorted(venue.holding):
                held = venue.inventory[key]
                quote = msg["best_bid"] if held > 0 else msg["best_ask"]
                if quote is None or venue.active_orders.get(key):
                    continue
                profit = venue.cash[key] + held * quote
                peak = max(venue.peak.get(key, profit), profit)
                venue.peak[key] = peak
                if peak <= 0 and not self.also_before_profit:
                    continue
                if profit >= peak or profit > peak - self.give_back * abs(peak):
                    continue
                venue.exit_attempts[key] += 1
                event = dict(rule=rule, trade_id=key, action="close", received=received, reference=reference,
                             bid=msg["best_bid"], ask=msg["best_ask"], side="buy" if held > 0 else "sell",
                             exit_policy="give back", highest_open_profit=peak, open_profit=profit,
                             reason=(f"gave back {float(self.give_back):.0%} of its highest open profit" if peak > 0
                                     else f"fell {float(self.give_back):.0%} below its best open result, never in profit"))
                venue.submit(event, {"style": "cross", "offset_ticks": 0}, abs(held),
                             f"{self.run}-giveback-{rule}-{key}-{venue.exit_attempts[key]}")

    def instructions(self, signal):
        """Route the originating signal and any independently configured manager orders."""
        parent = signal["rule"]
        if (self.give_back is not None and signal["action"] in ("close", "exit")
                and signal.get("trade_data") is not False and signal["reason"] != "trade day ended"
                and not signal.get("answer_id")):
            # Under the give-back exit a rule's own close stays a research signal in the journal and sends no
            # order: the paper trade is closed by the give-back. The end of the trade day and every
            # administrative close still close it. So does a close taken in from an answer (answer_id).
            return
        if parent in self.venues:
            if signal["action"] == "exit":
                # The research signal remains an exit indication. The actual order
                # closes only contracts this manager filled in its own venue.
                yield dict(signal, action="close", signal_action="exit")
            else:
                yield signal
        for manager in self.followers.get(parent, ()):
            if signal["side"] != self.rules[manager]["direction"]:
                continue
            action = signal["action"]
            if action == "close" and self.policies[manager]["on_parent_close"] == "hold":
                continue
            if action not in ("open", "close"):
                continue
            yield dict(signal, rule=manager, entry_rule=parent, source_trade_id=signal["trade_id"],
                       trade_id=f"{signal['trade_id']}/{manager}", management_only=True,
                       reason=f"{parent}: {signal['reason']}")

    def record_interruption(self, signal):
        """Keep the native exposure identifiable when a parent's research observation ends."""
        parent, key = signal["rule"], signal["trade_id"]
        positions = [(parent, key)]
        positions.extend((manager, f"{key}/{manager}") for manager in self.followers.get(parent, ())
                         if self.rules[manager]["direction"] == signal["side"])
        for rule, trade in positions:
            venue = self.venues.get(rule)
            if venue is None:
                continue
            held = venue.inventory.get(trade, 0)
            working = sorted(venue.active_orders.get(trade, ()))
            if not held and not working:
                continue
            details = dict(signal, rule=rule, trade_id=trade,
                           remaining_contracts=held, working_order_ids=working)
            if rule != parent:
                details.update(entry_rule=parent, source_trade_id=key, management_only=True)
            venue.emit("order status", **details, execution_event="observation ended with paper exposure")
            for identifier in working:
                # The observation has ended, so an order of it that is still resting is withdrawn. Left
                # working it could fill later with no rule watching it. The cancel carries the interruption's
                # reason and is not trade data. Contracts already held are left as recorded above.
                order, order_key, action, _ = venue.orders[identifier]
                venue.orders[identifier] = (order, order_key, action, signal["reason"])
                venue.engine.cancel_order(order)

    def submit(self, event, order_id):
        rule = event["rule"]
        policy = self.policies[rule]
        style = policy["open" if event["action"] == "open" else "close"]
        if event.get("trade_data") is False:
            style = {"style": "cross", "offset_ticks": 0}
        self.venues[rule].submit(event, style, policy["contracts"], order_id)

    def working_orders(self):
        return {rule: {key: sorted(tags) for key, tags in venue.active_orders.items() if tags}
                for rule, venue in self.venues.items() if venue.active_orders}

    def flatten(self, last, reason, run, *, executable=True, close_signals=None):
        errors = []
        for rule, venue in self.venues.items():
            keys = dict.fromkeys(venue.active_orders)
            keys.update((key, None) for key, held in venue.inventory.items() if held)
            for key in keys:
                identifier = f"{run}-flatten-{rule}-{key}"
                event = dict((close_signals or {}).get((rule, key), {}),
                             rule=rule, trade_id=key, action="close", trade_data=False,
                             administrative_flatten=True, reason=reason, received=last["raw"]["ts_recv"],
                             reference=(last["source_sha256"], last["record_index"]),
                             bid=last["best_bid"], ask=last["best_ask"])
                try:
                    venue.submit(event, {"style": "cross", "offset_ticks": 0}, 0, identifier, executable=executable)
                except Exception as error:
                    # The tag has been consumed. Continue with independent trades and
                    # retain the exact outstanding orders and inventory in the receipt.
                    errors.append(dict(stage="liquidation", rule=rule, trade_id=key, order_id=identifier,
                                       error=type(error).__name__))
        return errors
