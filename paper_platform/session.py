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

from .causal_gate import PreflightDeadline, check_prefix
from .engine import Engine
from .feed import HistoricalFeed
from .rulebook import RULES, catalogue
from .market import replay_market
from .run_clock import CLOCK_STOP, LIMIT_SECONDS, run_start_ns


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Session:
    def __init__(
        self, data_root, trade_day, *, message_budget, output_budget, market_key="nymex-ng", on_event=None,
        started=None, execution_plan=None, captured=None, plan_source=None, capture_connection=None,
        replay_capture=False, capture_commit=None, replay_receipt=None,
    ):
        self.started = run_start_ns() / 1_000_000_000 if started is None else started
        self.deadline = self.started + LIMIT_SECONDS
        if time.monotonic() >= self.deadline:
            raise PreflightDeadline("5.75-hour limit reached before paper startup")
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
        self.stopping = threading.Event()
        self.run = uuid4().hex
        self.on_event = on_event
        code = RULES.parents[1]
        self.revision = git(code, "rev-parse", "HEAD")
        if git(code, "status", "--porcelain", "--", "paper_platform", "root", "crypto_ng"):
            raise ValueError("the running rules and calculation code must be committed")
        if replay_receipt is not None and replay_receipt["code_commit"] != self.revision:
            raise ValueError("receipt replay must run at the original platform commit")
        catalogue_rules = catalogue()
        self.rules = {key: catalogue_rules[key] for key in self.market["rules"]}
        self.execution = None
        if execution_plan is not None:
            from .paper_execution import PaperExecution
            self.execution = PaperExecution(execution_plan, self.rules, self.write)
        self.rule_commits = {
            key: git(code, "log", "-1", "--format=%H", self.revision, "--", f"paper_platform/rules/{key}.json")
            for key in self.rules
        }
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
        self.shutdown_evidence = {}
        self.first_live_decision_cursor = None
        self.timers_after_input = []
        self.captured = captured is not None
        if self.captured:
            self.capture_frame_hash = hashlib.sha256()
            from .capture import CapturedFeed, capture_preflight
            path = Path(captured).resolve()
            relative = path.relative_to(self.data_root / "paper-trading" / "captures")
            if replay_receipt is not None:
                original = replay_receipt["inputs"][0]
                if (not replay_capture or original.get("mode") != "follow recorder"
                        or original.get("capture") != relative.as_posix()
                        or replay_receipt["trade_day"] != self.trade_day or replay_receipt["market"] != market_key
                        or "first_live_decision_cursor" not in replay_receipt):
                    raise ValueError("receipt lacks a matching live capture and recorded eligibility window")
            self.feed = CapturedFeed(path, trade_day, capture_connection)
            causal_check = capture_preflight(self.feed, self.rules, self.deadline, follow=not replay_capture)
            if replay_receipt is not None:
                self.stream = self.feed.recorded_run(replay_receipt)
            else:
                self.stream = self.feed.messages(follow=not replay_capture, poll=not replay_capture,
                                                 through_trade_day=replay_capture)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                self.warm_through = -1 if replay_capture else db.execute("SELECT COALESCE(MAX(position),-1) FROM frames").fetchone()[0]
            sources = [dict(capture=relative.as_posix(), connection_id=self.feed.connection_id,
                            mode="stored replay" if replay_capture else "follow recorder",
                            original_run=replay_receipt["run"] if replay_receipt else None,
                            checkpoint_commit=capture_commit, warm_through=None if replay_capture else self.warm_through,
                            reference="frame SHA-256 and tape position")]
        else:
            self.feed = HistoricalFeed(folder, trade_day, market_key)
            causal_check = check_prefix(self.feed, self.rules, self.deadline)
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
                                           or time.monotonic() >= self.deadline):
                break
            tag = order_id if instruction["rule"] == e["rule"] else f"{order_id}-{instruction['rule']}"
            self.execution.submit(instruction, tag)

    def due(self):
        if time.monotonic() >= self.deadline:
            return CLOCK_STOP
        if self.cursor + 1 >= self.message_budget or self.events >= self.output_budget:
            return "budget reached"
        return None

    def _apply(self, decide, value, *, timer=False):
        before = self.engine.begin_decision_batch()
        decisions = decide(value)
        needed = sum(1 + (e["action"] in ("open", "close", "exit")) for e in decisions)
        if time.monotonic() >= self.deadline:
            self.engine.rollback_decision_batch(before)
            self.stop(CLOCK_STOP)
            return False
        if self.events + needed > self.output_budget:
            self.engine.rollback_decision_batch(before)
            self.stop("budget reached")
            return False
        if timer:
            # The next source record may never be admitted if this timer stops the run.
            self.timers_after_input.append(value)
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
                self._apply(self.engine.advance_clock, received_ns, timer=True)
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
                # A recorder failure still governs whether liquidation is executable.
                if message is not None and "capture_end" in message:
                    self.stop_capture(message["capture_end"])
                    return None
                if time.monotonic() >= self.deadline:
                    self.stop(CLOCK_STOP)
                    return None
                if message is None:
                    self.stop("end of capture" if self.captured else "end of purchased input")
                    return None
                if "recorded_stop" in message:
                    for expiry in message["timers_after_input"]:
                        if self.engine.next_expiry_ns != expiry:
                            raise ValueError("recorded terminal timer differs from the next strategy expiry")
                        self.advance_clock(expiry)
                        if self.state != "active":
                            return None
                    if message["recorded_capture_end"] is not None:
                        self.stop_capture(message["recorded_capture_end"])
                    else:
                        self.stop(message["recorded_stop"])
                    return None
                if message.get("idle"):
                    return None
                if message["period"] != "vendor snapshot":
                    received = message["raw"]["ts_recv"]
                    while (expiry := self.engine.next_expiry_ns) is not None and expiry < received:
                        self.advance_clock(expiry)
                        if self.state != "active":
                            return None
                if time.monotonic() >= self.deadline:
                    self.stop(CLOCK_STOP)
                    return None
                self.timers_after_input.clear()
                self.cursor = message["cursor"]
                self.last = message
                if self.captured:
                    self.capture_frame_hash.update(bytes.fromhex(message["source_sha256"]))
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
                if message.get("live_eligible") and self.first_live_decision_cursor is None:
                    self.first_live_decision_cursor = self.cursor
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
            reason = (CLOCK_STOP if evidence["status"] == "clock limit"
                      else "capture: " + evidence["status"])
            return self.stop(reason)

    def stop_notice(self):
        reason = "5.75-hour stop reached" if self.state == CLOCK_STOP else self.state
        if not self.capture_end:
            return reason
        last = self.capture_end.get("last_mbo_position")
        recorded = "unknown" if last is None else str(last)
        processed = "none" if self.cursor < 0 else str(self.cursor)
        return f"{reason}. Last recorded MBO {recorded}. Last processed {processed}."

    def last_source(self):
        if self.last is None:
            return None
        return dict(cursor=self.cursor, reference=[self.last["source_sha256"], self.last["record_index"]],
                    tape_received_ns=self.last["raw"]["ts_recv"],
                    host_received_ns=self.last.get("received_by_capture_ns", self.last.get("received_by_replay_ns")))

    def _record_stop_signals(self, reason, close_signals):
        for decision in self.engine.stop(reason):
            if self.execution is None:
                self.record(decision)
                continue
            signal = self.write("signal", **decision)
            if decision["action"] == "close":
                for instruction in self.execution.instructions(dict(decision, signal_sequence=signal["sequence"])):
                    close_signals[instruction["rule"], instruction["trade_id"]] = instruction

    def stop(self, reason):
        with self.lock:
            if self.state != "active":
                return
            self.shutdown_evidence = {"elapsed_ns_at_stop": time.monotonic_ns() - round(self.started * 1_000_000_000)}
            self.state = reason
            self.stopping.set()
            try:
                errors = []
                close_signals = {}
                try:
                    self._record_stop_signals(reason, close_signals)
                except Exception as error:
                    errors.append(dict(stage="close signals", error=type(error).__name__))
                if self.execution and self.last:
                    errors.extend(self.execution.flatten(self.last, reason, self.run,
                                                         executable=self.liquidation_available,
                                                         close_signals=close_signals))
                working = self.execution.working_orders() if self.execution else {}
                inventory = self._paper_inventory()
                native_flat = None
                if self.execution:
                    if working or inventory:
                        native_flat = False
                    elif not any(issue["stage"] == "liquidation" for issue in errors):
                        native_flat = True
                self.shutdown_evidence.update(remaining_working_orders=working, errors=errors, native_flat=native_flat)
                self.write("status", reason=reason, trade_data=False, last_source=self.last_source(),
                           capture_end=self.capture_end, remaining_paper_inventory=inventory, shutdown=self.shutdown_evidence)
            finally:
                self.receives.close()
                self.journal.close()
                files = {p.name: digest(p) for p in self.folder.iterdir() if p.is_file()}
                receipt = dict(
                    **self.initial, status=reason, delivered=self.cursor + 1, events=self.events, files=files,
                    first_live_decision_cursor=self.first_live_decision_cursor, shutdown=self.shutdown_evidence,
                    remaining_paper_inventory=self._paper_inventory(), last_source=self.last_source(),
                    capture_end=self.capture_end,
                )
                if self.captured:
                    receipt["timers_after_last_input_ns"] = self.timers_after_input
                    receipt["capture_frame_hashes_sha256"] = self.capture_frame_hash.hexdigest()
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
