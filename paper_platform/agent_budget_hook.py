"""Hold a Claude Code subagent to the token budget on record.

Claude Code runs a PreToolUse hook before every tool call, inside a subagent too, and hands it the
call as JSON on standard input. Inside a subagent that JSON carries `agent_id`. The subagent's own
transcript is written as the run goes, at `<session transcript without .jsonl>/subagents/agent-<id>.jsonl`,
and every model call in it carries the API's own usage figures. This hook adds them up and, once the
run has passed the budget, refuses the tool call with the reason. Every later call of that run is
refused the same way. The first refusal writes a stop record beside the transcript and sends the
phone notice when the session holds the Pushover credentials.

The budget is the file `agent_budget.json` beside this script:

    {"measure": "last call", "tokens": 200000}

`measure` names what is counted, and it has no default:

- "last call": the size of the run's latest model call, all its input and its output together.
  This is the figure Claude Code reports for a subagent when it ends.
- "all calls": every model call of the run added together, all input and output of each.

With no budget file there is no budget on record and the hook does nothing. The main conversation
is never refused. A stop comes at the first tool call after the budget is passed, so the run goes
over by at least the model call that passed it, and a run that calls no more tools is not stopped.
When a budget is on record and the run's tokens cannot be counted, the call is refused: the budget
cannot be held.

Standard library only, so a session needs nothing installed to run it.
"""

import json
import os
from pathlib import Path
import sys
from urllib import parse, request

BUDGET = Path(__file__).with_name("agent_budget.json")
KINDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
MEASURES = ("last call", "all calls")


def figures(transcript):
    """The size of each model call. A call is written on several lines that share its message id."""
    calls = {}
    with transcript.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                message = json.loads(line).get("message")
            except ValueError:
                continue  # the line being written at this moment
            if isinstance(message, dict) and message.get("id") and isinstance(message.get("usage"), dict):
                calls[message["id"]] = sum(message["usage"].get(kind) or 0 for kind in KINDS)
    sizes = list(calls.values())
    return {"calls": len(sizes), "last call": sizes[-1] if sizes else 0, "all calls": sum(sizes)}


def phone(message):
    token, user = os.environ.get("PUSHOVER_APP_TOKEN"), os.environ.get("PUSHOVER_USER_KEY")
    if not token or not user:
        return "not sent: this session holds no Pushover credentials"
    body = parse.urlencode({"token": token, "user": user, "message": message}).encode()
    try:
        with request.urlopen("https://api.pushover.net/1/messages.json", body, timeout=5) as response:
            return "sent" if response.status == 200 else f"not delivered: HTTP {response.status}"
    except OSError as error:
        return "not delivered: " + type(error).__name__


def refuse(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason,
    }}))


def main():
    if not BUDGET.exists():
        return  # no budget is on record
    event = json.load(sys.stdin)
    agent = event.get("agent_id")
    if not agent:
        return  # the main conversation
    try:
        budget = json.loads(BUDGET.read_text(encoding="utf-8"))
        measure, limit = budget["measure"], budget["tokens"]
        if measure not in MEASURES or type(limit) is not int or limit <= 0:
            raise ValueError("measure must be 'last call' or 'all calls' and tokens a whole number above zero")
        folder = Path(event["transcript_path"]).with_suffix("") / "subagents"
        spent = figures(folder / f"agent-{agent}.jsonl")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        refuse(f"A token budget is on record and this run cannot be held to it ({type(error).__name__}: {error}). "
               "Call no more tools. Reply now with what you have and say why you stopped.")
        return
    if spent[measure] <= limit:
        return
    reason = (f"budget reached: {spent[measure]} tokens by the '{measure}' measure after {spent['calls']} model "
              f"calls, the limit is {limit}")
    record = folder / f"agent-{agent}.budget-stop.json"
    try:
        # Opening the record for exclusive creation makes the first refusal the only one that notifies.
        with record.open("x", encoding="utf-8") as stop:
            notice = phone(f"NG agent run {agent}: {reason}")
            stop.write(json.dumps(dict(
                agent_id=agent, agent_type=event.get("agent_type"), session_id=event.get("session_id"),
                budget=budget, figures=spent, refused_tool=event.get("tool_name"), phone_notice=notice,
            ), indent=2) + "\n")
    except OSError:
        pass  # the record is already written, or cannot be: the refusal still goes
    refuse(reason + ". Call no more tools. Reply now with what you have and say the budget stopped you.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Reached only when the call itself cannot be read. Exit 1 shows the line and blocks nothing.
        print(f"agent budget hook: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
