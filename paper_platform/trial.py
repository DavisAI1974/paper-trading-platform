"""One trial for the keep-if-better loop: a stretch of the stored tape through the same engine and matcher,
without waiting on the clock.

  python3 -m paper_platform.trial --data <markets-data checkout> --trade-day 2023-10-02 \
      --plan paper_platform/execution_plans/loop-trial.json --from-minute 30 --minutes 30 [--keep <folder>]

It is the paper run with three things taken away: the pacing (every decision is read on the tape's own
nanosecond times, so the clock on the wall decides nothing), the phone page, and the pushes to markets-data.
The session, the weekly book, the rules, the calculations and the paper matcher are the same objects a paper
run uses. The tape before the start point seats the book and warms the rule inputs and nothing is decided
there (`Session(paced_from_ns=...)`); the stretch ends at the first tape message at or after its end, the
timers due before that instant are fired, and what is still open is closed by the session's own stop, as
administrative closes that are not trade data.

With `--answers <file>` the trial takes in answers from outside (answers.py). Such a file is written before
the trial and is an input like the plan: every line is one answer and carries `not_before_tape_ns`, the tape
time before which it must not be taken (a file without it is refused). The trial hands an answer over at the
first step whose message is at or after that time, in the order of the file, and the session journals it as a
row of kind `answer`. A `close` answer is always journaled and not applied (closing by answer is not built). Without `--answers` nothing here changes.

With `--decider-mailbox <folder>` (and `--decider-timeout-s N`, default 600) a decider is asked at each opening
signal of a rule the plan trades: the session journals a `question` row, writes `<folder>/q-<sequence>/question.json`,
waits for `answer.json` beside it, and sends the opening order only on a `take`. A `skip`, a timeout or a bad answer
sends none. The wall time of the wait is recorded and is not added to any tape time. The paced run has no such gate.

It writes into a scratch copy and nowhere else: the journal, the receipt and one line for each trade
(`trades.jsonl`) are kept only when `--keep` names a folder. No network, no order to any venue, no notice.

The LAST line it prints is one JSON object. Its `figures` are counts of what was written and nothing else.
No result figure is among them: Greg has not chosen the figure a loop keeps or reverts by (2026-11-24), and
no total, average or ranking across trades is made here. Each trade's own result is on its own line of
`trades.jsonl`, in the tape's price units, from its own fills: no mid, no cost.
"""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from root.calendar import clock_days_needed, trade_day_bounds

from .answers import MailboxDecider, TimedAnswers
from .market import replay_market
from .session import Session, digest

STOP = "trial ended at the end of its stretch of tape"
ORIGIN = "https://github.com/DavisAI1974/markets-data.git"


def scratch_checkout(data, market_key, trade_day, scratch):
    """A scratch markets-data checkout holding only the stored files this trade day's week needs.

    The session writes its folder into the checkout it is given, so a trial is never given the real one.
    """
    archive = Path(replay_market(market_key)["archive"])
    source = Path(data).resolve() / archive
    root = Path(scratch) / "markets-data"
    (root / archive).mkdir(parents=True)
    manifest = json.loads((source / "MANIFEST.json").read_text(encoding="utf8"))
    shutil.copyfile(source / "MANIFEST.json", root / archive / "MANIFEST.json")
    wanted = {day.strftime("%Y%m%d") for day in clock_days_needed(trade_day)}
    for entry in manifest["files"]:
        name = entry.get("file", entry.get("name"))
        if any(day in name for day in wanted):
            os.symlink(source / name, root / archive / name)

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)

    git("init", "--quiet")
    git("remote", "add", "origin", ORIGIN)
    git("add", str(archive / "MANIFEST.json"))
    git("-c", "user.name=paper trial", "-c", "user.email=trial@localhost", "commit", "-qm", "the archive manifest")
    return root


def trade_lines(rows):
    """One line for each trade (one rule and one trade ID), from its own journal rows, in journal order."""
    trades = {}
    for row in rows:
        if row["kind"] not in ("fill", "paper order", "signal") or not row.get("trade_id"):
            continue
        key = row["rule"], row["trade_id"]
        trade = trades.setdefault(key, dict(rule=key[0], trade_id=key[1], side=None, fills=[], held=0,
                                            price_sum=0, administrative_close=False, close_reason=None,
                                            rule_close_reason=None))
        if row["kind"] == "signal":
            if row.get("action") in ("close", "exit") and row.get("trade_data") is not False:
                trade["rule_close_reason"] = row.get("reason")
        elif row["kind"] == "paper order":
            if row.get("action") != "open":
                trade["close_reason"] = row.get("reason")
        else:
            quantity, price = int(row["quantity"]), int(row["fill_price"])
            signed = quantity if row["side"] == "buy" else -quantity
            if not trade["fills"]:
                trade["side"] = row["side"]
            trade["held"] += signed
            trade["price_sum"] -= signed * price
            trade["administrative_close"] = trade["administrative_close"] or (
                row.get("action") != "open" and row.get("trade_data") is False)
            trade["fills"].append(dict(sequence=row["sequence"], action=row["action"], side=row["side"],
                                       quantity=quantity, fill_price=price, bid=row.get("bid"), ask=row.get("ask"),
                                       received_ns=row.get("fill_received_ns", row.get("received")),
                                       liquidity=row.get("liquidity"), trade_data=row.get("trade_data", True)))
    lines = []
    for trade in trades.values():
        if not trade["fills"]:
            continue
        first, last = trade["fills"][0], trade["fills"][-1]
        flat = trade["held"] == 0 and len(trade["fills"]) > 1
        lines.append(dict(
            rule=trade["rule"], trade_id=trade["trade_id"], side=trade["side"],
            in_received_ns=first["received_ns"], in_price=first["fill_price"],
            out_received_ns=last["received_ns"] if flat else None, out_price=last["fill_price"] if flat else None,
            contracts_held_at_end=trade["held"],
            # What this trade's own fills sold for less what they bought for, in the tape's price units.
            own_result_price_units=trade["price_sum"] if flat else None,
            closed_by_an_administrative_fill=trade["administrative_close"],
            close_reason=trade["close_reason"], rule_close_reason=trade["rule_close_reason"],
            fills=trade["fills"]))
    return lines


def figures(rows, lines):
    """Counts of what was written. Nothing here is a result."""
    return dict(
        journal_rows=len(rows),
        signals_open=sum(row["kind"] == "signal" and row.get("action") == "open" for row in rows),
        paper_orders=sum(row["kind"] == "paper order" for row in rows),
        fills=sum(row["kind"] == "fill" for row in rows),
        trades_opened=sum(any(fill["action"] == "open" for fill in line["fills"]) for line in lines),
        trades_holding_no_contract_at_end=sum(line["contracts_held_at_end"] == 0 for line in lines),
        trades_closed_by_an_administrative_fill=sum(line["closed_by_an_administrative_fill"] for line in lines),
    )


def quote_line(message):
    """One line of quotes.jsonl from a stepped message: the tape's own best bid and best ask, each as it is."""
    raw = message["raw"]
    line = dict(tape_received_ns=raw["ts_recv"], exchange_ns=raw["ts_event"],
                reference=[message["source_sha256"], message["record_index"]],
                bid=message["best_bid"], ask=message["best_ask"])
    for key, name in (("best_bid_size", "bid_size"), ("best_ask_size", "ask_size")):
        if key in message:  # only when the message carries them; this feed's does not
            line[name] = message[key]
    return line


def print_line(message):
    """One line of prints.jsonl from a stepped message that is a trade print."""
    raw = message["raw"]
    return dict(tape_received_ns=raw["ts_recv"], exchange_ns=raw["ts_event"],
                reference=[message["source_sha256"], message["record_index"]],
                price=raw["price"], size=raw["size"], aggressor=raw["side"])


class MarketSeries:
    """The market's own series for the stretch, written as the tape is stepped through, in tape time order.

    quotes.jsonl: a line each time the best bid or best ask changes (and one for the first message of the stretch).
    prints.jsonl: a line for every trade print. Kept for learning after the fact; the session never reads it.

    A message is written only when the trial DECIDES on it (`decided`, set by the trial from the session's own rule:
    not before the paced start on the receive clock, and inside the trade day on the exchange clock). A write that
    raises stops the series, never the trial: the failure is kept in `failure`.
    """

    def __init__(self, folder, begin_ns):
        self.begin_ns = begin_ns
        self.folder = Path(folder)
        self.quotes = (self.folder / "quotes.jsonl").open("w", encoding="utf8", newline="\n")
        self.prints = (self.folder / "prints.jsonl").open("w", encoding="utf8", newline="\n")
        self.count = dict(quotes=0, prints=0)
        self.standing = None
        self.first = True
        self.failure = None
        self.first_tape_received_ns = self.last_tape_received_ns = None

    def add(self, message, decided):
        if self.failure is not None or message["period"] == "vendor snapshot":
            return
        try:
            line = quote_line(message)
            standing = tuple(v for k, v in line.items() if k in ("bid", "ask", "bid_size", "ask_size"))
            changed = standing != self.standing
            self.standing = standing  # messages before the first decided one are read too, not written
            if not decided:
                return
            if self.first_tape_received_ns is None:
                self.first_tape_received_ns = line["tape_received_ns"]
            self.last_tape_received_ns = line["tape_received_ns"]
            if changed or self.first:
                self.quotes.write(json.dumps(line, separators=(",", ":")) + "\n")
                self.count["quotes"] += 1
            self.first = False
            if message["raw"]["action"] == "T":
                self.prints.write(json.dumps(print_line(message), separators=(",", ":")) + "\n")
                self.count["prints"] += 1
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"

    def close(self):
        for name in ("quotes", "prints"):
            try:
                getattr(self, name).close()
            except Exception as error:
                self.failure = self.failure or f"closing {name}.jsonl: {type(error).__name__}: {error}"

    def finish(self, begin, end):
        """After close: the hash of each file and market-series.json beside them. Returns what the last line prints."""
        out = dict(self.count)
        hashes = {}
        for name in ("quotes.jsonl", "prints.jsonl"):
            try:
                hashes[name] = digest(self.folder / name)
            except Exception as error:
                self.failure = self.failure or f"hashing {name}: {type(error).__name__}"
        try:
            (self.folder / "market-series.json").write_text(json.dumps(dict(
                counts=self.count, sha256=hashes, first_tape_received_ns=self.first_tape_received_ns,
                last_tape_received_ns=self.last_tape_received_ns, stretch_tape_ns=[begin, end],
                boundary=BOUNDARY, failure=self.failure), indent=2), encoding="utf8")
            hashes["market-series.json"] = digest(self.folder / "market-series.json")
        except Exception as error:
            self.failure = self.failure or f"writing market-series.json: {type(error).__name__}"
        out.update(sha256=hashes, failure=self.failure)
        return out


BOUNDARY = ("a message is in the series when the trial decides on it: its exchange time is inside the trade day "
            "(ts_event from the 6 pm open), and, with --from-minute above 0, its receive time (ts_recv) is at or after "
            "the stretch's start; the end is the first message at or after the end on the receive clock")


def decided(message, begin_ns, from_minute):
    """True when the session decides on this message: the session's own test (warming is ts_recv < the paced start,
    set only when --from-minute is above 0) and the engine's in-trade-day test (`in_session`, the exchange clock)."""
    warming = bool(from_minute) and message["raw"]["ts_recv"] < begin_ns
    return bool(message["in_session"]) and not warming


def mailbox_used(folder):
    """The first q-* entry already in a decider's folder, or None. A used question folder would refuse its sequence."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    return next((entry.name for entry in sorted(folder.glob("q-*"))), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="a markets-data checkout holding the stored tape")
    parser.add_argument("--trade-day", type=date.fromisoformat, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--market", default="nymex-ng")
    parser.add_argument("--from-minute", type=float, default=0,
                        help="minutes after the trade day's 6 pm Eastern open at which the stretch begins")
    parser.add_argument("--minutes", type=float, required=True, help="minutes of tape in the stretch")
    parser.add_argument("--answers", type=Path,
                        help="a file of answers written before the trial, each with not_before_tape_ns")
    parser.add_argument("--decider-mailbox", type=Path,
                        help="a folder through which a decider answers take or skip at each opening signal")
    parser.add_argument("--decider-timeout-s", type=float, default=600)
    parser.add_argument("--decider-mbo-window-s", type=float,
                        help="seconds of raw MBO messages the decider's question carries (default: the dipole line's width)")
    parser.add_argument("--keep", type=Path, help="a folder to keep the journal, the receipt and the trade lines in")
    parser.add_argument("--keep-market", action="store_true",
                        help="with --keep: also keep the market's own quotes and prints for the stretch (quotes.jsonl, "
                             "prints.jsonl); never given to a decider")
    args = parser.parse_args()
    if args.from_minute < 0 or args.minutes <= 0:
        parser.error("the stretch begins at or after the open and holds a positive number of minutes")
    if args.keep_market and not args.keep:
        parser.error("--keep-market needs --keep")
    if args.decider_timeout_s <= 0:
        parser.error("the decider's timeout is a positive number of seconds")
    if args.decider_mbo_window_s is not None and (args.decider_mbo_window_s <= 0 or not args.decider_mailbox):
        parser.error("--decider-mbo-window-s is a positive number of seconds and needs --decider-mailbox")
    if args.decider_mailbox:
        used = mailbox_used(args.decider_mailbox)
        if used:
            parser.error(f"--decider-mailbox {args.decider_mailbox} already holds {used}: use a folder no trial has used")
    if args.answers:
        refusal = TimedAnswers.check(args.answers)
        if refusal:
            parser.error("the answers file is refused: " + refusal)
    opens, halts = trade_day_bounds(args.trade_day)
    begin = opens + round(args.from_minute * 60 * 1_000_000_000)
    end = begin + round(args.minutes * 60 * 1_000_000_000)
    if end > halts:
        parser.error("the stretch ends after the trade day's 5 pm Eastern")
    plan = json.loads(args.plan.read_text(encoding="utf8"))
    scratch = tempfile.mkdtemp(prefix="paper-trial-")
    try:
        root = scratch_checkout(args.data, args.market, args.trade_day, scratch)
        records = sum(row.get("records", 0) for row in
                      json.loads((root / replay_market(args.market)["archive"] / "MANIFEST.json").read_text())["files"])
        session = Session(root, args.trade_day, message_budget=records + 1, output_budget=10 * records + 1,
                          market_key=args.market, execution_plan=plan, plan_source=str(args.plan),
                          paced_from_ns=begin if args.from_minute else None,
                          answers=TimedAnswers(args.answers) if args.answers else None,
                          decider=MailboxDecider(args.decider_mailbox, args.decider_timeout_s)
                          if args.decider_mailbox else None,
                          decider_mbo_window_s=args.decider_mbo_window_s)
        series = series_failure = None
        if args.keep_market:
            kept = args.keep.resolve() / session.run
            kept.mkdir(parents=True)
            try:
                series = MarketSeries(kept, begin)
            except Exception as error:
                series_failure = f"opening the series files: {type(error).__name__}: {error}"
        try:
            # Peek at each raw record's receive time, as the paced runner does, and admit it with step().
            for stored in session.feed.week:
                for index in range(len(stored.records)):
                    record = stored.records[index]
                    if index >= stored.vendor_snapshot and int(record["ts_recv"]) >= end and int(record["ts_event"]) >= opens:
                        break
                    if session.state != "active":
                        break
                    before = session.last
                    session.step(session.cursor)
                    if series is not None and session.last is not before:
                        series.add(session.last, decided(session.last, begin, args.from_minute))
                else:
                    continue
                break
            # The timers due before the end of the stretch fire as they would with no message between.
            while session.state == "active" and (expiry := session.engine.next_expiry_ns) is not None and expiry < end:
                session.advance_clock(expiry)
        finally:
            session.stop(STOP)
            if series is not None:
                series.close()
        rows = [json.loads(line) for line in (session.folder / "events.jsonl").read_text(encoding="utf8").splitlines()]
        lines = trade_lines(rows)
        kept = None
        if args.keep:
            kept = args.keep.resolve() / session.run
            kept.mkdir(parents=True, exist_ok=args.keep_market)
            for name in ("events.jsonl", "receipt.json", "start.json"):
                shutil.copyfile(session.folder / name, kept / name)
            if args.answers:
                shutil.copyfile(args.answers, kept / "answers-input.jsonl")
            with (kept / "trades.jsonl").open("w", encoding="utf8", newline="\n") as out:
                for line in lines:
                    out.write(json.dumps(line, separators=(",", ":")) + "\n")
        kept_market = None
        if series is not None:
            kept_market = series.finish(begin, end)
        elif series_failure:
            kept_market = dict(failure=series_failure)
        print(json.dumps(dict(
            figures=figures(rows, lines), run=session.run, status=session.state, trade_day=args.trade_day.isoformat(),
            stretch_tape_ns=[begin, end], code_commit=session.revision, plan=str(args.plan), exit=plan.get("exit"),
            messages=session.cursor + 1, kept=str(kept) if kept else None,
            **(dict(kept_market=kept_market) if kept_market is not None else {})), separators=(",", ":")))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
