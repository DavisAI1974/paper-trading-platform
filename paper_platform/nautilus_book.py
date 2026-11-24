"""Nautilus L3 book for purchased NG MBO, with ROOT's order provenance and queue rules."""

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.enums import BookType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import FIXED_PRECISION, Price, Quantity

from root import tape
from root.book import ASK, BID, Book


# DBN always represents price in billionths. Nautilus builds can use a wider fixed-point type.
DBN_TO_NATIVE = 10 ** (FIXED_PRECISION - 9)


class NautilusMboBook(Book):
    """Apply each accepted exchange mutation to the native book at its original timestamp."""

    def __init__(self, instrument_id):
        super().__init__()
        self.native = OrderBook(InstrumentId.from_str(f"{instrument_id}.NYMEX"), BookType.L3_MBO)
        self._event_ns = 0

    def apply(self, action, side, price, size, order_id, flags, ts_event, ts_recv, sequence, pos,
              carried=False, written=True):
        self._event_ns = ts_event
        if action == tape.CLEAR:
            self.native.clear(ts_event, sequence)
        return super().apply(action, side, price, size, order_id, flags, ts_event, ts_recv, sequence,
                             pos, carried=carried, written=written)

    @staticmethod
    def _native_order(order):
        return BookOrder(
            OrderSide.BUY if order.side == BID else OrderSide.SELL,
            Price.from_raw(order.price * DBN_TO_NATIVE, 9),
            Quantity.from_int(order.size),
            order.id,
        )

    # ROOT calls leave then join on a price change or size increase, and resize on
    # a reduction. A fill is descriptive; its exchange cancel or modify carries the size change.
    def _join(self, order):
        super()._join(order)
        self.native.add(self._native_order(order), self._event_ns, sequence=self.last_sequence)

    def _leave(self, order):
        self.native.delete(self._native_order(order), self._event_ns, sequence=self.last_sequence)
        super()._leave(order)

    def _resize(self, order, size):
        super()._resize(order, size)
        self.native.update(self._native_order(order), self._event_ns, sequence=self.last_sequence)

    def best(self, side):
        price = self.native.best_bid_price() if side == BID else self.native.best_ask_price()
        return None if price is None else price.raw // DBN_TO_NATIVE

    def verify_queues(self, source):
        """Check native IDs, remaining quantities and FIFO after ROOT matches the vendor restatement."""
        for side, levels in ((BID, self.native.bids()), (ASK, self.native.asks())):
            expected = self.sides[side].lines
            if len(levels) != len(expected):
                raise ValueError(f"{source}: Nautilus level count differs from the carried book")
            for level in levels:
                price = level.price.raw // DBN_TO_NATIVE
                line = expected.get(price)
                actual = [(order.order_id, int(order.size)) for order in level.orders()]
                if line is None or actual != [(order.id, order.size) for order in line.orders.values()]:
                    raise ValueError(f"{source}: Nautilus order queue differs at {side!r} {price}")
