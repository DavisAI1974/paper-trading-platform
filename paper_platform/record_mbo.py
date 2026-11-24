"""Manually gated capture of NG DBN deliveries. This command only records market data."""

import argparse
from datetime import date, datetime, time as wall_time, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

import databento as db
from databento_dbn import ErrorMsg, F_SNAPSHOT, RType, SystemCode, SystemMsg
import zstandard

from root.calendar import clock_days_needed, week_sunday
from root.tape import TapeFolder

from .capture import CaptureFailure, capture_writer
from .market import replay_market
from .run import notify
from .run_clock import LIMIT_SECONDS, run_start_ns
from .session import git


# The gateway sends a heartbeat while market data is quiet. Databento allows
# ten seconds beyond the negotiated interval before treating a connection as hung.
HEARTBEAT_SECONDS = 30
HEARTBEAT_GRACE_SECONDS = 10


class Sink:
    """Databento writes the DBN metadata followed by one encoded record per call."""

    closed = False

    def __init__(self, writer, message_budget, started):
        self.writer = writer
        self.message_budget = message_budget
        self.deadline = started + LIMIT_SECONDS
        self.records = 0
        self.last_delivery_at = started
        self.done = threading.Event()
        self.reason = None
        self.error = None

    def writable(self):
        return True

    def flush(self):
        pass  # Every accepted frame is already committed.

    def stop(self, reason):
        if not self.done.is_set():
            self.reason = reason
            self.done.set()

    def failed(self, error):
        self.error = error
        self.stop("capture failure")

    def write(self, data, *, snapshot=False):
        received_ns = time.time_ns()
        self.last_delivery_at = time.monotonic()
        if self.last_delivery_at >= self.deadline:
            self.stop("clock limit")
        if self.done.is_set():
            return len(data)
        if data.startswith(b"DBN"):
            kind = "metadata"
        else:
            if len(data) < 16 or len(data) != data[0] * 4:
                raise ValueError("incomplete DBN record")
            kind = "mbo" if data[1] == int(RType.MBO) else "control"
        self.writer.append(data, received_ns, kind, snapshot=snapshot)
        if kind != "metadata":
            self.records += 1
            if self.records == self.message_budget:
                self.stop("message budget reached")
        return len(data)


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise EOFError("incomplete purchased DBN frame")
    return data


def purchased(data_root, trade_day, profile, sink):
    archive = data_root / profile["archive"]
    manifest = TapeFolder(archive)
    for file_number, day in enumerate(clock_days_needed(trade_day)):
        if sink.done.is_set():
            break
        entry = manifest.entry(day)
        if (entry["schema"], entry["dataset"], entry["symbol"]) != (
            profile["schema"], profile["source"], profile["symbol"]
        ):
            raise ValueError("purchased file does not match the selected market")
        with (archive / entry["file"]).open("rb") as compressed:
            if hashlib.file_digest(compressed, "sha256").hexdigest() != entry["sha256"]:
                raise ValueError("purchased file differs from its source manifest")
            compressed.seek(0)
            with zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
                header = read_exact(stream, 8)
                if header[:3] != b"DBN":
                    raise ValueError("purchased file has no DBN metadata")
                sink.write(header + read_exact(stream, int.from_bytes(header[4:8], "little")))
                leading_snapshot = file_number > 0
                count = 0
                while not sink.done.is_set():
                    length = stream.read(1)
                    if not length:
                        if count != entry["records"]:
                            raise ValueError("incomplete purchased file")
                        break
                    size = length[0] * 4
                    if size < 16:
                        raise ValueError("invalid purchased DBN frame length")
                    frame = length + read_exact(stream, size - 1)
                    if frame[1] == int(RType.MBO):
                        leading_snapshot = leading_snapshot and bool(frame[36] & F_SNAPSHOT)
                    sink.write(frame, snapshot=leading_snapshot)
                    count += 1


def live(trade_day, profile, sink, contract_file=None):
    if not os.environ.get("DATABENTO_API_KEY", "").strip():
        raise ValueError("the live data entitlement requires DATABENTO_API_KEY")
    sunday = week_sunday(trade_day)
    client = db.Live(ts_out=False, reconnect_policy="none", heartbeat_interval_s=HEARTBEAT_SECONDS)
    client.add_stream(sink, exception_callback=sink.failed)
    from .live_contract import LiveContract
    contract = LiveContract(contract_file, profile["source"]) if contract_file else None
    replays_remaining = 2 if contract else 1

    def control(record):
        nonlocal replays_remaining
        if isinstance(record, ErrorMsg):
            sink.failed(CaptureFailure(f"Databento feed error {int(record.code)}"))
        elif isinstance(record, SystemMsg) and record.code == SystemCode.REPLAY_COMPLETED:
            replays_remaining -= 1
            if replays_remaining != 0:
                return
            if contract:
                contract.publish()
            with sink.writer.db:
                sink.writer.db.execute("INSERT OR IGNORE INTO live_starts VALUES(?,?)",
                                       (sink.writer.connection_id, sink.writer.position))
        elif contract:
            contract.observe(record)

    client.add_callback(control, exception_callback=sink.failed)
    connected = False
    try:
        client.subscribe(dataset=profile["source"], schema=profile["schema"], symbols=profile["symbol"],
                         stype_in="continuous", start=datetime.combine(sunday, wall_time(), timezone.utc))
        connected = True
        if contract:
            client.subscribe(dataset=profile["source"], schema="definition", symbols=profile["symbol"],
                             stype_in="continuous", start=0)
        subscribed_at = time.monotonic()
        client.start()
        while not sink.done.wait(0.1):
            if not client.is_connected():
                raise CaptureFailure("live feed disconnected")
            now = time.monotonic()
            if now - max(subscribed_at, sink.last_delivery_at) > HEARTBEAT_SECONDS + HEARTBEAT_GRACE_SECONDS:
                raise CaptureFailure("live feed heartbeat timed out")
            if now >= sink.deadline:
                sink.stop("clock limit")
        if sink.error is not None:
            if isinstance(sink.error, CaptureFailure):
                raise sink.error
            raise CaptureFailure("live DBN write failed") from sink.error
    except CaptureFailure as error:
        sink.failed(error)
        raise
    finally:
        if connected:
            try:
                if client.is_connected():
                    client.stop()
            finally:
                try:
                    client.block_for_close(timeout=10)
                except Exception as error:
                    if isinstance(sink.error, CaptureFailure):
                        raise sink.error from error
                    raise CaptureFailure("live feed closed with an error") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--trade-day", type=date.fromisoformat, required=True)
    parser.add_argument("--source", choices=("purchased", "live"), required=True)
    parser.add_argument("--message-budget", type=int, required=True)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--live-contract-file", type=Path)
    parser.add_argument("--greg-go", action="store_true")
    args = parser.parse_args()
    if not args.greg_go or args.message_budget <= 0:
        parser.error("capture requires Greg's go and a positive delivery budget")
    started = run_start_ns() / 1_000_000_000
    data_root = args.data.resolve()
    remote = git(data_root, "remote", "get-url", "origin").lower().removesuffix(".git")
    if remote not in ("https://github.com/davisai1974/markets-data", "git@github.com:davisai1974/markets-data"):
        parser.error("capture output belongs in DavisAI1974/markets-data")
    profile = replay_market("nymex-ng")
    sunday = week_sunday(args.trade_day).isoformat()
    path = data_root / "paper-trading" / "captures" / profile["id"] / sunday / (args.source + ".sqlite")
    identity = json.dumps(dict(market=profile["id"], dataset=profile["source"], symbol=profile["symbol"],
                               sunday=sunday, source=args.source), sort_keys=True)
    if not all(os.environ.get(key) for key in ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY")):
        parser.error("capture requires the configured phone stop and failure notices")
    outcome = "capture failure"
    try:
        if time.monotonic() >= started + LIMIT_SECONDS:
            outcome = "clock limit"
            return
        with capture_writer(path, identity) as writer:
            sink = Sink(writer, args.message_budget, started)
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: sink.stop("clock limit" if time.monotonic() >= sink.deadline else "capture stopped"))
            signal.signal(signal.SIGUSR1, lambda *_: sink.failed(CaptureFailure("capture checkpoint publication failed")))
            if args.ready_file:
                pending = args.ready_file.with_suffix(".pending")
                pending.write_text(str(writer.connection_id), encoding="ascii")
                pending.replace(args.ready_file)
            if args.source == "purchased":
                purchased(data_root, args.trade_day, profile, sink)
            else:
                live(args.trade_day, profile, sink, args.live_contract_file)
            writer.status = sink.reason or "end of purchased input"
        outcome = writer.status
    except CaptureFailure as exc:
        outcome = str(exc)
        raise
    finally:
        notify(outcome, f"{profile['id']}/{sunday}/{args.source}", activity="MBO capture")


if __name__ == "__main__":
    main()
