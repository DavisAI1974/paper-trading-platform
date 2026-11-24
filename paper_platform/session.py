"""One authorized paper session. The raw DBN stays in the input archive, addressed by hash and record."""

import hashlib
from importlib.metadata import version
import json
import struct
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from crypto_ng.tick import NS

from .causal_gate import check_prefix
from .engine import Engine
from .feed import HistoricalFeed
from .rulebook import RULES, catalogue
from .market import replay_market

LIMIT_SECONDS = 5.75 * 3600


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Session:
    def __init__(
        self, data_root, trade_day, *, message_budget, output_budget, market_key="nymex-ng", on_event=None,
        started=None,
    ):
        self.started = time.monotonic() if started is None else started
        self.market = replay_market(market_key)
        self.data_root = Path(data_root).resolve()
        origin = git(self.data_root, "remote", "get-url", "origin").lower().removesuffix(".git")
        if origin not in (
            "https://github.com/davisai1974/markets-data",
            "git@github.com:davisai1974/markets-data",
        ):
            raise ValueError("paper output must use a DavisAI1974/markets-data checkout")
        if min(message_budget, output_budget) <= 0:
            raise ValueError("set positive message and event budgets")
        self.lock = threading.RLock()
        self.run = uuid4().hex
        self.on_event = on_event
        code = RULES.parents[1]
        self.revision = git(code, "rev-parse", "HEAD")
        if git(code, "status", "--porcelain", "--", "paper_platform", "root", "crypto_ng"):
            raise ValueError("the running rules and calculation code must be committed")
        catalogue_rules = catalogue()
        self.rules = {key: catalogue_rules[key] for key in self.market["rules"]}
        self.rule_commits = {key: self.revision for key in self.rules}
        self.state = "active"
        self.cursor = -1
        self.events = 0
        self.last = None
        self.message_budget = message_budget
        self.output_budget = output_budget
        self.engine = Engine(trade_day, self.run, self.rules)
        folder = self.data_root / self.market["archive"]
        self.feed = HistoricalFeed(folder, trade_day, market_key)
        causal_check = check_prefix(self.feed, self.rules, self.started + LIMIT_SECONDS)
        self.stream = self.feed.messages()
        self.folder = self.data_root / "paper-trading" / self.run
        self.folder.mkdir(parents=True, exist_ok=False)
        self.journal = (self.folder / "events.jsonl").open("x", encoding="utf8", newline="\n")
        self.receives = (self.folder / "receive-ns.u64le").open("xb")
        self.initial = dict(
            run=self.run,
            market=market_key,
            trade_day=trade_day.isoformat(),
            code_commit=self.revision,
            order_book={"engine": "NautilusTrader", "version": version("nautilus_trader"), "type": "L3_MBO"},
            inputs=self.feed.sources,
            manifest_sha256=digest(folder / "MANIFEST.json"),
            data_commit=git(self.data_root, "rev-parse", "HEAD"),
            rule_commits=self.rule_commits,
            rule_files={k: digest(RULES / (k + ".json")) for k in self.rules},
            causal_check=causal_check,
            constants=dict(
                dipole_seconds=self.engine.inputs.dipole.width / NS,
                exhaustion_seconds=self.engine.inputs.exhaustion.width / NS,
                stop_seconds=LIMIT_SECONDS,
                message_budget=message_budget,
                event_budget=output_budget,
            ),
        )
        (self.folder / "start.json").write_text(json.dumps(self.initial, indent=2), encoding="utf8")
        self.write("status", reason="started")

    def write(self, kind, **fields):
        event = dict(
            run=self.run,
            market=self.market["id"],
            sequence=self.events,
            kind=kind,
            recorded_ns=time.time_ns(),
            **fields,
        )
        name = event.get("rule")
        if name:
            event.update(rule_commit=self.rule_commits[name], finding=self.rules[name]["evidence"])
        self.journal.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.journal.flush()
        self.events += 1
        if self.on_event is not None:
            self.on_event(event)
        return event

    def record(self, e):
        signal = self.write("signal", **e)
        if e["action"] in ("open", "close", "exit"):
            self.write(
                "paper order",
                **e,
                order_id=f"{self.run}-{signal['sequence']}",
                signal_sequence=signal["sequence"],
                pricing="open",
                fill_price=None,
            )

    def due(self):
        if time.monotonic() - self.started >= LIMIT_SECONDS:
            return "flattened at the 5.75-hour stop"
        if self.cursor + 1 >= self.message_budget or self.events >= self.output_budget:
            return "budget reached"
        return None

    def _apply(self, decide, value):
        before = self.engine.begin_decision_batch()
        decisions = decide(value)
        needed = sum(1 + (e["action"] in ("open", "close", "exit")) for e in decisions)
        if time.monotonic() - self.started >= LIMIT_SECONDS:
            self.engine.rollback_decision_batch(before)
            self.stop("flattened at the 5.75-hour stop")
            return False
        if self.events + needed > self.output_budget:
            self.engine.rollback_decision_batch(before)
            self.stop("budget reached")
            return False
        for decision in decisions:
            self.record(decision)
        reason = self.due()
        if reason:
            self.stop(reason)
        return True

    def advance_clock(self, received_ns):
        """Record a due strategy timer without consuming a feed record or message budget."""
        with self.lock:
            if self.state != "active":
                return
            reason = self.due()
            if reason:
                self.stop(reason)
                return
            try:
                self._apply(self.engine.advance_clock, received_ns)
            except Exception:
                self.stop("failure")
                raise

    def step(self, expected):
        with self.lock:
            if self.state != "active":
                return None
            if expected != self.cursor:
                raise ValueError("cursor changed; read the session before advancing")
            reason = self.due()
            if reason:
                self.stop(reason)
                return None
            try:
                message = next(self.stream, None)
                if message is None:
                    self.stop("end of purchased input")
                    return None
                if message["period"] != "vendor snapshot":
                    received = message["raw"]["ts_recv"]
                    while (expiry := self.engine.next_expiry_ns) is not None and expiry < received:
                        self.advance_clock(expiry)
                        if self.state != "active":
                            return None
                self.cursor = message["cursor"]
                self.last = message
                self.receives.write(struct.pack("<Q", message["received_by_replay_ns"]))
                return message if self._apply(self.engine.tick, message) else None
            except Exception:
                self.stop("failure")
                raise

    def stop(self, reason):
        with self.lock:
            if self.state != "active":
                return
            self.state = reason
            try:
                for e in self.engine.stop(reason):
                    self.record(e)
                self.write("status", reason=reason, trade_data=False)
            finally:
                self.receives.close()
                self.journal.close()
                files = {p.name: digest(p) for p in self.folder.iterdir() if p.is_file()}
                receipt = dict(
                    **self.initial, status=reason, delivered=self.cursor + 1, events=self.events, files=files
                )
                (self.folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")

    def status(self):
        with self.lock:
            return dict(
                run=self.run,
                market=self.market["id"],
                status=self.state,
                cursor=self.cursor,
                events=self.events,
                open_positions=self.engine.position_count,
                code_commit=self.revision,
            )
