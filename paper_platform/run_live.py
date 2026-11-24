"""Coordinate the recorder, checkpoint publisher and consumer for one manual Actions run."""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from root.calendar import week_sunday
from .live_contract import bind_live_plan
from .run import read_agent_plan
from .run_clock import LIMIT_SECONDS, run_start_ns


def launch(module, arguments, credentials):
    env = os.environ.copy()
    for key in ("DATABENTO_API_KEY", "PAPER_CAPTURE_KEY", "PAPER_RELAY_TOKEN",
                "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "MARKETS_DATA_TOKEN"):
        if key not in credentials:
            env.pop(key, None)
    return subprocess.Popen([sys.executable, "-m", "paper_platform." + module, *map(str, arguments)], env=env)


def finish(process, *, timeout=90):
    if process is None:
        return 0
    if process.poll() is None:
        process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--trade-day", type=date.fromisoformat, required=True)
    parser.add_argument("--message-budget", type=int, required=True)
    parser.add_argument("--event-budget", type=int, required=True)
    parser.add_argument("--greg-go", action="store_true")
    args = parser.parse_args()
    if not args.greg_go or min(args.message_budget, args.event_budget) <= 0:
        parser.error("Greg's go and positive budgets are required")
    started = run_start_ns()
    os.environ["PAPER_RUN_STARTED_NS"] = str(started)
    deadline = started / 1_000_000_000 + LIMIT_SECONDS
    if time.monotonic() >= deadline:
        from .run import notify
        notify("5.75-hour limit reached before capture startup", "preflight")
        return 1
    required = ("DATABENTO_API_KEY", "PAPER_CAPTURE_KEY", "PAPER_RELAY_URL", "PAPER_RELAY_TOKEN",
                "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        parser.error("missing live paper settings: " + ", ".join(missing))
    plan, plan_source = read_agent_plan(os.environ["EXECUTION_PLAN"])
    common = ["--data", args.data, "--trade-day", args.trade_day]
    week = week_sunday(args.trade_day).isoformat()
    db = args.data / "paper-trading" / "captures" / "nymex-ng" / week / "live.sqlite"
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    notices = {"PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"}
    recorder = paper = backup = None
    with tempfile.TemporaryDirectory() as temporary:
        ready = Path(temporary) / "connection"
        contract_file = Path(temporary) / "definition.dbn"
        bound_plan = Path(temporary) / "live-ng-plan.json"
        try:
            backup = launch("capture_backup", ["follow", *common, "--checkout", args.checkout, "--source", "live"],
                            {"PAPER_CAPTURE_KEY"})
            recorder = launch("record_mbo", [*common, "--source", "live", "--message-budget", args.message_budget,
                                              "--ready-file", ready, "--live-contract-file", contract_file,
                                              "--greg-go"], notices | {"DATABENTO_API_KEY"})
            while not contract_file.is_file():
                if stopping.wait(0.1) or time.monotonic() >= deadline:
                    return 1
                if recorder.poll() is not None or backup.poll() is not None:
                    raise RuntimeError("capture stopped before supplying the live NG contract definition")
            connection = int(ready.read_text(encoding="ascii"))
            if stopping.is_set() or time.monotonic() >= deadline or recorder.poll() is not None or backup.poll() is not None:
                return 1
            resolved = bind_live_plan(plan, plan_source, contract_file)
            bound_plan.write_text(json.dumps(resolved), encoding="utf-8")
            paper = launch("run", [*common, "--market", "nymex-ng", "--message-budget", args.message_budget,
                                   "--event-budget", args.event_budget, "--execution-plan", bound_plan, "--capture", db,
                                   "--capture-connection", connection, "--greg-go"],
                           notices | {"PAPER_RELAY_TOKEN", "MARKETS_DATA_TOKEN"})
            while paper.poll() is None and recorder.poll() is None and backup.poll() is None:
                if stopping.wait(0.2) or time.monotonic() >= deadline:
                    break
            backup_failed = backup.poll() is not None
            if recorder.poll() is None:
                if backup_failed:
                    recorder.send_signal(signal.SIGUSR1)
                else:
                    recorder.terminate()
                try:
                    recorder.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    recorder.kill()
                    recorder.wait()
            # The reader observes the committed reason (or the writer lock release).
            # Let it journal cancellations, remaining exposure and the terminal receipt.
            try:
                paper.wait(timeout=120)
            except subprocess.TimeoutExpired:
                finish(paper)
                return 1
            return int(backup_failed or stopping.is_set() or recorder.returncode != 0 or paper.returncode != 0)
        finally:
            finish(recorder)
            finish(paper)
            finish(backup, timeout=15)


if __name__ == "__main__":
    sys.exit(main())
