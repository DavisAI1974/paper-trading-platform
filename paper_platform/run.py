"""Manual Actions entry point. Replay only. Any interruption closes the quote-only run."""

import argparse
import base64
from collections import deque
from datetime import date
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit

import requests
from .api import wire
from .causal_gate import PreflightDeadline
from .session import Session, git
from .run_clock import CLOCK_STOP, LIMIT_SECONDS, run_start_ns
from root.calendar import trade_day_bounds


def notify(reason, run, *, activity="paper run"):
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": os.environ["PUSHOVER_APP_TOKEN"],
            "user": os.environ["PUSHOVER_USER_KEY"],
            "message": f"NG {activity} {run}: {reason}",
        },
        timeout=5,
    )
    if not response.ok:
        raise RuntimeError("phone notice delivery failed")


def writer_remote(data_root):
    urls = git(data_root, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if len(urls) != 1:
        raise ValueError("configure a single markets-data push destination")
    remote = urls[0].lower().removesuffix(".git")
    if remote == "https://github.com/davisai1974/markets-data":
        if not os.environ.get("MARKETS_DATA_TOKEN"):
            raise ValueError("HTTPS publication requires MARKETS_DATA_TOKEN")
    elif remote != "git@github.com:davisai1974/markets-data":
        raise ValueError("paper receipts must be pushed to DavisAI1974/markets-data")
    return urls[0]


class Publication:
    """Publish on a dedicated run branch. Stage only the run directory; keep credentials in memory."""

    def __init__(self, session, url, push_url):
        self.s = session
        self.url = url
        self.push_url = push_url
        self.last_push = time.monotonic()
        self.commit = None
        self.tree = None
        self.published_commit = None
        self.http = requests.Session()
        self.http.headers["Authorization"] = "Bearer " + os.environ["PAPER_RELAY_TOKEN"]
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if push_url.lower().startswith("https://"):
            basic = base64.b64encode(("x-access-token:" + os.environ["MARKETS_DATA_TOKEN"]).encode()).decode()
            self.env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0=f"http.{push_url}.extraheader",
                GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + basic,
            )
        self.branch = "paper-runs/" + session.run

    def command(self, *args, read_output=False):
        proc = subprocess.run(
            ["git", "-C", str(self.s.data_root), *args],
            env=self.env,
            stdout=subprocess.PIPE if read_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=25,
        )
        if proc.returncode:
            raise RuntimeError("markets-data git " + args[0] + " failed")
        return proc.stdout.strip() if read_output else None

    def publish_commit(self):
        self.command("push", self.push_url, self.commit + ":refs/heads/" + self.branch)
        self.published_commit = self.commit

    def push(self):
        if self.tree is not None and self.published_commit != self.commit:
            self.publish_commit()
        s = self.s
        path = s.folder.relative_to(s.data_root).as_posix()
        if self.commit is None:
            git_dir = Path(self.command("rev-parse", "--absolute-git-dir", read_output=True))
            self.env["GIT_INDEX_FILE"] = str(git_dir / ("paper-index-" + s.run))
            base = self.command("rev-parse", "HEAD", read_output=True)
            self.command("read-tree", base)
            self.commit = base
        # Freeze the journal and receive sidecar together. The commit consumes this
        # tree even if a watchdog subsequently records a stop or administrative fill.
        with s.lock:
            if not s.receives.closed:
                s.receives.flush()
            self.command("add", "--sparse", "--", path)
            tree = self.command("write-tree", read_output=True)
        if tree != self.tree:
            self.commit = self.command(
                "-c", "user.name=NG Paper Recorder", "-c", "user.email=paper@users.noreply.github.com",
                "commit-tree", tree, "-p", self.commit, "-m", "Append paper events [skip ci]", read_output=True,
            )
            self.tree = tree
        if self.published_commit != self.commit:
            self.publish_commit()
        self.last_push = time.monotonic()

    def send(self, event):
        response = self.http.post(
            self.url + "/ingest", json=self.packet(event), timeout=8, allow_redirects=False
        )
        if response.status_code == 409:
            target = response.json().get("detail")
            if (
                isinstance(target, dict)
                and type(target.get("next")) is int
                and 0 <= target["next"] < event["sequence"]
            ):
                with (self.s.folder / "events.jsonl").open(encoding="utf8") as f:
                    for line in f:
                        old = json.loads(line)
                        if target["next"] <= old["sequence"] <= event["sequence"]:
                            self.post_again(old)
                return
        if response.status_code != 200:
            raise RuntimeError("Render event delivery failed")

    def packet(self, event):
        status = self.s.status()
        status["events"] = event["sequence"] + 1
        status["status"] = (
            event["reason"] if event["kind"] == "status" and event["reason"] != "started" else "active"
        )
        if status["status"] == "active":
            status.pop("capture_end", None)
        else:
            status["capture_end"] = event.get("capture_end")
            status["last_source"] = event.get("last_source")
            if "shutdown" in event:
                status["shutdown"] = event["shutdown"]
            if "remaining_paper_inventory" in event:
                status["paper_inventory"] = event["remaining_paper_inventory"]
        return {"event": wire(event), "status": wire(status)}

    def post_again(self, event):
        response = self.http.post(
            self.url + "/ingest", json=self.packet(event), timeout=8, allow_redirects=False
        )
        if response.status_code != 200:
            raise RuntimeError("Render journal recovery failed")


def pace_to_record(session, received, base, stopped, publication, deliver):
    """Release due timers against the tape clock even when there is no intervening print."""
    while session.state == "active" and not stopped.is_set() and not session.due():
        with session.lock:
            expiry = session.engine.next_expiry_ns
        timer_first = expiry is not None and expiry < received
        target = expiry if timer_first else received
        remaining = base + target - time.monotonic_ns()
        if remaining > 0:
            stopped.wait(min(0.25, remaining / 1e9))
            if time.monotonic() - publication.last_push >= 60:
                publication.push()
        elif timer_first:
            session.advance_clock(expiry)
            deliver()
        else:
            return


def read_execution_plan(args):
    if args.execution_plan:
        return json.loads(args.execution_plan.read_text(encoding="utf-8")), args.execution_plan.name
    selector = args.agent_plan
    if selector is None:
        return None, None
    if selector.lstrip().startswith("{"):
        return json.loads(selector), "workflow input"
    root = Path(__file__).resolve().parent / "execution_plans"
    if Path(selector).name != selector or not selector.endswith(".json"):
        raise ValueError("supply an agent plan filename ending in .json or a JSON object")
    path = (root / selector).resolve()
    if path.parent != root:
        raise ValueError("named plans belong in paper_platform/execution_plans")
    return json.loads(path.read_text(encoding="utf-8")), f"paper_platform/execution_plans/{selector}"


def main():
    started = run_start_ns() / 1_000_000_000
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--trade-day", type=date.fromisoformat, required=True)
    p.add_argument("--market", default="nymex-ng")
    p.add_argument("--message-budget", type=int, required=True)
    p.add_argument("--event-budget", type=int, required=True)
    capture = p.add_mutually_exclusive_group()
    capture.add_argument("--capture", type=Path)
    capture.add_argument("--replay-capture", type=Path)
    p.add_argument("--capture-commit")
    p.add_argument("--capture-connection", type=int)
    plans = p.add_mutually_exclusive_group()
    plans.add_argument("--execution-plan", type=Path)
    plans.add_argument("--agent-plan")
    plans.add_argument("--replay-receipt", type=Path)
    p.add_argument("--greg-go", action="store_true")
    args = p.parse_args()
    if args.replay_receipt and not args.replay_capture:
        p.error("select the stored live capture for receipt-based replay")
    if args.replay_capture and args.capture_connection is not None:
        p.error("a stored capture has no active recorder connection")
    if not args.greg_go:
        p.error("Greg must start this run")
    url = os.environ["PAPER_RELAY_URL"].rstrip("/")
    if urlsplit(url).scheme != "https" or not urlsplit(url).hostname or urlsplit(url).username:
        p.error("Render must use an HTTPS URL")
    for key in ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "PAPER_RELAY_TOKEN"):
        if not os.environ.get(key):
            p.error("required run credential missing: " + key)
    try:
        push_url = writer_remote(args.data)
    except ValueError as error:
        p.error(str(error))
    s = pub = None
    finished = threading.Event()
    alert_sent = threading.Event()
    why = "failure"
    try:
        if time.monotonic() >= started + LIMIT_SECONDS:
            raise PreflightDeadline("5.75-hour limit reached before paper startup")
        requests.get(url + "/health", timeout=90).raise_for_status()
        receipt = None
        if args.replay_receipt:
            receipt = json.loads(args.replay_receipt.read_text(encoding="utf-8"))
            if receipt["run"] != args.replay_receipt.parent.name:
                raise ValueError("receipt belongs to a different paper run")
            execution_plan = receipt["execution_plan"]
            plan_source = "receipt for " + receipt["run"]
            args.message_budget = min(args.message_budget, receipt["constants"]["message_budget"])
            args.event_budget = min(args.event_budget, receipt["constants"]["event_budget"])
        else:
            execution_plan, plan_source = read_execution_plan(args)
        s = Session(
            args.data,
            args.trade_day,
            message_budget=args.message_budget,
            output_budget=args.event_budget,
            market_key=args.market,
            started=started, captured=args.capture or args.replay_capture, capture_connection=args.capture_connection,
            replay_capture=args.replay_capture is not None, capture_commit=args.capture_commit, replay_receipt=receipt,
            execution_plan=execution_plan, plan_source=plan_source,
        )
        pub = Publication(s, url, push_url)
        pending = deque()
        s.on_event = pending.append
        with (s.folder / "events.jsonl").open(encoding="utf8") as f:
            pending.extend(json.loads(row) for row in f)
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopped.set())
        opens, halts = trade_day_bounds(args.trade_day)
        base = None
        indices = () if s.captured else ((f, i) for f in s.feed.week for i in range(len(f.records)))

        def guard():
            while not finished.wait(0.25):
                if s.state != "active":
                    return
                end = None
                if s.captured and args.capture_connection is not None:
                    try:
                        end = s.feed.terminal_state()
                    except (OSError, sqlite3.Error, RuntimeError):
                        end = dict(connection_id=args.capture_connection, status="capture journal unreadable")
                if end is not None:
                    s.stop_capture(end)
                    return
                if reason := s.due():
                    s.stop(reason)
                    return

        def send_stop_notice():
            s.stopping.wait()
            try:
                notify(s.stop_notice(), s.run)
                alert_sent.set()
            except (requests.RequestException, RuntimeError):
                pass  # The finalizer retries if Pushover did not acknowledge delivery.

        notice_worker = threading.Thread(target=send_stop_notice, daemon=True)
        notice_worker.start()

        watcher = threading.Thread(target=guard, daemon=True)
        watcher.start()

        def deliver_relay(*, final=False):
            while pending and (final or s.state == "active"):
                pub.send(pending[0])
                pending.popleft()

        def deliver():
            # One market tick can emit a signal, an order and several fills together.
            # Take their Git snapshot before an earlier signal can block on Render.
            with s.lock:
                urgent = any(event["kind"] in ("paper order", "fill") for event in pending)
            if urgent or time.monotonic() - pub.last_push >= 60:
                pub.push()
            deliver_relay()

        while s.state == "active":
            if stopped.is_set():
                why = "run stopped"
                break
            if s.due():
                why = s.due()
                break
            deliver()
            if s.captured:
                if s.step(s.cursor) is None and s.state == "active":
                    stopped.wait(0.1)
                continue
            # Peek at the next raw receive time without exposing it to any strategy.
            # The scheduler paces delivery; only step() admits that record into the engine.
            next_row = next(indices, None)
            if next_row:
                f, i = next_row
                r = f.records[i]
                if i >= f.vendor_snapshot and opens <= int(r["ts_event"]) < halts:
                    received = int(r["ts_recv"])
                    if base is None:
                        base = time.monotonic_ns() - received
                    pace_to_record(s, received, base, stopped, pub, deliver)
                elif base is not None and not s.engine.ended and i >= f.vendor_snapshot and int(r["ts_event"]) >= halts:
                    pace_to_record(s, int(r["ts_recv"]), base, stopped, pub, deliver)
            if stopped.is_set() or s.due():
                continue
            s.step(s.cursor)
        if s.state != "active":
            why = s.state
    except PreflightDeadline as error:
        why = str(error)
        raise
    except Exception as error:
        if args.capture:
            from .capture import CaptureEnded
            if isinstance(error, CaptureEnded):
                evidence = error.evidence
                why = f"{error} (connection {evidence['connection_id']}, last MBO {evidence['last_mbo_position']})"
        raise
    finally:
        # Attempt the phone, local receipt, relay and final push independently on failure.
        finished.set()
        if "watcher" in locals():
            watcher.join()
        errors = []
        if s is not None:
            try:
                s.stop(why)
                shutdown = s.shutdown_evidence
                if shutdown.get("errors") or (s.state == CLOCK_STOP and shutdown.get("native_flat") is False):
                    errors.append("administrative liquidation")
            except Exception:
                errors.append("close")
        if "notice_worker" in locals():
            notice_worker.join()
        try:
            if not alert_sent.is_set():
                notify(s.stop_notice() if s is not None else why, s.run if s else "preflight")
        except Exception:
            errors.append("phone")
        if pub is not None:
            try:
                pub.push()
            except Exception:
                errors.append("git")
            try:
                deliver_relay(final=True)
            except Exception:
                errors.append("relay")
        if errors:
            try:
                notify("failure during shutdown: " + ", ".join(errors), s.run if s else "preflight")
            except Exception:
                pass
            raise RuntimeError("shutdown failed: " + ", ".join(errors))


if __name__ == "__main__":
    main()
