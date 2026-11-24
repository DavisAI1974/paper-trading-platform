"""Durable DBN delivery journal and ordered MBO history for one market week."""

from contextlib import closing, contextmanager
from datetime import datetime, time as wall_time, timedelta, timezone
import json
import fcntl
import hashlib
from itertools import islice
from pathlib import Path
import sqlite3
import struct
import time

from root import tape
from root.calendar import clock_days_needed, to_ns, week_sunday

from .feed import MboTape


# Databento DBN RecordHeader followed by the MBO payload, with ts_out disabled.
MBO = struct.Struct("<BBHIQQqIBBccQiI")
MBO_FIELDS = ("length", "rtype", "publisher_id", "instrument_id", "ts_event", "order_id",
              "price", "size", "flags", "channel_id", "action", "side", "ts_recv",
              "ts_in_delta", "sequence")


class CaptureFailure(RuntimeError):
    """A recorder stop reason suitable for operator notices and durable metadata."""


class CaptureEnded(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("capture: " + evidence["status"])


class Capture:
    def __init__(self, db, connection_id):
        self.db = db
        self.connection_id = connection_id
        self.position = 0
        self.prior = db.execute("SELECT COUNT(*) FROM frames WHERE position IS NOT NULL").fetchone()[0]
        self.deliveries = 0
        self.status = "closed"

    def append(self, body, received_ns, kind, *, snapshot=False):
        """Commit the delivery and any new tape position in the same transaction."""
        pos = replay = None
        mismatch = False
        if kind == "mbo" and not snapshot:
            if self.position < self.prior:
                original, = self.db.execute("SELECT body FROM frames WHERE position=?", (self.position,)).fetchone()
                mismatch = original != body
                if not mismatch:
                    replay = self.position
            else:
                pos = self.position
        with self.db:
            self.db.execute(
                "INSERT INTO frames(connection_id,received_ns,kind,body,position,replay_of) VALUES(?,?,?,?,?,?)",
                (self.connection_id, received_ns, kind, body, pos, replay),
            )
        self.deliveries += 1
        if mismatch:
            raise CaptureFailure(f"replayed MBO differs at position {self.position}")
        if pos is not None or replay is not None:
            self.position += 1

    def require_prefix(self):
        if self.position < self.prior:
            raise CaptureFailure(f"source ended before the stored prefix at position {self.position}")


@contextmanager
def capture_writer(path, identity):
    """A process crash releases the writer lock. SQLite retains committed deliveries."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect(path, check_same_thread=False)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS source(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS connections(
                    id INTEGER PRIMARY KEY, opened_ns INTEGER NOT NULL, ended_ns INTEGER, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_starts(connection_id INTEGER PRIMARY KEY, position INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS frames(
                    id INTEGER PRIMARY KEY, connection_id INTEGER NOT NULL, received_ns INTEGER NOT NULL,
                    kind TEXT NOT NULL, body BLOB NOT NULL, position INTEGER UNIQUE, replay_of INTEGER
                );
                CREATE INDEX IF NOT EXISTS frames_by_connection ON frames(connection_id, id);
            """)
            with db:
                db.execute("INSERT OR IGNORE INTO source VALUES(1,?)", (identity,))
                if db.execute("SELECT identity FROM source WHERE id=1").fetchone()[0] != identity:
                    raise ValueError("capture belongs to another source or week")
                # The old process no longer owns the lock. Its actual end time is unknown.
                db.execute("UPDATE connections SET status='interrupted' WHERE status='recording'")
                connection_id = db.execute(
                    "INSERT INTO connections(opened_ns,status) VALUES(?,'recording')", (time.time_ns(),)
                ).lastrowid
            writer = Capture(db, connection_id)
            status = "interrupted"
            try:
                yield writer
                writer.require_prefix()
                status = writer.status
            except CaptureFailure as exc:
                status = str(exc)
                raise
            finally:
                with db:
                    db.execute("UPDATE connections SET ended_ns=?, status=? WHERE id=?",
                               (time.time_ns(), status, connection_id))
        finally:
            db.close()


class CapturedFeed:
    """Read or tail the durable MBO positions with the same book logic as purchased replay."""

    def __init__(self, path, trade_day, connection_id=None):
        self.path = Path(path)
        self.trade_day = trade_day
        self.connection_id = connection_id

    def _connection_end(self, db):
        row = db.execute("SELECT opened_ns,ended_ns,status FROM connections WHERE id=?",
                         (self.connection_id,)).fetchone()
        if row is None:
            raise CaptureFailure("capture connection unavailable")
        opened, ended, status = row
        if status == "recording":
            with self.path.with_suffix(".lock").open("rb") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    return None
                # A hard exit releases the lock. A clean exit may have committed after BEGIN.
                db.commit()
                db.execute("BEGIN")
                ended, status = db.execute("SELECT ended_ns,status FROM connections WHERE id=?",
                                           (self.connection_id,)).fetchone()
                if status == "recording":
                    status = "recorder exited without a terminal record"
        last = db.execute("SELECT id,received_ns FROM frames WHERE connection_id=? ORDER BY id DESC LIMIT 1",
                          (self.connection_id,)).fetchone()
        last_mbo = db.execute("SELECT coalesce(position,replay_of) FROM frames WHERE connection_id=? "
                              "AND (position IS NOT NULL OR replay_of IS NOT NULL) ORDER BY id DESC LIMIT 1",
                              (self.connection_id,)).fetchone()
        return dict(connection_id=self.connection_id, status=status, opened_ns=opened, ended_ns=ended,
                    last_delivery_id=last[0] if last else None, last_received_ns=last[1] if last else None,
                    last_mbo_position=last_mbo[0] if last_mbo else None)

    def terminal_state(self):
        if self.connection_id is None:
            return None
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            return self._connection_end(db)

    def receive_time_coverage(self, cursor):
        """Describe when the stored first-copy timestamps were observed, through this run's cursor."""
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            source = json.loads(db.execute("SELECT identity FROM source WHERE id=1").fetchone()[0])["source"]
            rows = db.execute("""
                SELECT f.connection_id,
                       CASE WHEN ? = 'purchased' THEN 'purchased archive delivery'
                            WHEN s.position IS NOT NULL AND f.position >= s.position THEN 'live delivery'
                            ELSE 'gateway replay delivery' END,
                       MIN(f.position), MAX(f.position), COUNT(*)
                FROM frames f LEFT JOIN live_starts s ON f.connection_id=s.connection_id
                WHERE f.position BETWEEN 0 AND ?
                GROUP BY 1,2 ORDER BY MIN(f.position)
            """, (source, cursor))
            return [dict(connection_id=connection, observed_during=phase, first_position=first,
                         last_position=last, frames=count) for connection, phase, first, last, count in rows]

    def recorded_run(self, receipt):
        """Admit exactly the MBO prefix the original live runner consumed."""
        delivered = receipt["delivered"]
        first = receipt["first_live_decision_cursor"]
        if type(delivered) is not int or delivered < 0 or (
            first is not None and (type(first) is not int or not 0 <= first < delivered)
        ):
            raise ValueError("invalid recorded MBO window")
        last = receipt["last_source"]
        if delivered:
            if not last or last["cursor"] != delivered - 1 or last["reference"][1] != delivered - 1:
                raise ValueError("recorded endpoint differs from the delivered count")
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT body FROM frames WHERE position=?", (delivered - 1,)).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != last["reference"][0]:
                raise ValueError("checkpoint does not contain the recorded final MBO")
        elif last is not None:
            raise ValueError("empty recorded run has an input endpoint")
        count = 0
        for message in islice(self.messages(), delivered):
            message["live_eligible"] = first is not None and count >= first
            yield message
            count += 1
        if count != delivered:
            raise ValueError("checkpoint ended before the recorded endpoint")
        yield {"recorded_stop": receipt["status"], "recorded_capture_end": receipt.get("capture_end"),
               "timers_after_input": receipt.get("timers_after_last_input_ns", [])}

    def messages(self, *, follow=False, poll=False, through_trade_day=False):
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        stream = instrument = None
        position = 0
        try:
            identity = json.loads(db.execute("SELECT identity FROM source WHERE id=1").fetchone()[0])
            sunday = week_sunday(self.trade_day)
            if identity["sunday"] != sunday.isoformat() or identity["market"] != "nymex-ng":
                raise ValueError("capture does not cover the requested market week")
            replay_end = None
            if through_trade_day:
                # Match the purchased reader's last UTC file, including the marked break.
                end_day = clock_days_needed(self.trade_day)[-1] + timedelta(days=1)
                replay_end = to_ns(datetime.combine(end_day, wall_time(), timezone.utc))
            next_week = to_ns(datetime.combine(sunday + timedelta(days=7), wall_time(), timezone.utc))
            while True:
                db.execute("BEGIN")
                batch = db.execute(
                    "SELECT position,body,received_ns FROM frames WHERE position>=? ORDER BY position LIMIT 512",
                    (position,),
                ).fetchall()
                live_start = None
                terminal = None
                if follow:
                    if self.connection_id is None:
                        row = db.execute("SELECT MAX(id) FROM connections").fetchone()
                        self.connection_id = row[0]
                    terminal = self._connection_end(db)
                    if identity["source"] == "live":
                        live_start = db.execute("SELECT position FROM live_starts WHERE connection_id=?",
                                                (self.connection_id,)).fetchone()
                    elif terminal:
                        # For stored input, drain the final committed batch before reporting EOF.
                        batch = db.execute(
                            "SELECT position,body,received_ns FROM frames WHERE position>=? "
                            "ORDER BY position LIMIT 512", (position,),
                        ).fetchall()
                        if batch:
                            terminal = None
                db.commit()
                if terminal is not None:
                    if poll:
                        yield {"capture_end": terminal}
                    return
                if not batch:
                    if not follow:
                        return
                    if poll:
                        yield {"idle": True}
                    else:
                        time.sleep(0.1)
                    continue
                for index, body, received_ns in batch:
                    fields = dict(zip(MBO_FIELDS, MBO.unpack(body), strict=True))
                    if replay_end is not None and fields["ts_event"] >= replay_end:
                        return
                    if stream is None:
                        day = datetime.fromtimestamp(fields["ts_event"] // 1_000_000_000, timezone.utc).date()
                        if day != sunday or fields["action"] != tape.CLEAR or fields["flags"] & tape.flag_bits()["F_SNAPSHOT"]:
                            raise ValueError("capture must start at Sunday's unflagged exchange clear")
                        instrument = fields["instrument_id"]
                        stream = MboTape(self.trade_day, instrument)
                    if fields["instrument_id"] != instrument or index != position:
                        raise ValueError("contract or tape position changed within the captured week")
                    if fields["ts_event"] >= next_week:
                        raise ValueError("source crossed into a new capture week")
                    message = stream.apply(fields, source_sha256=hashlib.sha256(body).hexdigest(),
                                           index=index, local_ns=received_ns)
                    message["received_by_capture_ns"] = message.pop("received_by_replay_ns")
                    if follow and identity["source"] == "live":
                        message["live_eligible"] = live_start is not None and index >= live_start[0]
                    yield message
                    position += 1
        finally:
            db.close()


def capture_preflight(feed, rules, deadline, *, minimum=4096, follow=True):
    """Compare decisions on the collected prefix with a physically shortened copy."""
    from tempfile import TemporaryDirectory
    from .causal_gate import PreflightDeadline
    from .engine import Engine

    engine = Engine(feed.trade_day, "capture-cutoff", rules)
    expected = hashlib.sha256()
    count = eligible = opens = 0
    cutoff = None
    def record(digest, machine, message):
        decisions = machine.tick(message)
        if decisions:
            digest.update(json.dumps([message["cursor"], decisions], sort_keys=True,
                                     separators=(",", ":")).encode() + b"\n")
        return decisions

    for message in feed.messages(follow=follow, poll=follow, through_trade_day=not follow):
        if time.monotonic() >= deadline:
            raise PreflightDeadline("5.75-hour budget reached during capture preflight")
        if "capture_end" in message:
            raise CaptureEnded(message["capture_end"])
        if message.get("idle"):
            time.sleep(0.1)
            continue
        if cutoff is not None:
            break  # One further input exists and is withheld from the physical copy.
        decisions = record(expected, engine, message)
        count += len(decisions)
        opens += sum(d["action"] == "open" for d in decisions)
        eligible += int(message["in_session"])
        if eligible >= minimum and opens:
            cutoff = {k: message[k] for k in ("cursor", "source_sha256", "record_index")}
    else:
        raise ValueError("capture ended before a causal prefix and suffix were available")
    actual = hashlib.sha256()
    with TemporaryDirectory() as tmp:
        short = Path(tmp) / "cut.sqlite"
        with sqlite3.connect(short) as db:
            db.execute("ATTACH DATABASE ? AS original", (feed.path.as_uri() + "?mode=ro",))
            db.execute("CREATE TABLE source AS SELECT * FROM original.source")
            db.execute("CREATE TABLE connections AS SELECT * FROM original.connections")
            db.execute("CREATE TABLE frames AS SELECT position,body,received_ns FROM original.frames "
                       "WHERE position BETWEEN 0 AND ?", (cutoff["record_index"],))
            db.execute("CREATE UNIQUE INDEX tape_order ON frames(position)")
        machine = Engine(feed.trade_day, "capture-cutoff", rules)
        for message in CapturedFeed(short, feed.trade_day).messages():
            if time.monotonic() >= deadline:
                raise PreflightDeadline("5.75-hour budget reached during capture preflight")
            record(actual, machine, message)
    if actual.digest() != expected.digest():
        raise ValueError("captured tape cutoff changed strategy decisions")
    return dict(status="passed", cutoff=cutoff, decisions=count, decisions_sha256=actual.hexdigest())
