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
        started=None, execution_plan=None, captured=None, plan_source=None, capture_connection=None,
    ):
        self.started = time.monotonic() if started is None else started
        self.trade_day = trade_day.isoformat()
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
        self.execution = None
        if execution_plan is not None:
            from .paper_execution import PaperExecution
            self.execution = PaperExecution(execution_plan, self.rules, self.write)
        self.rule_commits = {key: self.revision for key in self.rules}
        self.state = "active"
        self.cursor = -1
        self.events = 0
        self.last = None
        self.message_budget = message_budget
        self.output_budget = output_budget
        self.engine = Engine(trade_day, self.run, self.rules)
        folder = self.data_root / self.market["archive"]
        self.capture_end = None
        self.liquidation_available = True
        self.captured = captured is not None
        if self.captured:
            from .capture import CapturedFeed, capture_preflight
            path = Path(captured).resolve()
            relative = path.relative_to(self.data_root / "paper-trading" / "captures")
            self.feed = CapturedFeed(path, trade_day, capture_connection)
            causal_check = capture_preflight(self.feed, self.rules, self.started + LIMIT_SECONDS)
            self.stream = self.feed.messages(follow=True, poll=True)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                self.warm_through = db.execute("SELECT COALESCE(MAX(position),-1) FROM frames").fetchone()[0]
            sources = [dict(capture=relative.as_posix(), connection_id=self.feed.connection_id, reference="frame SHA-256 and tape position")]
        else:
            self.feed = HistoricalFeed(folder, trade_day, market_key)
            causal_check = check_prefix(self.feed, self.rules, self.started + LIMIT_SECONDS)
            self.stream = self.feed.messages()
            sources = self.feed.sources
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
            inputs=sources,
            execution_plan=execution_plan,
            execution_plan_source=plan_source,
            execution_plan_sha256=hashlib.sha256(json.dumps(execution_plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if execution_plan is not None else None,
            manifest_sha256=None if self.captured else digest(folder / "MANIFEST.json"),
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
        if not self.liquidation_available or e["action"] not in ("open", "close", "exit"):
            return
        sequence = signal["sequence"]
        order_id = f"{self.run}-{sequence}"
        if self.execution is None:
            self.write("paper order", **e, order_id=order_id, signal_sequence=sequence,
                       pricing="open", fill_price=None)
            return
        for instruction in self.execution.instructions(dict(e, signal_sequence=sequence)):
            if self.state == "active" and (self.events >= self.output_budget
                                           or time.monotonic() - self.started >= LIMIT_SECONDS):
                break
            tag = order_id if instruction["rule"] == e["rule"] else f"{order_id}-{instruction['rule']}"
            self.execution.submit(instruction, tag)

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
            if self.execution and self.events >= self.output_budget:
                self.stop("budget reached")
                return False
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
                    self.stop("end of capture" if self.captured else "end of purchased input")
                    return None
                if "capture_end" in message:
                    self.stop_capture(message["capture_end"])
                    return None
                if message.get("idle"):
                    return None
                if message["period"] != "vendor snapshot":
                    received = message["raw"]["ts_recv"]
                    while (expiry := self.engine.next_expiry_ns) is not None and expiry < received:
                        self.advance_clock(expiry)
                        if self.state != "active":
                            return None
                self.cursor = message["cursor"]
                self.last = message
                if self.execution:
                    self.execution.market(message)
                self.receives.write(struct.pack("<Q", message["received_by_capture_ns" if self.captured else "received_by_replay_ns"]))
                if self.execution and self.events >= self.output_budget:
                    self.stop("budget reached")
                    return None
                if self.captured and "live_eligible" in message and (
                    not message["live_eligible"] or self.cursor <= self.warm_through
                ):
                    # Warm the rule inputs but carry no historical position across the manual start.
                    self.engine.tick(message)
                    self.engine.open.clear()
                    self.engine.unmonitored.clear()
                    self.engine.managers.clear()
                    self.engine.responses.pending.clear()
                    return message
                return message if self._apply(self.engine.tick, message) else None
            except Exception:
                self.stop("failure")
                raise

    def stop_capture(self, evidence):
        with self.lock:
            if self.state != "active":
                return False
            self.capture_end = evidence
            self.liquidation_available = evidence["status"] in (
                "closed", "end of purchased input", "message budget reached", "clock limit", "capture stopped"
            )
            reason = ("flattened at the 5.75-hour stop" if evidence["status"] == "clock limit"
                      else "capture: " + evidence["status"])
            return self.stop(reason)

    def last_source(self):
        if self.last is None:
            return None
        return dict(cursor=self.cursor, reference=[self.last["source_sha256"], self.last["record_index"]],
                    tape_received_ns=self.last["raw"]["ts_recv"],
                    host_received_ns=self.last.get("received_by_capture_ns", self.last.get("received_by_replay_ns")))

    def stop(self, reason):
        with self.lock:
            if self.state != "active":
                return
            self.state = reason
            try:
                for e in self.engine.stop(reason):
                    self.record(e)
                if self.execution and self.last:
                    self.execution.flatten(self.last, reason, self.run, executable=self.liquidation_available)
                self.write("status", reason=reason, trade_data=False, last_source=self.last_source(),
                           capture_end=self.capture_end, remaining_paper_inventory=self._paper_inventory())
            finally:
                self.receives.close()
                self.journal.close()
                files = {p.name: digest(p) for p in self.folder.iterdir() if p.is_file()}
                receipt = dict(
                    **self.initial, status=reason, delivered=self.cursor + 1, events=self.events, files=files,
                    remaining_paper_inventory=self._paper_inventory(), last_source=self.last_source(),
                    capture_end=self.capture_end,
                )
                (self.folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")

            return True

    def _paper_inventory(self):
        if self.execution is None:
            return {}
        return {rule: {key: qty for key, qty in venue.inventory.items() if qty}
                for rule, venue in self.execution.venues.items() if any(venue.inventory.values())}

    def status(self):
        with self.lock:
            return dict(
                run=self.run,
                market=self.market["id"],
                status=self.state,
                trade_day=self.trade_day,
                cursor=self.cursor,
                capture_end=self.capture_end,
                events=self.events,
                open_positions=self.engine.position_count,
                paper_inventory=self._paper_inventory(),
                code_commit=self.revision,
            )
