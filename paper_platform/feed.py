"""Deliver every original DBN record in source order, including vendor restatements."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import struct
import time

from root import tape
from root.book import ASK, BID, compare_with_restatement
from root.calendar import clock_days_needed, label, trade_day_bounds
from .market import replay_market
from .nautilus_book import NautilusMboBook


class MboTape:
    """One weekly book and tape cursor, shared by archive and capture readers."""

    def __init__(self, trade_day, instrument_id):
        self.book = NautilusMboBook(instrument_id)
        self.flags = tape.flag_bits()
        self.position = self.event_start = self.cursor = 0
        self.opens, self.halts = trade_day_bounds(trade_day)
        self.utc_day = None

    def boundary(self, fields, source, index):
        day = fields["ts_recv"] // 86_400_000_000_000
        if day == self.utc_day:
            return None
        digest = hashlib.sha256()
        for side in (BID, ASK):
            levels = self.book.sides[side]
            for price in levels.prices:
                for order in levels.lines[price].orders.values():
                    digest.update(struct.pack("<cqQIQ", side, price, order.id, order.size, order.placed_pos))
        self.utc_day = day
        return dict(utc_date=datetime.fromtimestamp(day * 86400, timezone.utc).date().isoformat(),
                    position=self.position, source=[source, index], instrument_id=fields["instrument_id"],
                    exchange_action=fields["action"].decode("ascii"), orders_before=len(self.book.orders),
                    queue_sha256_before=digest.hexdigest())

    def apply(self, fields, *, source_sha256, index, local_ns, snapshot=False):
        exchange, received = fields["ts_event"], fields["ts_recv"]
        eligible = not snapshot and self.opens <= exchange < self.halts
        order_change = None
        entered_levels = {}
        boundary = None
        if snapshot:
            period, named = tape.VENDOR_SNAPSHOT, None
            tick_position = group = None
        else:
            boundary = self.boundary(fields, source_sha256, index)
            if boundary is not None and boundary["position"]:
                # The native mirror is checked against the carried book at every UTC date change. A captured
                # or live stream brings no vendor restatement to check it against, so this is its only check.
                self.book.verify_queues(f"UTC {boundary['utc_date']}")
            period, named = label(exchange)
            tick_position, group = self.position, self.event_start
            oid = fields["order_id"]
            prior = self.book.orders.get(oid)
            old = (prior.price, prior.size, prior.side.decode(), prior.entered_pos) if prior else None
            filled = self.book.filled_in_event.get(oid, 0)
            before = (self.book.best(BID), self.book.best(ASK))
            notes = self.book.apply(
                fields["action"],
                fields["side"],
                fields["price"],
                fields["size"],
                fields["order_id"],
                fields["flags"],
                exchange,
                received,
                fields["sequence"],
                self.position,
                carried=bool(fields["flags"] & self.flags["F_SNAPSHOT"]),
                written=eligible,
            )
            after = self.book.orders.get(oid)
            new = (after.price, after.size, after.side.decode(), after.entered_pos) if after else None
            order_change = dict(old=old, new=new, filled_in_event=filled, notes=notes)
            for side, was in zip((BID, ASK), before):
                best = self.book.best(side)
                if best is not None and best != was:
                    entered_levels[side.decode()] = tuple(self.book.sides[side].lines[best].orders)
            self.position += 1
            if fields["flags"] & self.flags["F_LAST"]:
                self.event_start = self.position
        fields["action"] = fields["action"].decode("ascii")
        fields["side"] = fields["side"].decode("ascii")
        message = dict(
            cursor=self.cursor,
            source_sha256=source_sha256,
            record_index=index,
            raw=fields,
            received_by_replay_ns=local_ns,
            period=period,
            trade_day=named.isoformat() if named else None,
            in_session=eligible,
            position_on_tape=tick_position,
            event_start_position=group,
            order_change=order_change,
            entered_levels=entered_levels,
            best_bid=self.book.best(BID),
            best_ask=self.book.best(ASK),
        )
        self.cursor += 1
        if boundary is not None:
            message["weekly_book_boundary"] = boundary
        return message


class HistoricalFeed:
    """One fresh book per session. Replay sessions seat it from the exchange's Sunday clear."""

    def __init__(self, folder: Path, trade_day: date, market_key="nymex-ng"):
        self.trade_day = trade_day
        profile = replay_market(market_key)
        self.week = tape.read_week(folder, trade_day)
        manifest = tape.TapeFolder(folder)
        for day in clock_days_needed(trade_day):
            entry = manifest.entry(day)
            if (entry["schema"], entry["dataset"], entry["symbol"]) != (
                profile["schema"],
                profile["source"],
                profile["symbol"],
            ):
                raise ValueError(f"{day}: archive does not match market profile {market_key}")
            if entry.get("expected_records", entry["records"]) != entry["records"]:
                raise ValueError(f"{day}: archive record counts disagree")
        self.sources = [
            dict(
                file=f.name,
                sha256=f.sha256,
                records=len(f.records),
                utc_file_date=f.day.isoformat(),
                instrument_ids=f.instrument_ids,
            )
            for f in self.week
        ]

    def messages(self, *, paced=False):
        """Fresh independent iterator. Wall time affects delivery pace only, never signal eligibility."""
        stream = MboTape(self.trade_day, self.week[0].instrument_ids[0])
        opens, halts = stream.opens, stream.halts
        clock_origin = tape_origin = None
        for file in self.week:
            if file.vendor_snapshot:
                comparison = compare_with_restatement(stream.book, file.records[: file.vendor_snapshot])
                if not comparison["equal"]:
                    raise ValueError(f"{file.name}: carried book differs from opening restatement")
                stream.book.verify_queues(file.name)
            for index, record in enumerate(file.records):
                fields = {k: record[k].item() for k in record.dtype.names}
                exchange = fields["ts_event"]
                received = fields["ts_recv"]
                snapshot = index < file.vendor_snapshot
                eligible = not snapshot and opens <= exchange < halts
                if paced and eligible:
                    if clock_origin is None:
                        clock_origin = time.monotonic_ns()
                        tape_origin = received
                    wait = (clock_origin + received - tape_origin - time.monotonic_ns()) / 1e9
                    if wait > 0:
                        time.sleep(wait)
                yield stream.apply(fields, source_sha256=file.sha256, index=index,
                                   local_ns=time.time_ns(), snapshot=snapshot)
