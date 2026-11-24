"""Answers taken in during a run: the one place a run takes in an answer from outside.

An answer is one JSON object on one line:

  {"id": "<unique>", "action": "rule_off" | "rule_on" | "close", "rule": "<rule id>", "trade_id": "<for close>",
   "by": "<who answered, e.g. run:0081>", "packet": {"sha256": "...", "tokens": int} or null,
   "asked_at_sequence": <journal sequence of the signal it answers> or null}

`rule` is required for rule_off and rule_on, `trade_id` for close. `packet` and `asked_at_sequence` may be left out
(they then read null). A line may also carry `not_before_tape_ns`, which only a trial's file needs (see
`TimedAnswers`). Nothing else is accepted.

A source has `take(tape_ns=None)`, which returns the answers that have arrived since the last call, in the order
they arrived, and never blocks. `tape_ns` is the receive time of the tape message the session is about to apply;
a source that is not paced by the tape ignores it. A line that cannot be read is returned as
`{"_malformed": "<reason>", "line": "<text>"}` and never raises. The wall clock decides nothing here: when an
answer is applied is set by the session, on the tape's own time.
"""

import json
import os

ACTIONS = ("rule_off", "rule_on", "close")
KEYS = {"id", "action", "rule", "trade_id", "by", "packet", "asked_at_sequence", "not_before_tape_ns"}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _whole(value):
    return type(value) is int


def validate(answer):
    """The reason an answer is malformed, or None."""
    if not isinstance(answer, dict):
        return "an answer is one JSON object"
    if "_malformed" in answer:
        return answer["_malformed"]
    if answer.keys() - KEYS:
        return "unknown field: " + ", ".join(sorted(answer.keys() - KEYS))
    if not _text(answer.get("id")):
        return "id is missing or empty"
    if answer.get("action") not in ACTIONS:
        return "action is not one of " + ", ".join(ACTIONS)
    if not _text(answer.get("by")):
        return "by is missing or empty"
    if answer["action"] == "close":
        if not _text(answer.get("trade_id")):
            return "a close names its trade_id"
        if "rule" in answer and not _text(answer["rule"]):
            return "rule is empty"
    elif not _text(answer.get("rule")):
        return answer["action"] + " names its rule"
    packet = answer.get("packet")
    if packet is not None and not (isinstance(packet, dict) and _text(packet.get("sha256")) and _whole(packet.get("tokens"))):
        return "packet is null or {sha256, tokens}"
    if answer.get("asked_at_sequence") is not None and not _whole(answer["asked_at_sequence"]):
        return "asked_at_sequence is null or a whole number"
    if "not_before_tape_ns" in answer and not _whole(answer["not_before_tape_ns"]):
        return "not_before_tape_ns is a whole number of nanoseconds"
    return None


class NoAnswers:
    """A source that never has an answer."""

    def take(self, tape_ns=None):
        return []

    def waiting(self):
        return 0


class FileAnswers:
    """Complete new lines appended to a JSON-lines file. A partly written last line waits for the next call.

    When the file has not grown the cost of a call is one `os.stat`. The file may not exist yet.
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        self.offset = 0
        self.lines = 0

    def take(self, tape_ns=None):
        try:
            size = os.stat(self.path).st_size
        except FileNotFoundError:
            return []
        if size == self.offset:
            return []
        if size < self.offset:
            self.offset = size
            return [{"_malformed": "the answers file is shorter than what was already taken", "line": ""}]
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        end = data.rfind(b"\n") + 1
        if not end:
            return []
        self.offset += end
        taken = []
        for raw in data[:end].split(b"\n")[:-1]:
            self.lines += 1
            if not raw.strip():
                continue
            try:
                text = raw.decode("utf8")
            except UnicodeDecodeError:
                taken.append({"_malformed": f"line {self.lines} is not UTF-8", "line": raw.decode("utf8", "replace")})
                continue
            try:
                answer = json.loads(text)
            except ValueError as error:
                taken.append({"_malformed": f"line {self.lines} is not JSON: {error}", "line": text})
                continue
            if not isinstance(answer, dict):
                taken.append({"_malformed": f"line {self.lines} is not one JSON object", "line": text})
                continue
            taken.append(answer)
        return taken

    def waiting(self):
        return 0


class TimedAnswers:
    """For a trial: a file written before the trial, whose every answer carries `not_before_tape_ns`.

    An answer is handed over at the first step whose message is at or after that time, and never before an
    answer that stands above it in the file: the answers leave in the order of the file.
    """

    def __init__(self, path):
        self.source = FileAnswers(path)
        self.held = []

    @staticmethod
    def check(path):
        """The reason a trial's file is refused, or None. Every line must be one readable answer with its time."""
        source = FileAnswers(path)
        for answer in source.take():
            reason = validate(answer)
            if reason is not None:
                return reason
            if "not_before_tape_ns" not in answer:
                return f"answer {answer['id']!r} has no not_before_tape_ns, which a trial needs"
        return None

    def take(self, tape_ns=None):
        self.held.extend(self.source.take())
        out = []
        while self.held:
            head = self.held[0]
            when = head.get("not_before_tape_ns")
            if tape_ns is None or (_whole(when) and when > tape_ns):
                break
            out.append(self.held.pop(0))
        return out

    def waiting(self):
        return len(self.held)
