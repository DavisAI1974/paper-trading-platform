"""The paper runner loop on the tastytrade SANDBOX (builder run B2).

    python3 -m tasty.paper.runner --out DIR --account A --symbol /NGF7 --streamer-symbol /NGF27:XNYM \\
        --seconds N --max-orders K [--orders] | --replay FILE

Nothing is defaulted by the code. Without --orders nothing is sent to the sandbox: each intended order is written
with sent false. With --replay a recording is read instead of the socket and NO order is ever sent. The engine is
tasty.paper.replay.Engine (all rules); the signals are the new entries of engine.ledger.events. Gross only: no fee,
slippage, mid price, fill assumption, average or label anywhere; a fill price is written as the sandbox said it.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from tasty.paper import broker_tasty as bt
from tasty.paper import feed_tasty
from tasty.paper.events import PRINT
from tasty.paper.ledger import NOT_SCORED
from tasty.paper.replay import Engine

CODE_REPO = Path(__file__).resolve().parents[2]
RUN_CAP_SECONDS = 5.75 * 3600          # Greg's cap
FEED_STOP_ET = (16, 44)                # the feed showing 4:44 pm Eastern
WALL_STOP_ET = (16, 50)                # the wall clock 4:50 pm Eastern
DAY_ENDS_ET_HOUR = 18                  # from 6 pm Eastern the next trade day has begun: these two stops are for 16:xx-17:xx
SIDE_TO_ACTION = {"BUY": "Buy", "SELL": "Sell"}
OPPOSITE = {"Buy": "Sell", "Sell": "Buy"}

NOT_EXERCISED_LIVE = [
    "sign-in, quote token and the DXLink socket inside the runner (the 7e check ran them; this runner's copy has not)",
    "the order worker against the sandbox: dry-run, submit, polling and the fill reading in a running loop",
    "an order submit that returns 2xx without data.order.id, and one that is uncertain (timeout, 5xx): the look-up "
    "by external-identifier and the outcome read from what it finds",
    "a dry-run that returns errors or warnings",
    "a close sent after a confirmed open; a close withheld because its open was not confirmed",
    "the order budget stop (max-orders minus one) and the flatten that it keeps back for",
    "the flatten order, the start check (no position, no live order), and the final pull of orders and positions",
    "the 4:44 pm feed stop, the 4:50 pm wall-clock stop and the 5 pm to 6 pm refusal of orders on the live wall clock",
    "a dropped stream (no reconnect), SIGINT and SIGTERM during a live run",
    "an external-identifier longer or differently shaped than the sandbox accepts (not known)",
]


def _et(ns):
    return datetime.datetime.fromtimestamp(ns / 1e9, bt.ET)


def in_stop_window(dt, hm):
    """True from hh:mm up to (not including) 6 pm Eastern, the same day."""
    return (dt.hour, dt.minute) >= hm and dt.hour < DAY_ENDS_ET_HOUR


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))


class Runner:
    def __init__(self, a, out):
        self.a, self.out = a, out
        self.replay = a.replay is not None
        self.orders_on = bool(a.orders) and not self.replay
        self.start_ns = time.time_ns()
        self.run_id = bt.stamp(self.start_ns)
        self.ctx = bt.make_ctx(out)
        self.feed = bt.LockedRecorder(self.ctx, "feed.jsonl")
        self.events_rec = bt.LockedRecorder(self.ctx, "events.jsonl")
        self.ctx["rec"] = bt.LockedRecorder(self.ctx, "orders.jsonl")
        self.engine = Engine()
        self.n_seen = 0
        self.n_frames = 0
        self.sides = {}            # trade id -> the action of its open
        self.trade = {}            # trade id -> dict(open_confirmed, close_sent)
        self.trade_orders = {}     # trade id -> list of dict(role, external_identifier, sent, reason, outcome)
        self.flatten_orders = []
        self.stop_reason = None
        self.stopping = False
        self.submits = 0
        self.counts = dict(frames=0, events_open=0, events_close=0, events_indication=0, intents=0, intents_sent=0)
        self.broker = None
        self.queue = None
        self.lock = threading.Lock()

    # ---- stops
    def stop(self, why):
        if self.stop_reason is None:
            self.stop_reason = why
        self.stopping = True

    def should_stop(self):
        if self.stopping:
            return True
        el = (time.time_ns() - self.start_ns) / 1e9
        if el >= self.a.seconds:
            self.stop(f"--seconds {self.a.seconds} reached")
        elif el >= RUN_CAP_SECONDS:
            self.stop("5.75 hour cap reached")
        elif in_stop_window(_et(time.time_ns()), WALL_STOP_ET):
            self.stop("wall clock 4:50 pm Eastern")
        return self.stopping

    # ---- the feed
    def on_frame(self, raw, t_ns, msg_no, host=None):
        self.n_frames += 1
        self.counts["frames"] += 1
        self.feed.write(t_ns=t_ns, kind="received", host=host, msg_no=msg_no, raw=raw)
        for ev in feed_tasty.events_from_frame(raw, t_ns, msg_no):
            self.engine.on_event(ev)
            if isinstance(ev, PRINT) and in_stop_window(_et(ev.time_ns), FEED_STOP_ET):
                self.stop("feed time 4:44 pm Eastern")
        self.process_new(t_ns)

    def process_new(self, t_ns):
        evs = self.engine.ledger.events
        while self.n_seen < len(evs):
            ev = evs[self.n_seen]
            self.n_seen += 1
            self.events_rec.write(t_ns=t_ns, kind="event", event=ev)
            self.counts["events_" + ev["event"]] = self.counts.get("events_" + ev["event"], 0) + 1
            if ev["event"] == "indication":
                continue                               # sends nothing: the engine emits the close if it closes
            self.intend(ev)

    def intend(self, ev):
        tid, role = ev["trade_id"], ev["event"]
        if role == "open":
            action = SIDE_TO_ACTION[ev["side"]]
            self.sides[tid] = action
        else:
            action = OPPOSITE[self.sides[tid]]
        at = ev["at"]
        intent = dict(role=role, strategy=ev["strategy"], trade_id=tid, action=action, quantity=bt.ORDER_QUANTITY,
                      symbol=self.a.symbol, external_identifier=safe_id(f"{self.run_id}-{ev['strategy']}-{tid}-{role}"),
                      event_at=dict(place=at["place"], feed_time_ns=at["feed_time_ns"], sequence=at["sequence"],
                                    bid=at["bid"], offer=at["offer"]))
        self.counts["intents"] += 1
        if not self.orders_on:
            why = "replay: no order is ever sent" if self.replay else "no --orders flag: nothing is sent"
            bt.write_intent(self.ctx["rec"], intent, False, why)
            self.trade_orders.setdefault(tid, []).append(dict(role=role, external_identifier=intent["external_identifier"],
                                                              sent=False, reason=why, outcome=None))
            return
        self.queue.put_nowait(intent)

    # ---- the order worker (one thread of work at a time, called through asyncio.to_thread)
    def execute(self, intent):
        tid, role = intent["trade_id"], intent["role"]
        rec = self.ctx["rec"]

        def skip(why):
            bt.write_intent(rec, intent, False, why)
            self.trade_orders.setdefault(tid, []).append(dict(role=role, external_identifier=intent["external_identifier"],
                                                              sent=False, reason=why, outcome=None))
        st = self.trade.setdefault(tid, dict(open_confirmed=None, close_sent=False))
        if self.stopping and self.stop_reason is not None:
            return skip(f"run stopping ({self.stop_reason}): no opening or closing by rule; the flatten covers the account")
        if role == "close":
            if st["open_confirmed"] is not True:
                return skip("its open was not confirmed by fills: never closed by an order, left to the flatten")
            if st["close_sent"]:
                return skip("a close for this trade was already sent: only once")
        if self.submits >= self.a.max_orders - 1:
            self.stop("order budget (max-orders minus one kept for the flatten)")
            return skip("order budget: nothing more is opened or closed by a rule")
        if role == "close":
            st["close_sent"] = True
        res = self.broker.send(intent, on_submit=self._count_submit)
        outcome = res["outcome"]
        if res["sent"]:
            self.counts["intents_sent"] += 1
        if role == "open":
            st["open_confirmed"] = bool(outcome and outcome["confirmed"])
        self.trade_orders.setdefault(tid, []).append(dict(role=role, external_identifier=intent["external_identifier"],
                                                          sent=res["sent"], reason=res["reason"], outcome=outcome))
        if self.submits >= self.a.max_orders - 1:
            self.stop("order budget (max-orders minus one kept for the flatten)")

    def _count_submit(self):
        self.submits += 1

    async def worker(self):
        while True:
            intent = await self.queue.get()
            if intent is None:
                return
            try:
                await asyncio.to_thread(self.execute, intent)
            except Exception as e:     # recorded; the loop goes on, nothing is re-sent
                self.ctx["rec"].note(f"order worker error: {bt.scrub(type(e).__name__ + ': ' + str(e))}")
                self.ctx["failed"].append({"step": "worker", "note": bt.scrub(str(e))})

    # ---- start check, flatten, final pull (blocking: called through asyncio.to_thread)
    def start_check(self):
        pos = self.broker.pull("positions")
        live = self.broker.pull("live")
        if pos is None or live is None:
            return "the start check could not read positions or live orders"
        if bt.read_positions(pos) or (bt.dget(live, "data", "items") or []):
            return "the account holds a position or a live order at the start: nothing sent"
        return None

    def flatten(self):
        if getattr(self, "flatten_started", False):
            return                      # never a second flatten, whatever failed after the first
        self.flatten_started = True
        pos = self.broker.pull("positions")
        if pos is None:
            self.flatten_orders.append(dict(sent=False, reason="positions could not be read: not flattened"))
            return
        positions = bt.read_positions(pos)
        self.left = [p for p in positions if p["symbol"] != self.a.symbol]
        net = bt.net_position(positions, self.a.symbol)
        if net is None:
            self.flatten_orders.append(dict(sent=False, reason="a position could not be signed: not flattened"))
        elif net != 0:
            intent = dict(role="flatten", strategy=None, trade_id=None, action="Sell" if net > 0 else "Buy",
                          quantity=abs(net), symbol=self.a.symbol, note=bt.FLATTEN_NOTE,
                          external_identifier=safe_id(f"{self.run_id}-flatten"), event_at=None)
            res = self.broker.send(intent, on_submit=self._count_submit)
            self.flatten_orders.append(dict(external_identifier=intent["external_identifier"], note=bt.FLATTEN_NOTE,
                                            action=intent["action"], quantity=intent["quantity"], **res))
            if res["sent"]:
                self.counts["intents_sent"] += 1

    def final_pull(self):
        self.final = {"positions": self.broker.pull("positions"), "live_orders": self.broker.pull("live")}
        self.final_reads = {}
        for tid, lst in self.trade_orders.items():
            for o in lst:
                oid = (o.get("outcome") or {}).get("order_id")
                if oid is not None:
                    o["final_read"] = self.final_reads[oid] = self.broker.final_read(oid)
        for o in self.flatten_orders:
            oid = (o.get("outcome") or {}).get("order_id")
            if oid is not None:
                o["final_read"] = self.broker.final_read(oid)

    # ---- the live run
    async def run_live(self):
        from tasty.paper import stream_tasty
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: self.stop(f"signal {signal.Signals(s).name}"))
        self.ctx["creds"] = bt.read_creds("sandbox")
        self.broker = bt.Broker(self.ctx, self.a.account, self.a.symbol)
        self.queue = asyncio.Queue()
        await asyncio.to_thread(bt.mint, self.ctx)
        if self.orders_on:
            why = await asyncio.to_thread(self.start_check)
            if why:
                self.stop(why)
                return
        url, token = await asyncio.to_thread(stream_tasty.quote_token, self.ctx)
        w = asyncio.create_task(self.worker()) if self.orders_on else None
        try:
            await stream_tasty.stream(url, token, [self.a.streamer_symbol], self.feed, self.on_frame, self.should_stop)
            self.stop(self.stop_reason or "stream ended")
        except stream_tasty.StreamDropped as e:
            self.stop(f"stream dropped: {e}")
        except bt.StepStop as e:
            self.stop(f"stream step stopped: {e}")
        finally:
            self.stopping = True
            if w is not None:
                await self.queue.put(None)
                await w
        if self.orders_on:
            await asyncio.to_thread(self.flatten)
            await asyncio.to_thread(self.final_pull)

    def run_replay(self):
        with open(self.a.replay, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                o = json.loads(line)
                if o.get("kind") != "received":
                    continue
                self.on_frame(o["raw"], int(o["t_ns"]), int(o["msg_no"]), o.get("host"))
                if self.stopping:
                    break
        if self.stop_reason is None:
            self.stop_reason = "recording ended"

    # ---- the end
    def finish(self, args_dict, start_check_note=None):
        for r in (self.feed, self.events_rec, self.ctx["rec"]):
            r.close()
        lines = []
        for rec in self.engine.ledger.records():
            lines.append(json.dumps(dict(rec, orders=self.trade_orders.get(rec["trade_id"], [])), sort_keys=True,
                                    default=str))
        bt.write_text_file(self.ctx, "ledger.jsonl", "".join(l + "\n" for l in lines))
        files = [dict(path=f, bytes=os.path.getsize(f), sha256=bt.sha256_file(f)) for f in self.ctx["files"]]
        receipt = dict(
            what="paper runner on the tastytrade sandbox", note=NOT_SCORED, run_id=self.run_id,
            code_commit=bt.code_commit(), arguments=args_dict, stopped_by=bt.scrub(self.stop_reason),
            mode=dict(replay=self.replay, orders_flag=bool(self.a.orders), orders_sent_to_sandbox=self.orders_on),
            counts=dict(self.counts, submits=self.submits, engine=self.engine.counts()),
            trades_in_ledger=len(lines), flatten_orders=self.flatten_orders,
            account_positions_left_not_flattened=getattr(self, "left", None),
            failed_steps=self.ctx["failed"],
            constants_used=dict(
                sandbox_host=bt.SANDBOX_HOST, run_cap_seconds=RUN_CAP_SECONDS, feed_stop_et=FEED_STOP_ET,
                wall_stop_et=WALL_STOP_ET, halt_hour_et=bt.HALT_ET_HOURS, order_type=bt.ORDER_TYPE,
                time_in_force=bt.ORDER_TIME_IN_FORCE, quantity=bt.ORDER_QUANTITY, poll_count=bt.POLL_COUNT,
                poll_seconds=bt.POLL_SECONDS, lookup_max_pages=bt.LOOKUP_MAX_PAGES,
                http_timeout_seconds=bt.HTTP_TIMEOUT_SECONDS, max_attempts=bt.MAX_ATTEMPTS, backoff_rule=bt.BACKOFF_RULE,
                terminal_statuses=sorted(bt.TERMINAL), user_agent=bt.USER_AGENT,
                budget_rule="when submits reach max_orders - 1 nothing more is opened or closed by a rule; the run flattens and stops",
                flatten_rule="one order of the absolute net quantity of --symbol, opposite side, marked: " + bt.FLATTEN_NOTE,
                external_identifier_rule="<run id>-<strategy>-<trade id>-<open|close>, characters outside A-Za-z0-9._- replaced by _"),
            files=files, not_scored=NOT_SCORED, not_exercised_live=NOT_EXERCISED_LIVE)
        rpath = os.path.join(self.out, "receipt.json")
        bt.write_text_file(self.ctx, "receipt.json", json.dumps(receipt, indent=1, sort_keys=True, default=str) + "\n")
        files.append(dict(path=rpath, bytes=os.path.getsize(rpath), sha256=bt.sha256_file(rpath)))
        bt.say("MANIFEST lines for the main session:")
        for e in files:
            bt.say(json.dumps(dict(e, source=f"tasty/paper/runner.py run {self.run_id} @ {receipt['code_commit']}")))
        return receipt


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="a markets-data folder; refused inside this code repository")
    ap.add_argument("--account")
    ap.add_argument("--symbol", help="the order symbol, e.g. /NGF7")
    ap.add_argument("--streamer-symbol", help="e.g. /NGF27:XNYM")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--max-orders", type=int, help="counts submits; one is kept back for the flatten")
    ap.add_argument("--replay", help="read this recording instead of the socket; sends NO orders")
    ap.add_argument("--orders", action="store_true", help="without it nothing is sent to the sandbox")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.replay:
        for n in ("account", "symbol", "streamer_symbol", "seconds", "max_orders"):
            if getattr(a, n) is None:
                ap.error(f"--{n.replace('_', '-')} is required (nothing is defaulted)")
        if a.seconds <= 0 or a.max_orders < 1:
            ap.error("--seconds must be more than 0 and --max-orders 1 or more")
    else:
        a.seconds = a.seconds if a.seconds is not None else float("inf")   # replay: read the whole recording
    out = bt.check_out_dir(a.out)          # exits, naming the folder, if it is inside the code repository
    os.makedirs(out, exist_ok=True)
    args_dict = {k: v for k, v in vars(a).items()}
    if args_dict["seconds"] == float("inf"):
        args_dict["seconds"] = "not given (replay reads the whole recording)"
    r = Runner(a, out)
    code = 0
    try:
        if r.replay:
            r.run_replay()
        else:
            asyncio.run(r.run_live())
    except BaseException as e:      # everything: the flatten and the receipt are always attempted
        r.stop(f"{type(e).__name__}: {e}")
        code = 130 if isinstance(e, KeyboardInterrupt) else 1
        bt.say(f"STOPPED: {r.stop_reason}")
        if r.orders_on and r.broker is not None:
            try:
                r.flatten()
                r.final_pull()
            except BaseException as e2:
                r.ctx["failed"].append({"step": "flatten after a stop", "note": bt.scrub(str(e2))})
    finally:
        try:
            r.finish(args_dict)
        except Exception as e:
            bt.say(f"receipt not written: {type(e).__name__}")
            code = code or 1
    return code


if __name__ == "__main__":
    sys.exit(main())
