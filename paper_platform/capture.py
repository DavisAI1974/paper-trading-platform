"""Durable DBN delivery journal and ordered MBO history for one market week."""

from contextlib import contextmanager
from datetime import datetime, time as wall_time, timedelta, timezone
import json
import fcntl
import hashlib
from pathlib import Path
import sqlite3
import struct
import time

from root import tape
from root.calendar import to_ns, week_sunday

from .feed import MboTape


# Databento DBN RecordHeader followed by the MBO payload, with ts_out disabled.
MBO = struct.Struct("<BBHIQQqIBBccQiI")
MBO_FIELDS = ("length", "rtype", "publisher_id", "instrument_id", "ts_event", "order_id",
              "price", "size", "flags", "channel_id", "action", "side", "ts_recv",
              "ts_in_delta", "sequence")


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
            raise ValueError(f"replayed MBO differs at position {self.position}")
        if pos is not None or replay is not None:
            self.position += 1

    def require_prefix(self):
        if self.position < self.prior:
            raise ValueError(f"source ended before the stored prefix at position {self.position}")


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
                CREATE TABLE IF NOT EXISTS frames(
                    id INTEGER PRIMARY KEY, connection_id INTEGER NOT NULL, received_ns INTEGER NOT NULL,
                    kind TEXT NOT NULL, body BLOB NOT NULL, position INTEGER UNIQUE, replay_of INTEGER
                );
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
            finally:
                with db:
                    db.execute("UPDATE connections SET ended_ns=?, status=? WHERE id=?",
                               (time.time_ns(), status, connection_id))
        finally:
            db.close()


class CapturedFeed:
    """Read or tail the durable MBO positions with the same book logic as purchased replay."""

    def __init__(self, path, trade_day):
        self.path = Path(path)
        self.trade_day = trade_day

    def messages(self, *, follow=False):
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        stream = instrument = None
        position = 0
        try:
            identity = json.loads(db.execute("SELECT identity FROM source WHERE id=1").fetchone()[0])
            sunday = week_sunday(self.trade_day)
            if identity["sunday"] != sunday.isoformat() or identity["market"] != "nymex-ng":
                raise ValueError("capture does not cover the requested market week")
            next_week = to_ns(datetime.combine(sunday + timedelta(days=7), wall_time(), timezone.utc))
            while True:
                db.execute("BEGIN")
                batch = db.execute(
                    "SELECT position,body,received_ns FROM frames WHERE position>=? ORDER BY position LIMIT 512",
                    (position,),
                ).fetchall()
                state = db.execute("SELECT status FROM connections ORDER BY id DESC LIMIT 1").fetchone()
                db.commit()
                if not batch:
                    if not follow or not state or state[0] != "recording":
                        return
                    time.sleep(0.1)
                    continue
                for index, body, received_ns in batch:
                    fields = dict(zip(MBO_FIELDS, MBO.unpack(body), strict=True))
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
                    yield message
                    position += 1
        finally:
            db.close()
