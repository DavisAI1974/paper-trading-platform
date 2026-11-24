"""Checks of paper_platform/answers.py alone. The answer lines are written here; no tape, no market data."""

import json

from paper_platform.answers import FileAnswers, NoAnswers, TimedAnswers, validate


def line(**fields):
    base = dict(id="a1", action="rule_off", rule="CL01", by="run:0001")
    return json.dumps(dict(base, **fields)) + "\n"


def test_new_lines_are_taken_once_and_in_order(tmp_path):
    path = tmp_path / "answers.jsonl"
    source = FileAnswers(path)
    assert source.take() == []  # no file yet
    path.write_text(line(id="a1") + line(id="a2", action="rule_on"))
    assert [a["id"] for a in source.take()] == ["a1", "a2"]
    assert source.take() == []
    with path.open("a") as f:
        f.write(line(id="a3"))
    assert [a["id"] for a in source.take()] == ["a3"]


def test_a_half_written_line_waits(tmp_path):
    path = tmp_path / "answers.jsonl"
    source = FileAnswers(path)
    whole = line(id="a1")
    path.write_text(whole[:20])
    assert source.take() == []
    with path.open("a") as f:
        f.write(whole[20:])
    assert [a["id"] for a in source.take()] == ["a1"]


def test_a_malformed_line_is_returned_marked_and_does_not_raise(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text("not json\n[1]\n" + line(id="a2"))
    taken = FileAnswers(path).take()
    assert len(taken) == 3
    assert "_malformed" in taken[0] and taken[0]["line"] == "not json"
    assert "_malformed" in taken[1]
    assert validate(taken[0]) is not None
    assert validate(taken[2]) is None


def test_validate():
    ok = dict(id="a", action="close", trade_id="t", by="x", packet={"sha256": "ab", "tokens": 3}, asked_at_sequence=4)
    assert validate(ok) is None
    assert validate(dict(ok, action="buy")) is not None
    assert validate({k: v for k, v in ok.items() if k != "trade_id"}) is not None
    assert validate(dict(ok, packet={"sha256": "ab"})) is not None
    assert validate(dict(ok, extra=1)) is not None
    assert validate(dict(id="a", action="rule_on", by="x")) is not None
    assert validate(dict(id="a", action="rule_on", rule="CL01", by="x", asked_at_sequence=True)) is not None


def test_no_answers():
    assert NoAnswers().take(5) == []


def test_timed_answers_leave_in_file_order_and_not_before_their_time(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text(line(id="a1", not_before_tape_ns=200) + line(id="a2", not_before_tape_ns=100)
                    + line(id="a3", not_before_tape_ns=300))
    source = TimedAnswers(path)
    assert source.take(150) == []
    assert [a["id"] for a in source.take(200)] == ["a1", "a2"]
    assert source.waiting() == 1
    assert [a["id"] for a in source.take(999)] == ["a3"]


def test_a_trial_file_without_a_time_is_refused(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text(line(id="a1", not_before_tape_ns=1) + line(id="a2"))
    assert "not_before_tape_ns" in TimedAnswers.check(path)
    path.write_text(line(id="a1", not_before_tape_ns=1))
    assert TimedAnswers.check(path) is None


def question(sequence=7):
    return dict(sequence=sequence, rule="CL01", side="buy", trade_id="t", tape_received_ns=1, journal="/x/events.jsonl")


def test_mailbox_writes_a_question_and_picks_up_the_answer(tmp_path):
    from paper_platform.answers import MailboxDecider
    answered = dict(action="take", asked_at_sequence=7, by="agent:1", packet=None, why="the flow agrees")

    def sleep(_):  # the other side answers while the session waits
        (tmp_path / "q-7" / "answer.json").write_text(json.dumps(answered))

    got = MailboxDecider(tmp_path, 5, 0, sleep=sleep).ask(question())
    asked = json.loads((tmp_path / "q-7" / "question.json").read_text())
    assert asked == question() and got == answered


def test_mailbox_times_out_with_none_and_refuses_a_wrong_answer(tmp_path):
    from paper_platform.answers import MailboxDecider
    now = iter(range(100))
    got = MailboxDecider(tmp_path, 3, 0, clock=lambda: next(now), sleep=lambda _: None).ask(question(8))
    assert got["action"] == "none" and "no answer" in got["reason"]
    def sleep(_):
        (tmp_path / "q-10" / "answer.json").write_text(json.dumps(dict(action="take", asked_at_sequence=3, by="a")))

    got = MailboxDecider(tmp_path, 5, 0, sleep=sleep).ask(question(10))
    assert got["action"] == "none" and "question was about 10" in got["reason"]


# The checks below call single methods on a Session built without a tape (object.__new__, attributes set by hand).

def bare_session(source, rows, **attributes):
    import hashlib
    from types import SimpleNamespace
    from paper_platform.session import Session
    s = object.__new__(Session)
    s.answers, s.answer_close = source, True
    s.answers_seen, s.answers_off, s.answers_unordered = set(), set(), set()
    s.answers_taken = s.answers_applied = 0
    s.answers_hash = hashlib.sha256()
    s.execution, s.rules, s.engine = None, {}, SimpleNamespace(last=None, open={})

    def write(kind, **fields):
        row = dict(kind=kind, sequence=len(rows), **fields)
        rows.append(row)
        return row

    s.write = write
    for key, value in attributes.items():
        setattr(s, key, value)
    return s


class Handing:
    def __init__(self, *answers):
        self.answers = list(answers)

    def take(self, tape_ns=None):
        out, self.answers = self.answers, []
        return out


def test_bad_answers_are_journaled_not_raised():
    rows = []
    bad = [
        dict(action="take", by="x", asked_at_sequence=3, not_before_tape_ns=1),  # no id
        dict(action="close", by="x", trade_id="t"),  # no id
        dict(id="b", action="rule_off", by="x"),  # no rule
        dict(id="c", action="nonsense", by="x"),
        {"_malformed": "line 1 is not JSON", "line": "x"},
    ]
    s = bare_session(Handing(*bad), rows)
    s._take_answers(5)
    assert len(rows) == 5 and all(r["kind"] == "answer" and r["applied"] is False and r["reason"] for r in rows)


def test_an_exception_inside_taking_or_applying_is_journaled():
    rows = []

    class Raises:
        def take(self, tape_ns=None):
            raise RuntimeError("source broke")

    bare_session(Raises(), rows)._take_answers(5)
    assert rows[0]["applied"] is False and "source broke" in rows[0]["reason"]
    rows = []
    s = bare_session(Handing(dict(id="a", action="rule_off", rule="CL01", by="x")), rows)
    s._answer_refusal = lambda answer: (_ for _ in ()).throw(ValueError("refusal broke"))
    s._take_answers(5)
    assert rows[0]["applied"] is False and "refusal broke" in rows[0]["reason"] and s.answers_applied == 0


def test_a_close_answer_is_always_journaled_not_applied_with_the_reason():
    from types import SimpleNamespace
    close = dict(id="a", action="close", trade_id="t", by="x")
    venue = SimpleNamespace(inventory={"t": 1}, active_orders={})
    for plug in (dict(), dict(answer_close=True, execution=SimpleNamespace(venues={"CL01": venue}),
                              engine=SimpleNamespace(last={"settlement": False}, open={}))):
        rows = []
        bare_session(Handing(close), rows, **plug)._take_answers(5)
        assert len(rows) == 1 and rows[0]["applied"] is False
        assert rows[0]["reason"] == "closing by answer is not built; the hold rule in force is the give-back exit"


def test_malformed_key_marks_an_answer_whatever_its_value():
    from paper_platform.answers import validate_decision
    take = dict(action="take", by="x", asked_at_sequence=7)
    for value in (None, "", 0, False, "why"):
        assert validate(dict(take, _malformed=value)) is not None
        assert validate_decision(dict(take, _malformed=value), 7).startswith("malformed: ")
    assert validate(dict(take, _malformed=None)) == "malformed"
    assert validate_decision(take, 7) is None
    assert validate_decision(dict(take, by=""), 7) is not None
    assert validate_decision({k: v for k, v in take.items() if k != "by"}, 7) is not None
    assert "colour" in validate_decision(dict(take, colour="red"), 7)
    assert "colour" in validate(dict(take, colour="red"))


def test_a_malformed_null_line_in_a_trial_file_is_refused_and_never_applied(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text(line(id="a1", not_before_tape_ns=1))
    source = TimedAnswers(path)
    assert [a["id"] for a in source.take(5)] == ["a1"]
    with path.open("a") as f:
        f.write(line(id="a2", _malformed=None))  # appended during the trial, no time
    got = source.take(5)
    assert len(got) == 1 and validate(got[0]) == "malformed"
    path.write_text(line(id="a3", _malformed=None, not_before_tape_ns=1))
    assert TimedAnswers.check(path) == "malformed"
    rows = []
    s = bare_session(Handing(*got), rows)
    s._take_answers(5)
    assert rows[0]["applied"] is False and rows[0]["reason"].startswith("malformed: ") and s.answers_applied == 0


def test_a_deeply_nested_line_does_not_lose_the_good_line_beside_it(tmp_path):
    path = tmp_path / "answers.jsonl"
    deep = "[" * 200000 + "]" * 200000
    path.write_text(line(id="a1") + deep + "\n" + line(id="a2", action="rule_on"))
    source = FileAnswers(path)
    got = source.take()
    assert len(got) == 3 and got[0]["id"] == "a1" and got[2]["id"] == "a2"
    assert "RecursionError" in got[1]["_malformed"] or "not JSON" in got[1]["_malformed"]
    assert validate(got[1]) is not None
    assert source.offset == path.stat().st_size and source.lines == 3 and source.take() == []


def test_an_answer_the_journal_cannot_hold_is_journaled_not_raised():
    rows = []
    s = bare_session(Handing(dict(id="a", action="rule_off", rule="CL01", by="x")), rows)
    real = s.write

    def write(kind, **fields):
        if fields.get("answer") is not None:
            raise RecursionError("too deeply nested for the journal")  # what json.dumps does on a very deep answer
        return real(kind, **fields)

    s.write = write
    s._take_answers(5)
    assert len(rows) == 1 and rows[0]["applied"] is False and rows[0]["answer"] is None
    assert "RecursionError" in rows[0]["reason"]


def gate_session(decider, rows):
    from types import SimpleNamespace
    s = bare_session(None, rows, decider=decider, run="r", liquidation_available=True, folder=__import__("pathlib").Path("/x"),
                     decided=dict(asked=0, take=0, skip=0, none=0), waited_ns=0,
                     answers=None, answer_close=False)
    import hashlib
    s.decider_hash = hashlib.sha256()
    s.execution = None
    return s


def test_the_gate_opens_an_order_only_on_a_valid_take():
    # Session.record on a stub with no tape and no plan: its journal rows are the stub's list, no engine is called.
    sig = dict(action="open", rule="CL01", side="buy", trade_id="t1", received=5)

    class Returns:
        def __init__(self, what):
            self.what = what

        def ask(self, question):
            if isinstance(self.what, Exception):
                raise self.what
            return self.what

    def run(what):
        rows = []
        s = gate_session(Returns(what), rows)
        s.record(dict(sig))
        return rows, s

    take = dict(action="take", by="x", asked_at_sequence=1)  # the signal row is row 0, the question row 1: sequence 0
    rows, s = run(dict(take, asked_at_sequence=0))
    assert [r["kind"] for r in rows] == ["signal", "question", "answer", "paper order"] and rows[2]["action"] == "take"
    rows, s = run(dict(action="skip", by="x", asked_at_sequence=0))
    assert [r["kind"] for r in rows] == ["signal", "question", "answer"] and rows[2]["action"] == "skip"
    for bad in (dict(take, asked_at_sequence=0, _malformed=None), dict(take, asked_at_sequence=0, by=""),
                dict(take, asked_at_sequence=0, colour=1), dict(take, asked_at_sequence=9), RuntimeError("down"),
                "text", None):
        rows, s = run(bad)
        assert [r["kind"] for r in rows] == ["signal", "question", "answer"], bad
        assert rows[2]["action"] == "none" and rows[2]["applied"] is False and "t1" in s.answers_unordered


def test_a_validator_that_raises_leaves_no_order(monkeypatch):
    import paper_platform.session as session_module
    monkeypatch.setattr(session_module, "validate_decision", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    rows = []

    class Take:
        def ask(self, question):
            return dict(action="take", by="x", asked_at_sequence=0)

    s = gate_session(Take(), rows)
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t1", received=5))
    assert [r["kind"] for r in rows] == ["signal", "question", "answer"] and rows[2]["action"] == "none"
    assert "KeyError" in rows[2]["reason"]


def test_a_managers_row_is_skipped_when_its_parent_is_not_ordered():
    rows = []
    s = gate_session(None, rows)
    s.answers_unordered.add("t1")
    for trade_id in ("t1", "t1/giveback"):
        s.record(dict(action="exit", rule="CL01", side="sell", trade_id=trade_id, received=5))
    s.record(dict(action="exit", rule="CL01", side="sell", trade_id="t2/giveback", received=5))
    assert [r["kind"] for r in rows] == ["signal"] * 3 + ["paper order"] and rows[3]["trade_id"] == "t2/giveback"
    rows = []
    s = gate_session(None, rows)  # nothing unordered: as before, and no trade_id needed
    s.record(dict(action="exit", rule="CL01", side="sell", received=5))
    assert [r["kind"] for r in rows] == ["signal", "paper order"]


def test_unreadable_answers_file_is_reported_once_then_nothing(tmp_path):
    source = FileAnswers(tmp_path)  # a directory
    first = source.take()
    assert len(first) == 1 and first[0]["_malformed"].startswith("unreadable: ")
    assert source.take() == []
    assert "unreadable" in TimedAnswers.check(tmp_path)


def test_a_file_line_without_a_time_is_refused_not_applied(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text(line(id="a1", not_before_tape_ns=1))
    source = TimedAnswers(path)
    assert [a["id"] for a in source.take(5)] == ["a1"]
    with path.open("a") as f:
        f.write(line(id="a2"))  # appended during the trial, no time
    got = source.take(5)
    assert len(got) == 1 and got[0]["_malformed"] == "no not_before_tape_ns" and validate(got[0]) is not None


def test_a_file_line_without_a_time_in_check_does_not_raise(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text(json.dumps(dict(action="take", by="x", asked_at_sequence=3)) + "\n")
    assert TimedAnswers.check(path) is not None
    path.write_text(json.dumps(dict(id="a", action="rule_off", rule="CL01", by="x")) + "\n")
    assert "not_before_tape_ns" in TimedAnswers.check(path)


def test_a_used_mailbox_folder_is_found(tmp_path):
    from paper_platform.trial import mailbox_used
    assert mailbox_used(tmp_path / "none") is None and mailbox_used(tmp_path) is None
    (tmp_path / "q-7").mkdir()
    assert mailbox_used(tmp_path) == "q-7"


def test_a_question_with_lines_round_trips_through_the_mailbox(tmp_path):
    from paper_platform.answers import MailboxDecider
    asked = dict(question(7), exchange_ns=3, lines=dict(dipole=dict(value=0.25, width_s=60),
                                                         exhaustion=dict(value=None, width_s=20)))
    answered = dict(action="skip", asked_at_sequence=7, by="agent:1")

    def sleep(_):
        (tmp_path / "q-7" / "answer.json").write_text(json.dumps(answered))

    assert MailboxDecider(tmp_path, 5, 0, sleep=sleep).ask(asked) == answered
    assert json.loads((tmp_path / "q-7" / "question.json").read_text()) == asked


def test_the_gate_puts_the_lines_the_engine_holds_in_the_question_and_its_row():
    from types import SimpleNamespace
    seen = []

    class Records:
        def ask(self, question):
            seen.append(question)
            return dict(action="skip", by="x", asked_at_sequence=question["sequence"])

    rows = []
    s = gate_session(Records(), rows)
    tick = dict(position=11, received=5, exchange=4, dipole=0.5, exhaustion=None)
    s.engine = SimpleNamespace(last=tick, open={}, inputs=SimpleNamespace(
        dipole=SimpleNamespace(width=60 * 10**9), exhaustion=SimpleNamespace(width=20 * 10**9)))
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t1", position=11, received=5, exchange=4))
    lines = dict(dipole=dict(value=0.5, width_s=60), exhaustion=dict(value=None, width_s=20))
    assert seen[0]["lines"] == lines and seen[0]["exchange_ns"] == 4 and rows[1]["lines"] == lines
    assert rows[1]["exchange"] == 4
    # not the signal's own tick: left out
    s.engine.last = dict(tick, position=10)
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t2", position=11, received=5, exchange=4))
    assert "lines" not in seen[1]


# ---- the state the decider is given. Every value below is a stand-in I named: not market data, written nowhere.
class _Stand:
    """Stands in for an object that holds named values."""
    def __init__(self, **values):
        self.__dict__.update(values)


class _Slotted:
    __slots__ = ("alpha", "beta")

    def __init__(self):
        self.alpha, self.beta = 3, "b"


class _Raises:
    @property
    def broken(self):
        raise RuntimeError("accessor")


def _state_session(last, inputs, p=None, feed=None):
    from types import SimpleNamespace
    s = gate_session(None, [])
    s.engine = SimpleNamespace(last=last, p=p, inputs=inputs)
    if feed is not None:
        s.feed = feed
    return s


def test_a_question_with_a_large_state_round_trips_through_the_mailbox(tmp_path):
    from paper_platform.answers import MailboxDecider
    big = dict(inputs=dict(tick={f"n{i}": i for i in range(2000)}), book=dict(bid=[dict(price=i, size=1, orders=1,
               queue=[[i, 1]]) for i in range(3000)], ask=[]), known_at_tape_ns=9, left_out=[])
    asked = dict(question(8), state=big)
    answered = dict(action="skip", asked_at_sequence=8, by="agent:1")

    def sleep(_):
        (tmp_path / "q-8" / "answer.json").write_text(json.dumps(answered))

    assert MailboxDecider(tmp_path, 5, 0, sleep=sleep).ask(asked) == answered
    assert json.loads((tmp_path / "q-8" / "question.json").read_text()) == asked


def test_state_gives_every_attribute_by_its_own_name_and_lists_what_it_leaves_out():
    from collections import deque
    flow = _Stand(width=60, trades=deque([(1, 0, 2, ("sha", 4))]), size=[2, 0], value=0.5)
    inputs = _Stand(dipole=flow, later_added=7, slotted=_Slotted(), blob=b"x", fn=len, bad=float("nan"))
    tick = dict(position=11, received=5, bid=100, ask=101, quote_facts=[dict(fact="x")], leg=(1, "up"))
    s = _state_session(tick, inputs, p=dict(position=9))
    state, why = s._state_at(dict(position=11, received=5))
    assert why is None and state["known_at_tape_ns"] == 5
    got = state["inputs"]
    assert got["strategy_inputs"]["dipole"] == dict(width=60, trades=[[1, 0, 2, ["sha", 4]]], size=[2, 0], value=0.5)
    assert got["strategy_inputs"]["later_added"] == 7 and got["strategy_inputs"]["slotted"] == dict(alpha=3, beta="b")
    assert got["tick"] == dict(tick, leg=[1, "up"]) and got["previous_trade_tick"] == dict(position=9)
    names = {x["name"] for x in state["left_out"]}
    assert {"strategy_inputs.blob", "strategy_inputs.fn", "strategy_inputs.bad", "book"} <= names
    assert all(x["reason"] for x in state["left_out"])
    json.dumps(state, allow_nan=False)


def test_state_is_left_out_when_the_tick_is_not_the_signals_own_and_never_raises():
    inputs = _Stand(a=1)
    tick = dict(position=11, received=5)
    assert _state_session(tick, inputs)._state_at(dict(position=12, received=5))[0] is None
    assert _state_session(None, inputs)._state_at(dict(position=11, received=5))[0] is None
    state, why = _state_session(tick, _Raises())._state_at(dict(position=11, received=5))
    assert why is None and state["inputs"]["strategy_inputs"] == {}  # a property is a class attribute, not walked
    unset = _Slotted.__new__(_Slotted)  # its slots are never set: reading each raises AttributeError
    state, why = _state_session(tick, _Stand(a=1, unset=unset))._state_at(dict(position=11, received=5))
    assert why is None and state["inputs"]["strategy_inputs"]["a"] == 1
    assert {x["name"] for x in state["left_out"]} >= {"strategy_inputs.unset.alpha", "strategy_inputs.unset.beta"}
    inputs = _Stand(a=1)
    state, why = _state_session(dict(position=11), inputs)._state_at(dict(position=11, received=5))
    assert state is None and "KeyError" in why


def test_state_book_reads_both_sides_best_outward_with_queues_from_a_stand_in_book():
    from types import SimpleNamespace
    line = lambda *orders: SimpleNamespace(size=sum(z for _, z in orders),
                                           orders={i: SimpleNamespace(id=i, size=z) for i, z in orders})
    book = SimpleNamespace(sides={
        b"B": SimpleNamespace(prices=[10, 20, 30], lines={10: line((1, 1)), 20: line((2, 2), (3, 5)), 30: line((4, 4))}),
        b"A": SimpleNamespace(prices=[40, 50], lines={40: line((5, 1)), 50: line((6, 2))})})
    tick = dict(position=11, received=5)
    s = _state_session(tick, _Stand(a=1), feed=SimpleNamespace(book=book))
    state, _ = s._state_at(dict(position=11, received=5))
    assert [x["price"] for x in state["book"]["bid"]] == [30, 20, 10]
    assert [x["price"] for x in state["book"]["ask"]] == [40, 50]
    assert state["book"]["bid"][1] == dict(price=20, size=7, orders=2, queue=[[2, 2], [3, 5]])
    assert "book" not in {x["name"] for x in state["left_out"]}
    broken = SimpleNamespace(sides={})
    state, _ = _state_session(tick, _Stand(a=1), feed=SimpleNamespace(book=broken))._state_at(dict(position=11, received=5))
    assert state["book"] is None and any(x["name"] == "book" for x in state["left_out"])


def test_the_gate_puts_state_in_the_question_and_only_its_counts_in_the_row():
    seen, rows = [], []

    class Records:
        def ask(self, question):
            seen.append(question)
            return dict(action="skip", by="x", asked_at_sequence=question["sequence"])

    s = gate_session(Records(), rows)
    from types import SimpleNamespace
    s.engine = SimpleNamespace(last=dict(position=11, received=5, exchange=4), p=None, open={},
                               inputs=_Stand(dipole=_Stand(width=60 * 10**9), exhaustion=_Stand(width=20 * 10**9)))
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t1", position=11, received=5, exchange=4))
    row = rows[1]
    assert "state" in seen[0] and "state" not in row and row["state_sha256"] and row["inputs_named"] >= 1
    assert row["book_levels_bid"] is None and row["book_levels_ask"] is None
    s.engine.last = dict(position=10, received=5)
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t2", position=11, received=5, exchange=4))
    assert "state" not in seen[1]
