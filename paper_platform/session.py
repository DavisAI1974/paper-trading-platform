"""One authorized paper session. The raw DBN stays in the input archive, addressed by hash and record."""

import hashlib
from importlib.metadata import version
import json
import sqlite3
import struct
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from crypto_ng.tick import NS

from .answers import validate, validate_decision
from .causal_gate import PreflightDeadline, check_prefix
from .engine import Engine
from .feed import HistoricalFeed
from .rulebook import RULES, catalogue
from .market import replay_market
from .run_clock import CLOCK_STOP, LIMIT_SECONDS, clock_stop, run_start_ns


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Session:
    def __init__(
        self, data_root, trade_day, *, message_budget, output_budget, market_key="nymex-ng", on_event=None,
        started=None, execution_plan=None, captured=None, plan_source=None, capture_connection=None,
        replay_capture=False, capture_commit=None, replay_receipt=None, paced_from_ns=None,
        answers=None, answer_close=False, decider=None,
    ):
        # A decider (answers.py) is asked at each opening signal of a rule the plan trades; None = no gate.
        self.decider = decider
        self.decided = dict(asked=0, take=0, skip=0, none=0)
        self.decider_hash = hashlib.sha256()
        # Answers from outside (see answers.py): with none plugged in (answers=None) nothing below runs.
        self.answers = answers
        self.answer_close = answer_close
        self.answers_off = set()
        self.answers_seen = set()
        self.answers_taken = self.answers_applied = 0
        self.answers_hash = hashlib.sha256()
        self.answers_unordered = set()
        # A start point inside the trade day, on the tape's own receive time (Greg, 2026-11-24: "Just do the
        # next 30 min"). Before it the book, the matcher and the rule inputs are warmed and nothing is
        # decided, journaled or ordered; the run starts new at that instant and carries no position into it.
        self.paced_from_ns = paced_from_ns
        self.started = run_start_ns() / 1_000_000_000 if started is None else started
        # A run that follows the live feed stops before 5 pm Eastern when its 5.75 hours would land in the
        # 5 pm to 6 pm hour (Greg, 2026-11-24). A replay keeps the 5.75 hours: its tape is not on the wall clock.
        follows_live = captured is not None and not replay_capture
        self.stop_seconds, self.clock_stop = clock_stop(self.started) if follows_live else (LIMIT_SECONDS, CLOCK_STOP)
        self.deadline = self.started + self.stop_seconds
        if time.monotonic() >= self.deadline:
            raise PreflightDeadline("5.75-hour limit reached before paper startup" if self.clock_stop == CLOCK_STOP
                                    else "the stop before 5 pm Eastern was reached before paper startup")
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
            self.execution = PaperExecution(execution_plan, self.rules, self.write, run=self.run)
        self.rule_commits = {
            key: git(code, "log", "-1", "--format=%H", self.revision, "--", f"paper_platform/rules/{key}.json")
            for key in self.rules
        }
        self.state = "active"
        self.cursor = -1
        self.weekly_book_boundaries = []
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
            calculation_files={path: digest(code / path) for path in (
                "paper_platform/inputs.py", "crypto_ng/tick.py", "root/calcs/quote_runs.py",
                "root/calcs/leg_peaks.py", "root/calendar.py", "root/book.py",
                # The code that decides for the rules: a CL rule's file holds its words, not its conditions.
                "paper_platform/claude_rules.py", "paper_platform/unsided_print_rules.py",
                "paper_platform/engine.py", "paper_platform/rulebook.py", "paper_platform/cx_observations.py")},
            rule_commits=self.rule_commits,
            rule_files={k: digest(RULES / (k + ".json")) for k in self.rules},
            causal_check=causal_check,
            constants=dict(
                dipole_seconds=self.engine.inputs.dipole.width / NS,
                exhaustion_seconds=self.engine.inputs.exhaustion.width / NS,
                stop_seconds=LIMIT_SECONDS,
                clock_stop_seconds=self.stop_seconds,
                clock_stop=self.clock_stop,
                message_budget=message_budget,
                event_budget=output_budget,
                paced_from_tape_ns=paced_from_ns,
            ),
        )
        if answers is not None or decider is not None:
            self.initial["answers"] = dict(on=True, answer_close=answer_close, decider=decider is not None)
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
        if (self.execution and e.get("trade_data") is False and e.get("trade_id")
                and e["action"] in ("not scored", "observation interrupted") and not e.get("management_only")):
            self.execution.record_interruption(dict(e, signal_sequence=signal["sequence"]))
        if not self.liquidation_available or e["action"] not in ("open", "close", "exit"):
            return
        sequence = signal["sequence"]
        order_id = f"{self.run}-{sequence}"
        if self.answers_off and e["action"] == "open" and e["rule"] in self.answers_off:
            # An answer switched this rule's orders off: the signal is journaled above and sends no order.
            self.answers_unordered.add(e["trade_id"])
        elif self.decider is not None and e["action"] == "open" and self._orders_for(e["rule"]):
            # The gate: the signal is journaled above; its order goes only on a take.
            if not self._ask_decider(signal, e):
                self.answers_unordered.add(e["trade_id"])
        if self.execution is None:
            if e["trade_id"] in self.answers_unordered:
                return
            self.write("paper order", **e, order_id=order_id, signal_sequence=sequence,
                       pricing="open", fill_price=None)
            return
        for instruction in self.execution.instructions(dict(e, signal_sequence=sequence)):
            if self.answers_off and instruction["action"] == "open" and (
                    instruction["rule"] in self.answers_off or instruction.get("entry_rule") in self.answers_off):
                continue
            if self.decider is not None and instruction["action"] == "open" and e["trade_id"] in self.answers_unordered:
                continue
            # Only the event budget cuts a decision's orders short. The clock is checked once the decision
            # is whole, so a live run and its replay send the same orders for the same message.
            if self.state == "active" and self.events >= self.output_budget:
                break
            tag = order_id if instruction["rule"] == e["rule"] else f"{order_id}-{instruction['rule']}"
            self.execution.submit(instruction, tag)

    def due(self):
        if time.monotonic() >= self.deadline:
            return self.clock_stop
        if self.cursor + 1 >= self.message_budget or self.events >= self.output_budget:
            return "budget reached"
        return None

    def _apply(self, decide, value, *, timer=False):
        before = self.engine.begin_decision_batch()
        decisions = decide(value)
        needed = sum(1 + (e["action"] in ("open", "close", "exit")) for e in decisions)
        if time.monotonic() >= self.deadline:
            self.engine.rollback_decision_batch(before)
            self.stop(self.clock_stop)
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
                    self.stop(self.clock_stop)
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
                warming = self.paced_from_ns is not None and message["raw"]["ts_recv"] < self.paced_from_ns
                if message["period"] != "vendor snapshot":
                    received = message["raw"]["ts_recv"]
                    while (expiry := self.engine.next_expiry_ns) is not None and expiry < received:
                        if warming:
                            # The windows expire as they would; before the start point nothing is recorded.
                            self.engine.advance_clock(expiry)
                            continue
                        self.advance_clock(expiry)
                        if self.state != "active":
                            return None
                if time.monotonic() >= self.deadline:
                    self.stop(self.clock_stop)
                    return None
                self.timers_after_input.clear()
                self.cursor = message["cursor"]
                self.last = message
                if "weekly_book_boundary" in message:
                    self.weekly_book_boundaries.append(message["weekly_book_boundary"])
                if self.captured:
                    self.capture_frame_hash.update(bytes.fromhex(message["source_sha256"]))
                self.receives.write(struct.pack("<Q", message["received_by_capture_ns" if self.captured else "received_by_replay_ns"]))
                if (self.answers is not None and message["period"] != "vendor snapshot" and not (
                        warming or self.captured and "live_eligible" in message and (
                            not message["live_eligible"] or self.cursor <= self.warm_through))):
                    # The same test as the warm-up branch below: nothing is answered while only warming.
                    self._take_answers(message["raw"]["ts_recv"])
                if self.execution:
                    self.execution.market(message)
                if self.execution and self.events >= self.output_budget:
                    self.stop("budget reached")
                    return None
                if warming or self.captured and "live_eligible" in message and (
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

    def _take_answers(self, tape_ns):
        """Take the waiting answers, in the order they arrived, before this message is applied.

        Each is a journal row of kind `answer` at the message's receive time. It changes only what comes after
        its row. The wall clock is not read here.
        """
        for answer in self.answers.take(tape_ns):
            self.answers_taken += 1
            self.answers_hash.update(json.dumps(answer, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            reason = self._answer_refusal(answer)
            if isinstance(answer, dict) and isinstance(answer.get("id"), str):
                self.answers_seen.add(answer["id"])
            if reason is None:
                self.answers_applied += 1
            row = dict(answer=answer, tape_received_ns=tape_ns, applied=reason is None)
            if reason is not None:
                row["reason"] = reason
            self.write("answer", **row)
            if reason is None:
                self._apply_answer(answer)

    def _ask_decider(self, signal, e):
        """Journal the question, block on the decider, journal its answer. True only on a take."""
        sequence = signal["sequence"]
        question = dict(sequence=sequence, rule=e["rule"], side=e.get("side"), trade_id=e["trade_id"],
                        tape_received_ns=e.get("received"), journal=str(self.folder / "events.jsonl"))
        self.write("question", signal_sequence=sequence, rule=e["rule"], side=e.get("side"),
                   trade_id=e["trade_id"], received=e.get("received"))
        self.decided["asked"] += 1
        started = time.monotonic_ns()
        try:
            given = self.decider.ask(question)
            failure = None
        except Exception as error:
            given, failure = None, f"the decider raised {type(error).__name__}"
        latency = time.monotonic_ns() - started  # wall time, recorded only; never added to a tape time
        if failure is None:
            failure = validate_decision(given, sequence)
        if failure is not None:
            action = "none"
            fields = dict(answer=given if isinstance(given, dict) else None, reason=failure)
            if isinstance(given, dict) and given.get("action") == "none" and isinstance(given.get("reason"), str):
                fields["reason"] = given["reason"]
            for key in ("by", "packet", "why"):
                if isinstance(given, dict) and key in given:
                    fields[key] = given[key]
        else:
            action = given["action"]
            fields = dict(answer=given, **{k: given.get(k) for k in ("by", "packet", "why")})
        self.decided[action] += 1
        self.decider_hash.update(json.dumps(given, sort_keys=True, separators=(",", ":"), default=str).encode() + b"\n")
        self.write("answer", action=action, asked_at_sequence=sequence, applied=action in ("take", "skip"),
                   latency_ns=latency, tape_received_ns=e.get("received"), **fields)
        return action == "take"

    def _orders_for(self, rule):
        if self.execution is None:
            return True
        return rule in self.execution.policies or bool(self.execution.followers.get(rule))

    def _answer_refusal(self, answer):
        """The reason an answer is not applied, or None. Decided before its row is written."""
        reason = validate(answer)
        if reason is not None:
            return "malformed: " + reason
        if answer["id"] in self.answers_seen:
            return "an answer with this id was already taken"
        if answer["action"] in ("take", "skip"):
            return "take and skip answer a question put to the decider; they are not taken from a file"
        if answer["action"] == "close":
            idea = self.engine.open.get(answer["trade_id"])
            if idea is None:
                return "the trade is not open"
            if answer.get("rule", idea["rule"]) != idea["rule"]:
                return "the trade belongs to another rule"
            if not self._orders_for(idea["rule"]):
                return "the plan sends no orders for this rule"
            if not self.answer_close:
                return ("the hold rule in force is the give-back exit; closing by answer is not switched on")
            return None
        rule = answer["rule"]
        if rule not in self.rules:
            return "unknown rule"
        if not self._orders_for(rule):
            return "the plan sends no orders for this rule"
        if (answer["action"] == "rule_off") == (rule in self.answers_off):
            return "the rule is already " + ("off" if rule in self.answers_off else "on")
        return None

    def _apply_answer(self, answer):
        if answer["action"] == "rule_off":
            self.answers_off.add(answer["rule"])
        elif answer["action"] == "rule_on":
            self.answers_off.discard(answer["rule"])
        else:
            # The trade is closed with its rule's own closing instruction, on the last quote the session knows.
            event = self.engine.close_trade(answer["trade_id"], "answer " + answer["id"], self.engine.last)
            self.record(dict(event, answer_id=answer["id"]))

    def answers_receipt(self):
        if self.answers is None and self.decider is None:
            return {"on": False}
        return dict(on=True, source=self.answers is not None, answer_close=self.answer_close,
                    taken=self.answers_taken, applied=self.answers_applied,
                    not_applied=self.answers_taken - self.answers_applied,
                    waiting_at_stop=self.answers.waiting() if self.answers is not None else 0,
                    sha256=self.answers_hash.hexdigest(),
                    decider=dict(on=self.decider is not None, **self.decided,
                                 sha256=self.decider_hash.hexdigest()),
                    causal_check_covers="the rules' decisions without answers")

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
                    weekly_book_boundaries=self.weekly_book_boundaries,
                )
                receipt["answers"] = self.answers_receipt()
                if self.captured:
                    receipt["timers_after_last_input_ns"] = self.timers_after_input
                    receipt["capture_frame_hashes_sha256"] = self.capture_frame_hash.hexdigest()
                    try:
                        receipt["capture_receive_time_coverage"] = self.feed.receive_time_coverage(self.cursor)
                    except (OSError, sqlite3.Error) as error:
                        receipt["capture_receive_time_coverage_error"] = type(error).__name__
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
