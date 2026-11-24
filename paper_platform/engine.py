"""Quote-only paper decisions. Every management action names its originating entry."""

import json
from uuid import NAMESPACE_URL, uuid5

from databento_dbn import F_MAYBE_BAD_BOOK

from .claude_rules import ClaudeRules, sign
from .cx_observations import Responses
from .inputs import StrategyInputs
from .rulebook import catalogue, confirmation, management, manager_action
from .unsided_print_rules import UnsidedPrintRules


def quote(t):
    return {k: t[k] for k in ("position", "received", "exchange", "reference", "bid", "ask")}


class Engine:
    def __init__(self, trade_day, run_id, rules=None):
        self.run_id = run_id
        self.inputs = StrategyInputs(trade_day)
        self.cl = ClaudeRules()
        self.unsided_print_rules = UnsidedPrintRules()
        self.rules = catalogue() if rules is None else rules
        self.cx = [r for r in self.rules.values() if r["family"] == "CX"]
        self.open = {}
        self.unmonitored = {}
        self.responses = Responses()
        self.managers = {}
        self.p = None
        self.last = None
        self.ended = False

    @property
    def position_count(self):
        return len(self.open) + len(self.unmonitored)

    def begin_decision_batch(self):
        # A refused batch immediately stops. Preserve inventory and outstanding
        # price evidence for previously published positions.
        return self.open.copy(), self.unmonitored.copy(), self.responses.pending.copy()

    def rollback_decision_batch(self, snapshot):
        self.open, self.unmonitored, self.responses.pending = snapshot

    def event(self, rule, action, why, tick, key, **details):
        return dict(rule=rule, action=action, reason=why, trade_id=key, **quote(tick), **details)

    def open_trade(self, signal, t, p=None):
        rule = signal["rule"]
        tag = json.dumps([self.run_id, rule, t["position"], signal["identity"]], separators=(",", ":"))
        key = str(uuid5(NAMESPACE_URL, tag))
        self.open[key] = dict(**signal, opened_at=t["position"], closed=False, p=p, q=t)
        if self.rules[rule]["family"] == "CX":
            self.responses.start(key, rule, key, t, signal["side"], rule, "open")
        return self.event(
            rule,
            "open",
            signal["reason"],
            t,
            key,
            side=signal["side"],
            arm_reference=p["reference"] if p else None,
            trigger=signal["identity"],
            conditions=signal.get("conditions"),
        )

    def close_trade(self, key, reason, t, administrative=False):
        idea = self.open.pop(key)
        self.managers.pop(key, None)
        return self.event(
            idea["rule"],
            "close",
            reason,
            t,
            key,
            side=idea["side"],
            entry=quote(idea["q"]),
            trade_data=not administrative,
            **{
                k: idea[k]
                for k in ("third_step_reference", "expiry_ns", "expiry_references", "expiry_quote_reference")
                if k in idea and not administrative
            },
        )

    def manage(self, key, idea, t):
        # Independent assessments refer to one parent's trade. They never create a second entry.
        active = self.managers.setdefault(key, {})
        events = []
        for rule in self.cx:
            if rule["role"] != "manager" or rule["direction"] != idea["side"]:
                continue
            name = rule["id"]
            state = active.get(name)
            if state is not None and "reset_at" in state:
                if self.p is None or self.p["position"] <= state["reset_at"]:
                    continue
                del active[name]
                state = None
            if state is not None:
                if state["finished"]:
                    continue
                action, missing = management(rule, idea["side"], state["p"], state["q"], t)
                if not missing:
                    events.extend(self.responses.prints((key + "/" + name,), t, self.event))
                if action == "exit":
                    state["finished"] = True
                    self.responses.exit(key + "/" + name)
                elif action == "release warning" or missing:
                    active[name] = dict(reset_at=t["position"])
            elif self.p and t["trade"]:
                action, missing = manager_action(rule, idea, self.p, t)
                if action:
                    active[name] = dict(p=self.p, q=t, finished=action == "exit")
                    self.responses.start(key + "/" + name, name, key, t, idea["side"], idea["rule"], action)
            else:
                continue
            if missing:
                action = "not scored"
                reason = "not scored: the feed lacks " + ", ".join(sorted(set(missing)))
            else:
                reason = rule["specification"].splitlines()[0][3:]
            if action:
                events.append(
                    self.event(
                        name,
                        action,
                        reason,
                        t,
                        key + "/" + name,
                        source_trade_id=key,
                        entry_rule=idea["rule"],
                        entry=quote(idea["q"]),
                        side=idea["side"],
                        management_only=True,
                        arm_reference=(state["p"] if state else self.p)["reference"],
                        **({"trade_data": False} if missing else {}),
                    )
                )
                if state is not None and (missing or action == "release warning"):
                    events.extend(self.responses.end(key + "/" + name, reason, t, self.event))
        return events

    def end_orphaned_responses(self, tick):
        events = []
        for key, response in list(self.responses.pending.items()):
            if response.parent not in self.open and not response.exited:
                events.extend(
                    self.responses.end(
                        key, "Parent observation ended before a distinct print", tick, self.event
                    )
                )
        return events

    def interrupt_trade(self, key, reason, tick, missing=False):
        idea = self.open.pop(key)
        self.unmonitored[key] = idea
        self.managers.pop(key, None)
        return self.event(
            idea["rule"],
            "not scored" if missing else "observation interrupted",
            reason,
            tick,
            key,
            entry=quote(idea["q"]),
            side=idea["side"],
            trade_data=False,
        )

    @property
    def next_expiry_ns(self):
        if self.ended or self.cl.hump is None:
            return None
        expiry = self.inputs.exhaustion.next_expiry_ns
        return expiry if expiry is not None and expiry < self.inputs.end_of_day else None

    def advance_clock(self, until_ns):
        """Drain distinct scheduled exhaustion expiries using only previously received records."""
        events = []
        while (expiry := self.next_expiry_ns) is not None and expiry <= until_ns:
            flow = self.inputs.exhaustion
            sources = flow.expire(expiry)
            hump = self.cl.hump
            if sign(flow.value) == hump["direction"]:
                continue
            self.cl.hump = None
            # A span expiry has a tape-clock time and last-known quotes, but no exchange tick.
            tick = dict(
                position=None,
                received=expiry,
                exchange=None,
                reference=sources[0],
                bid=self.last["bid"],
                ask=self.last["ask"],
            )
            for key, idea in list(self.open.items()):
                if idea["rule"] not in ("CL07", "CL08") or idea["conditions"]["hump"] != hump["start"]:
                    continue
                if "deferred_close" in idea:
                    continue
                idea.update(
                    expiry_ns=expiry, expiry_references=sources, expiry_quote_reference=self.last["reference"]
                )
                reason = "exhaustion hump ended as prints left its span"
                if self.inputs.start <= expiry < self.inputs.end:
                    idea["deferred_close"] = (reason, sources[0])
                else:
                    events.append(self.close_trade(key, reason, tick))
            events.extend(self.end_orphaned_responses(tick))
        return events

    def tick(self, message):
        if self.last is not None and message["period"] != "vendor snapshot":
            raw = message["raw"]
            # ROOT combines a same-nanosecond sided print and expiry into one read.
            sided = message["in_session"] and raw["action"] == "T" and raw["side"] in ("A", "B")
            events = self.advance_clock(raw["ts_recv"] - 1 if sided else raw["ts_recv"])
            return events + self._receive(message)
        return self._receive(message)

    def _receive(self, message):
        if self.ended:
            return []
        if not message["in_session"]:
            # Trade-day closure is knowable only when the clock leaves the trade day.
            # Later-file vendor snapshots are bookkeeping, including while a position is open.
            if (
                self.last
                and message["period"] != "vendor snapshot"
                and message["raw"]["ts_event"] >= self.inputs.end_of_day
            ):
                self.ended = True
                closed = self.stop("trade day ended", administrative=False)
                for event in closed:
                    event["known_at"] = [message["source_sha256"], message["record_index"]]
                    event["known_received_ns"] = message["raw"]["ts_recv"]
                return closed
            return []
        t = self.inputs.at(message)
        self.last = t
        signals = self.cl.at(message, t)
        print_signals = self.unsided_print_rules.at(t)
        events = []
        interrupted = (
            "settlement"
            if t["settlement"]
            else "feed marked the book uncertain"
            if message["raw"]["flags"] & F_MAYBE_BAD_BOOK
            else None
        )
        if interrupted:
            for key in list(self.responses.pending):
                events.extend(self.responses.end(key, interrupted, t, self.event))
            for states in self.managers.values():
                for name, state in list(states.items()):
                    if not state.get("finished"):
                        del states[name]
        elif t["trade"]:
            detached = [key for key, response in self.responses.pending.items() if response.exited]
            events.extend(self.responses.prints(detached, t, self.event))
        for key, idea in list(self.open.items()):
            rule = self.rules[idea["rule"]]
            if rule["family"] == "CL":
                reads_unsided = rule.get("reads_unsided_prints", False)
                if reads_unsided and t["trade"]:
                    missing = [name for name in ("bid", "ask") if t[name] is None]
                    if missing:
                        reason = "not scored: the feed lacks " + ", ".join(missing)
                        events.append(self.interrupt_trade(key, reason, t, missing=True))
                        continue
                close = self.unsided_print_rules.close(idea) if reads_unsided else self.cl.close(idea, message, t)
                unsided = t["trade"] and t["aggressor"] is None and not reads_unsided
                if unsided:
                    close = None
                if t["settlement"] and close:
                    idea.setdefault("deferred_close", (close, t["reference"]))
                    close = None
                if not t["settlement"] and not unsided and "deferred_close" in idea:
                    close = idea["deferred_close"][0] + " during settlement; released after settlement"
                if t["settlement"] and not idea.get("settlement_seen"):
                    idea["settlement_seen"] = True
                    events.append(
                        self.event(rule["id"], "held into settlement", "settlement window began", t, key)
                    )
            elif interrupted:
                events.append(self.interrupt_trade(key, interrupted, t))
                continue
            else:
                close, missing = management(rule, idea["side"], idea["p"], idea["q"], t)
                if missing:
                    reason = "not scored: the feed lacks " + ", ".join(sorted(set(missing)))
                    events.append(self.interrupt_trade(key, reason, t, missing=True))
                    continue
                events.extend(self.responses.prints((key,), t, self.event))
                name = rule["id"]
                if name in ("CX07", "CX08") and not idea.get("quote_followed"):
                    field = "bid" if name == "CX07" else "ask"
                    old, new = idea["q"][field], t[field]
                    if new is not None and (new > old if name == "CX07" else new < old):
                        idea["quote_followed"] = True
                        reason = (
                            "Bid rose above the decision bid"
                            if name == "CX07"
                            else "Offer fell below the decision offer"
                        )
                        events.append(
                            self.event(
                                name,
                                "quote followed",
                                reason,
                                t,
                                key,
                                q_reference=idea["q"]["reference"],
                                decision_quote=old,
                            )
                        )
            if not interrupted:
                events.extend(self.manage(key, idea, t))
            if close:
                events.append(self.close_trade(key, close, t))
        if t["trade"] and t["aggressor"] is None:
            signals = []
        for signal in signals + print_signals:
            if signal["rule"] in self.rules:
                events.append(self.open_trade(signal, t))
        if interrupted:
            self.p = None
        elif t["trade"]:
            if self.p:
                active_names = {idea["rule"] for idea in self.open.values()}
                active_names.update(idea["rule"] for idea in self.unmonitored.values())
                for rule in self.cx:
                    if rule["role"] == "manager" or rule["id"] in active_names:
                        continue
                    side, missing = confirmation(rule, self.p, t)
                    if side:
                        signal = dict(
                            rule=rule["id"],
                            side=side,
                            identity=self.p["position"],
                            reason=rule["specification"].splitlines()[0][3:],
                        )
                        events.append(self.open_trade(signal, t, self.p))
                    elif missing:
                        events.append(
                            self.event(
                                rule["id"],
                                "not scored",
                                "not scored: the feed lacks " + ", ".join(sorted(set(missing))),
                                t,
                                None,
                                trade_data=False,
                            )
                        )
            self.p = t
        events.extend(self.end_orphaned_responses(t))
        return events

    def stop(self, why, administrative=True):
        if self.last is None:
            return []
        events = []
        for key in list(self.responses.pending):
            events.extend(self.responses.end(key, why, self.last, self.event))
        for key, idea in list(self.open.items()):
            if not administrative and self.rules[idea["rule"]]["family"] == "CX":
                events.append(self.interrupt_trade(key, why, self.last))
            else:
                events.append(self.close_trade(key, why, self.last, administrative))
        if not administrative:
            for key, idea in list(self.unmonitored.items()):
                if self.rules[idea["rule"]]["family"] == "CL":
                    self.open[key] = self.unmonitored.pop(key)
                    events.append(self.close_trade(key, why, self.last, administrative=True))
        if administrative:
            self.open.update(self.unmonitored)
            self.unmonitored.clear()
            events.extend(self.close_trade(key, why, self.last, True) for key in list(self.open))
        return events
