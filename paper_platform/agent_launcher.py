"""Manual entry point for one Ccode-owned agent run under an authorized budget.

One run sends one written brief, with the files it names, to a model through the official SDK.
The model call passes one gate first. The gate takes the API's own count of the input, adds the
output reserve and refuses a call that would pass the token budget, so the model is never called.
The count itself is a request that carries the brief and runs no model. The clock budget is a timer
on the process. It ends the run at its second, wherever the run is. A budget stop, a run stop, a
failure and a completed run all take the same stop path. That path sends the phone notice first, then
publishes the record, and each goes even when the other fails. A start the launcher refuses also
reaches the phone. No budget figure has a default.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

import requests

from .run import notify, writer_remote
from .session import git

BUDGET_STOP = "budget reached"
USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


# The two stops are BaseException: the timer and the stop signal raise them inside the SDK's
# request, and the SDK reports any Exception raised there as a connection failure.
class BudgetStop(BaseException):
    """The run met an authorized limit."""


class RunStop(BaseException):
    """The runner was told to stop."""


class TokenBudget:
    """The token limit of one run. Every model call asks `admit` first."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.spent = 0

    def admit(self, input_tokens, output_reserve):
        if self.spent + input_tokens + output_reserve > self.tokens:
            raise BudgetStop(
                f"{BUDGET_STOP}: {self.spent} tokens spent, the next call counts {input_tokens} input "
                f"and reserves {output_reserve} output, the limit is {self.tokens}"
            )

    def charge(self, usage):
        self.spent += sum(value for value in usage.values() if value is not None)


def request_text(brief, attachments):
    """The brief, then each named file with numbered lines, and the fingerprint of every source."""
    parts, sources = [], []
    for position, path in enumerate([brief, *attachments]):
        raw = path.read_bytes()
        sources.append(dict(path=path.as_posix(), sha256=hashlib.sha256(raw).hexdigest()))
        text = raw.decode("utf-8")
        if position:
            lines = "\n".join(f"{number}: {line}" for number, line in enumerate(text.splitlines(), 1))
            text = f"FILE {path.as_posix()}\n{lines}"
        parts.append(text)
    return "\n\n".join(parts), sources


def disarm():
    signal.setitimer(signal.ITIMER_REAL, 0)
    for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)


def arm(seconds):
    """At most one stop is ever raised: the first one to arrive turns the others off."""

    def clock(*_):
        disarm()
        raise BudgetStop(f"{BUDGET_STOP}: the {seconds}-second clock limit")

    def stopped(*_):
        disarm()
        raise RunStop

    signal.signal(signal.SIGALRM, clock)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stopped)
    signal.setitimer(signal.ITIMER_REAL, seconds)


def publish(root, *args):
    """One markets-data git step under a time limit, so a stalled push cannot hold the stop path."""
    subprocess.run(["git", "-C", str(root), *args], check=True, timeout=25, stdout=subprocess.DEVNULL)


def tell(reason, run):
    """Send one phone notice. Returns None when it went, or why it did not."""
    try:
        notify(reason, run, activity="agent run")
    except (requests.RequestException, RuntimeError, KeyError) as error:
        return "phone notice not delivered: " + type(error).__name__
    return None


def finish(data_root, folder, record, outcome):
    """The one stop path. The phone hears first. The notice and the record each go even when the other fails."""
    record["outcome"] = outcome
    unheard = tell(outcome, record["run"])
    record["first_phone_notice"] = unheard or "delivered"
    unpublished = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        publish(data_root, "add", "--", folder.relative_to(data_root).as_posix())
        publish(data_root, "-c", "user.name=NG agent run", "-c", "user.email=paper@users.noreply.github.com",
                "commit", "-qm", "Record agent run " + record["run"] + " [skip ci]")
        publish(data_root, "push", "origin", "HEAD:refs/heads/agent-runs/" + record["run"])
    except (OSError, subprocess.SubprocessError) as error:
        unpublished = "record not published: " + type(error).__name__
    if unheard or unpublished:
        # One more notice, carrying what the first could not: the outcome itself, or the record's fate.
        unheard = tell(". ".join(part for part in (outcome, unpublished) if part), record["run"])
    return [error for error in (unpublished, unheard) if error]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--brief", type=Path, required=True)
    p.add_argument("--attach", type=Path, action="append", default=[])
    p.add_argument("--model", required=True)
    p.add_argument("--output-reserve", type=int, required=True)
    p.add_argument("--token-budget", type=int, required=True)
    p.add_argument("--seconds-budget", type=int, required=True)
    p.add_argument("--greg-go", action="store_true")
    args = p.parse_args()
    if not args.greg_go:
        p.error("Greg must start this run")

    def refuse(reason):
        # A start Greg made and the launcher refuses is a failure. The phone hears of it when both
        # Pushover credentials are present; without them no notice can go and the refusal stands.
        if os.environ.get("PUSHOVER_APP_TOKEN") and os.environ.get("PUSHOVER_USER_KEY"):
            tell("not started: " + reason, args.run if re.fullmatch("[0-9a-f]{32}", args.run) else "preflight")
        p.error(reason)

    if not re.fullmatch("[0-9a-f]{32}", args.run):
        refuse("the run identifier is 32 lowercase hexadecimal characters")
    if min(args.output_reserve, args.token_budget, args.seconds_budget) <= 0:
        refuse("the output reserve, the token budget and the clock budget must each be positive")
    for key in ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(key):
            refuse("required run credential missing: " + key)
    # The brief and its files go to the model and onto a markets-data branch, so only files committed
    # in the platform repository are read. A path elsewhere on the runner is refused.
    platform = Path(__file__).resolve().parents[1]
    for path in (args.brief, *args.attach):
        try:
            git(platform, "ls-files", "--error-unmatch", "--", path.resolve().relative_to(platform).as_posix())
        except (ValueError, subprocess.CalledProcessError):
            refuse("the brief and its files must be committed in the platform repository: " + path.as_posix())
    data_root = args.data.resolve()
    try:
        # The same check the paper runner makes: one push destination, and it is markets-data.
        writer_remote(data_root)
    except ValueError as error:
        refuse(str(error))
    except subprocess.CalledProcessError:
        refuse("--data is not a markets-data checkout")
    folder = data_root / "paper-trading" / "agent-runs" / args.run
    budget = TokenBudget(args.token_budget)
    began = time.monotonic()
    record = dict(
        run=args.run, activity="agent run", model=args.model, platform_commit=None,
        actions_run=os.environ.get("GITHUB_RUN_ID"), started_ns=time.time_ns(), ended_ns=None, elapsed_seconds=None,
        budget=dict(tokens=args.token_budget, seconds=args.seconds_budget, output_reserve=args.output_reserve),
        sources=None, input_tokens_counted=None, call_sent=False, usage=None, served_model=None,
        stop_reason=None, message_id=None,
    )
    outcome, failure = "failure", None
    try:
        try:
            arm(args.seconds_budget)
            folder.mkdir(parents=True)
            record["platform_commit"] = git(Path(__file__).resolve().parent, "rev-parse", "HEAD")
            text, record["sources"] = request_text(args.brief, args.attach)
            (folder / "request.txt").write_text(text, encoding="utf-8")
            # The record's destination is proven before any request that costs tokens.
            publish(data_root, "push", "--dry-run", "origin", "HEAD:refs/heads/agent-runs/" + args.run)
            import anthropic

            # No SDK retry: a retried request would be a second model call outside the gate.
            client = anthropic.Anthropic(max_retries=0)
            messages = [{"role": "user", "content": text}]
            counted = client.messages.count_tokens(model=args.model, messages=messages).input_tokens
            record["input_tokens_counted"] = counted
            budget.admit(counted, args.output_reserve)
            record["call_sent"] = True
            with client.messages.stream(model=args.model, max_tokens=args.output_reserve, messages=messages) as stream:
                message = stream.get_final_message()
            usage = {key: getattr(message.usage, key, None) for key in USAGE}
            budget.charge(usage)
            record.update(usage=usage, served_model=message.model, stop_reason=message.stop_reason,
                          message_id=message.id)
            answer = "".join(block.text for block in message.content if block.type == "text")
            (folder / "answer.txt").write_text(answer, encoding="utf-8")
            if budget.spent > budget.tokens:
                # The API's own usage came to more than its count had admitted. The answer is kept.
                outcome = f"{BUDGET_STOP}: the call used {budget.spent} tokens, the limit is {budget.tokens}"
            elif message.stop_reason == "end_turn":
                outcome = "completed"
            else:
                outcome = f"the model stopped: {message.stop_reason}"
        except BudgetStop as stop:
            outcome = str(stop)
        except RunStop:
            outcome = "run stopped"
        except Exception as error:
            outcome = "failure: " + type(error).__name__
            failure = error
        finally:
            disarm()
    except BudgetStop as stop:
        # The single stop arrived while the body was already ending.
        outcome = str(stop)
    except RunStop:
        outcome = "run stopped"
    record["tokens_spent"] = budget.spent
    if record["call_sent"] and record["usage"] is None:
        # The call went out and did not finish. The API reports usage at the end, so this is a ceiling.
        record["tokens_spent_at_most"] = record["input_tokens_counted"] + args.output_reserve
    record["ended_ns"] = time.time_ns()
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    errors = finish(data_root, folder, record, outcome)
    if failure is not None:
        raise failure
    if outcome != "completed" or errors:
        raise SystemExit(". ".join([outcome, *errors]))


if __name__ == "__main__":
    main()
