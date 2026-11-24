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
        assert rows[0]["reason"] == "closing by answer is not built"


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


def _stand_last_message(position=11, received=5, sequence=5, order_id=99, new=None):
    """A stand-in for the session's last message: only the keys the book guard reads."""
    return dict(position_on_tape=position, raw=dict(ts_recv=received, sequence=sequence, order_id=order_id),
                order_change=dict(new=new), source_sha256="s", record_index=position)


def _state_session(last, inputs, p=None, feed=None, message=None):
    from types import SimpleNamespace
    s = gate_session(None, [])
    s.engine = SimpleNamespace(last=last, p=p, inputs=inputs)
    s.last = _stand_last_message() if message is None else message
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
    book = SimpleNamespace(last_sequence=5, orders={}, sides={
        b"B": SimpleNamespace(prices=[10, 20, 30], lines={10: line((1, 1)), 20: line((2, 2), (3, 5)), 30: line((4, 4))}),
        b"A": SimpleNamespace(prices=[40, 50], lines={40: line((5, 1)), 50: line((6, 2))})})
    tick = dict(position=11, received=5, bid=30, ask=40, reference=("s", 11))
    s = _state_session(tick, _Stand(a=1), feed=SimpleNamespace(book=book))
    state, _ = s._state_at(dict(position=11, received=5))
    assert [x["price"] for x in state["book"]["bid"]] == [30, 20, 10]
    assert [x["price"] for x in state["book"]["ask"]] == [40, 50]
    assert state["book"]["bid"][1] == dict(price=20, size=7, orders=2, queue=[[2, 2], [3, 5]],
                                                   provenance=[[None] * 10, [None] * 10])
    assert state["book"]["totals"]["bid"]["levels"] == 3 and len(state["book"]["provenance_fields"]) == 10
    assert "book" not in {x["name"] for x in state["left_out"]}
    broken = SimpleNamespace(sides={})
    state, _ = _state_session(tick, _Stand(a=1), feed=SimpleNamespace(book=broken))._state_at(dict(position=11, received=5))
    assert state["book"] is None and any(x["name"] == "book" for x in state["left_out"])


def test_state_book_is_left_out_with_both_pairs_when_its_best_levels_are_not_the_ticks_stand_ins():
    from types import SimpleNamespace
    line = lambda: SimpleNamespace(size=1, orders={1: SimpleNamespace(id=1, size=1)})
    book = SimpleNamespace(last_sequence=5, orders={}, sides={b"B": SimpleNamespace(prices=[30], lines={30: line()}),
                                  b"A": SimpleNamespace(prices=[40], lines={40: line()})})
    here = dict(position=11, received=5)
    for bid, ask in ((30, 41), (29, 40), (None, 40), (30, None)):  # one side differs or is unreadable
        s = _state_session(dict(here, bid=bid, ask=ask), _Stand(a=1), feed=SimpleNamespace(book=book))
        state, _ = s._state_at(dict(here))
        left = [x for x in state["left_out"] if x["name"] == "book"]
        assert state["book"] is None and left[0]["reason"] == "the feed's book is not at the signal's tick"
        assert left[0]["feed_book_best"] == dict(bid=30, ask=40) and left[0]["engine_tick_best"] == dict(bid=bid, ask=ask)
    empty = SimpleNamespace(last_sequence=5, orders={}, sides={b"B": SimpleNamespace(prices=[], lines={}), b"A": SimpleNamespace(prices=[], lines={})})
    s = _state_session(dict(here, bid=None, ask=None), _Stand(a=1), feed=SimpleNamespace(book=empty))
    assert s._state_at(dict(here))[0]["book"] is None  # an unreadable side is never a match


def test_state_book_is_left_out_when_the_feed_has_no_handle_stand_in():
    from types import SimpleNamespace
    s = _state_session(dict(position=11, received=5, bid=30, ask=40), _Stand(a=1), feed=SimpleNamespace())
    state, _ = s._state_at(dict(position=11, received=5))
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


# ---- the rules' own state and the paper state. Every object below is a stand-in I named; none is market data.
def test_state_has_the_rules_working_state_and_the_paper_state_by_their_own_names():
    from types import SimpleNamespace
    s = _state_session(dict(position=11, received=5), _Stand(a=1))
    s.engine = SimpleNamespace(last=s.engine.last, p=None, inputs=_Stand(a=1), cl=_Stand(armed=[1, 2]),
                               unsided_print_rules=_Stand(count=3), open={"t1": dict(rule="CL01")}, unmonitored={},
                               managers={}, responses=_Stand(pending={}), ended=False, run_id="r")
    s.rules = {"CL02": {}, "CL01": {}}
    s.answers_off, s.answers_unordered = {"CL01"}, {"t9"}
    state, why = s._state_at(dict(position=11, received=5))
    assert why is None
    r = state["rules"]
    assert r["cl"] == dict(armed=[1, 2]) and r["unsided_print_rules"] == dict(count=3)
    assert r["open"] == {"t1": dict(rule="CL01")} and r["responses"] == dict(pending={}) and r["ended"] is False
    assert r["run_id"] == "r" and r["in_force"] == ["CL01", "CL02"]
    assert state["paper"]["answers_off"] == ["CL01"] and state["paper"]["answers_unordered"] == ["t9"]
    assert state["paper"]["execution"] is None
    assert not any("engine.cl" in x["name"] for x in state["left_out"])
    # a venue stand-in: its fields by their own names, a working order read attribute by attribute
    order = _Stand(side=None, quantity=2, leaves_qty=1, price=None, has_price=False, status=None)
    venue = _Stand(inventory={"t1": 2}, cash={"t1": -50}, peak={"t1": 7}, exit_attempts={}, holding={"t1"},
                   active_orders={"t1": {"o1"}}, orders={"o1": (order, "t1", "open", None)})
    s.execution = _Stand(give_back=None, also_before_profit=False, venues={"CL01": venue})
    state, _ = s._state_at(dict(position=11, received=5))
    v = state["paper"]["venues"]["CL01"]
    assert v["inventory"] == {"t1": 2} and v["cash"] == {"t1": -50} and v["holding"] == ["t1"] and v["peak"] == {"t1": 7}
    assert state["paper"]["give_back_fraction"] is None and state["paper"]["also_before_profit"] is False
    assert "active_orders" in v and json.dumps(state, allow_nan=False)


# ---- the MBO window. Every message below is a stand-in I named (just the keys the session reads): not market data.
def _stand_message(position, received, action="T", in_session=True, period="trade day"):
    return dict(cursor=position, position_on_tape=position, period=period, in_session=in_session,
                source_sha256="stand-in", record_index=position, raw=dict(ts_recv=received, action=action))


def _stepping_session(decider, messages, window_s=None, paced_from_ns=None):
    """A Session by object.__new__ whose stream is the stand-in messages and whose engine does nothing."""
    import io
    import threading
    from types import SimpleNamespace
    from paper_platform.session import Session
    s = object.__new__(Session)
    s.lock, s.state, s.cursor, s.last, s.events = threading.RLock(), "active", -1, None, 0
    s.decider, s.answers, s.execution, s.captured, s.paced_from_ns = decider, None, None, False, paced_from_ns
    s.waited_ns, s.deadline, s.message_budget, s.output_budget = 0, 10**18, 10**9, 10**9
    s.timers_after_input, s.weekly_book_boundaries, s.first_live_decision_cursor = [], [], None
    s.stream, s.receives = iter(messages), io.BytesIO()
    for m in messages:
        m["received_by_replay_ns"] = 1
    s.engine = SimpleNamespace(next_expiry_ns=None, tick=lambda m: [], begin_decision_batch=lambda: None,
                               inputs=SimpleNamespace(dipole=SimpleNamespace(width=60 * 10**9)))
    if decider is not None:
        s._decider_window_init(window_s)
    return s


def test_the_window_keeps_only_what_is_inside_it_and_ends_with_the_signals_own_message():
    n = 10**9
    messages = [_stand_message(i, t * n, action=a) for i, (t, a) in enumerate(
        [(1, "A"), (30, "T"), (41, "T"), (70, "T"), (100, "A")])]
    s = _stepping_session(object(), messages, window_s=60)
    assert s.decider_mbo_window_s == 60  # the default would also be 60: the dipole width
    for _ in messages:
        assert s.step(s.cursor) is not None
    assert [m["position_on_tape"] for m in s._mbo] == [2, 3, 4]  # 41 s, 70 s, 100 s: 100 - 60 = 40 is outside
    # asked at position 3 (receive 70 s): not the message after it, and 10 s is the edge
    tick = dict(position=3, received=70 * n)
    left = []
    got = s._mbo_window_state(tick, left)
    assert [m["position_on_tape"] for m in got["messages"]] == [2, 3] and got["count"] == 2 and got["window_s"] == 60
    assert got["messages"][-1]["raw"]["ts_recv"] == 70 * n
    assert s._mbo_window_state(dict(position=0, received=n), left) is None  # not in the window any more
    assert left[-1]["name"] == "mbo_window"
    c = s._mbo_counts
    assert (c["stepped"], c["in_trade_day"], c["prints_in_trade_day"]) == (5, 5, 3)
    assert json.dumps(got, allow_nan=False)
    assert _stepping_session(object(), [], window_s=None).decider_mbo_window_ns == 60 * n
    import pytest
    with pytest.raises(ValueError):
        _stepping_session(object(), [], window_s=0)


def test_with_no_decider_the_window_and_counters_are_never_created_or_touched():
    from paper_platform.session import Session
    messages = [_stand_message(i, i * 10**9) for i in range(3)]
    s = _stepping_session(None, messages)
    for _ in messages:
        s.step(s.cursor)
    assert s._mbo is None and s._mbo_counts is None and s.decider_mbo_window_s is None
    assert "_mbo" not in vars(s) and "_mbo_counts" not in vars(s) and "decider_mbo_window_s" not in vars(s)
    assert Session._mbo is None and Session._mbo_counts is None
    assert s.cursor == 2 and s.answers_receipt()["on"] is False if hasattr(s, "answers_off") else s.cursor == 2


def test_the_book_must_be_the_session_readers_book_at_the_signals_own_message():
    from types import SimpleNamespace
    line = lambda: SimpleNamespace(size=1, orders={1: SimpleNamespace(id=1, size=1)})
    held = SimpleNamespace(price=30, size=4, side=b"B", entered_pos=8)  # a stand-in order the stand-in book holds
    sides = {b"B": SimpleNamespace(prices=[30], lines={30: line()}), b"A": SimpleNamespace(prices=[40], lines={40: line()})}
    tick = dict(position=11, received=5, bid=30, ask=40, reference=("s", 11))

    def ask(book, message):
        s = _state_session(tick, _Stand(a=1), feed=SimpleNamespace(book=book), message=message)
        state, _ = s._state_at(dict(position=11, received=5))
        left = [x for x in state["left_out"] if x["name"] == "book"]
        return state["book"], left

    mine = _stand_last_message(order_id=7, new=(30, 4, "B", 8))
    book, left = ask(SimpleNamespace(last_sequence=5, orders={7: held}, sides=sides), mine)
    assert book is not None and not left
    # another reader's book, or one ahead by a message: the sequence differs
    book, left = ask(SimpleNamespace(last_sequence=6, orders={7: held}, sides=sides), mine)
    assert book is None and "last exchange sequence" in left[0]["detail"]
    # the order the message changed is not what the book holds
    book, left = ask(SimpleNamespace(last_sequence=5, orders={7: SimpleNamespace(price=30, size=3, side=b"B", entered_pos=8)},
                                     sides=sides), mine)
    assert book is None and "order differs" in left[0]["detail"]
    # a message that removed its order: the book must hold none
    gone = _stand_last_message(order_id=7, new=None)
    assert ask(SimpleNamespace(last_sequence=5, orders={}, sides=sides), gone)[0] is not None
    assert ask(SimpleNamespace(last_sequence=5, orders={7: held}, sides=sides), gone)[0] is None
    # the session's last message is not the signal's own
    assert ask(SimpleNamespace(last_sequence=5, orders={}, sides=sides), _stand_last_message(position=10))[0] is None
    # same position, receive time, sequence and order change, but another tape reference (a later message of the same
    # exchange event): refused
    later = dict(mine, record_index=12)
    book, left = ask(SimpleNamespace(last_sequence=5, orders={7: held}, sides=sides), later)
    assert book is None and "tape reference" in left[0]["detail"]
    assert dict(mine, source_sha256="other") and ask(SimpleNamespace(last_sequence=5, orders={7: held}, sides=sides),
                                                     dict(mine, source_sha256="other"))[0] is None


def test_conditions_hold_each_named_item_and_no_label():
    from types import SimpleNamespace
    from root.calendar import trade_day_bounds
    from datetime import date
    opens, halts = trade_day_bounds(date(2023, 10, 2))  # a calendar fact, not market data
    start, end = opens + 20 * 3600 * 10**9, opens + 20 * 3600 * 10**9 + 120 * 10**9
    tick = dict(position=11, received=opens - 5, exchange=opens - 7, settlement=False)  # before the open: negative
    inputs = _Stand(a=1, start=start, end=end, end_of_day=halts)
    s = _state_session(tick, inputs)
    s.trade_day, s.paced_from_ns = "2023-10-02", opens - 100
    s.market = dict(price_scale=10**9, price_unit="USD/MMBtu", contract_size=10000, contract_unit="MMBtu")
    plan = dict(databento_instrument_id=1, instrument=dict(raw_symbol="STAND", price_increment="0.001", multiplier="10000",
                maker_fee="0", taker_fee="0", margin_init="0", margin_maint="0", expiration_ns=halts + 10))
    s.initial = dict(execution_plan=plan)
    s._mbo_counts = dict(stepped=4, in_trade_day=0)
    state, why = s._state_at(dict(position=11, received=opens - 5))
    c = state["conditions"]
    cal = c["calendar"]
    assert why is None and cal["weekday"] == "Monday" and cal["trade_day"] == "2023-10-02"
    assert cal["since_open_ns"] == dict(exchange=-7, received=-5)  # negative stays negative
    assert cal["until_settle_start_ns"]["received"] == start - (opens - 5) and cal["until_halt_ns"]["exchange"] == halts - opens + 7
    assert cal["in_settlement_window"] is False and cal["since_paced_start_ns"] == dict(received=95)
    assert cal["open_ns"] == opens and cal["halt_ns"] == halts and cal["settle_end_ns"] == end
    assert c["counts"] == dict(stepped=4, in_trade_day=0)
    ins = c["instrument"]
    assert ins["raw_symbol"] == "STAND" and ins["price_increment_in_tape_units"] == 1_000_000
    assert ins["until_expiration_ns"] == dict(exchange=halts + 10 - (opens - 7), received=halts + 10 - (opens - 5))
    assert not {"maker_fee", "taker_fee", "margin_init", "margin_maint"} & set(ins)
    assert c["units"]["price_scale"] == 10**9 and c["market"]["price_unit"] == "USD/MMBtu"
    assert not any("mid" in k or "regime" in k or "label" in k for k in c)
    # a run with no plan and no counters: nulls, named, no raise
    s.initial = dict(execution_plan=None)
    s._mbo_counts = None
    state, why = s._state_at(dict(position=11, received=opens - 5))
    assert why is None and state["conditions"]["instrument"] is None and state["conditions"]["counts"] is None
    assert any(x["name"] == "conditions.instrument" for x in state["left_out"])


def test_the_row_counts_the_layers_and_the_whole_question_round_trips_through_the_mailbox(tmp_path):
    from types import SimpleNamespace
    from paper_platform.answers import MailboxDecider
    seen, rows = [], []

    class Mail:
        def ask(self, question):
            seen.append(question)
            return MailboxDecider(tmp_path, 5, 0, sleep=lambda _: (tmp_path / f"q-{question['sequence']}" / "answer.json")
                                  .write_text(json.dumps(dict(action="skip", by="x", asked_at_sequence=question["sequence"])))).ask(question)

    s = gate_session(Mail(), rows)
    s.last = _stand_last_message()
    s.engine = SimpleNamespace(last=dict(position=11, received=5, exchange=4, settlement=False), p=None, open={},
                               inputs=_Stand(dipole=_Stand(width=60 * 10**9), exhaustion=_Stand(width=20 * 10**9)))
    s._mbo = [dict(position_on_tape=11, raw=dict(ts_recv=5, action="T"), period="stand-in", in_session=True)]
    s.decider_mbo_window_s, s.decider_mbo_window_ns = 60, 60 * 10**9
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t1", position=11, received=5, exchange=4))
    row = rows[1]
    assert row["mbo_window_messages"] == 1 and row["rules_named"] >= 1 and row["conditions_named"] >= 1
    assert "state" not in row and "mbo_window" not in row
    written = json.loads((tmp_path / "q-0" / "question.json").read_text())
    assert written == json.loads(json.dumps(seen[0])) and len(written["state"]["mbo_window"]["messages"]) == 1
    assert not list(tmp_path.glob("q-*/*.part"))
    # a state that cannot be written as plain JSON is dropped and named; the run goes on
    s._state_at = lambda e: (dict(book=None, inputs=dict(strategy_inputs={}, tick={}), bad=float("nan")), None)
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t2", position=11, received=5, exchange=4))
    assert "state" not in seen[1] and "JSON" in rows[-2]["state_left_out"]


def test_the_market_series_writers_with_stand_in_messages(tmp_path):
    from paper_platform.trial import MarketSeries, print_line, quote_line

    def stand(received, bid, ask, action="A", side="N", price=0, size=0, period="trade day"):  # a stand-in message
        return dict(source_sha256="stand-in-sha", record_index=received, period=period, best_bid=bid, best_ask=ask,
                    raw=dict(ts_recv=received, ts_event=received - 1, action=action, side=side, price=price, size=size))

    assert quote_line(stand(5, 30, 40)) == dict(tape_received_ns=5, exchange_ns=4, reference=["stand-in-sha", 5],
                                                bid=30, ask=40)
    assert set(print_line(stand(5, 30, 40, "T", "B", 31, 2))) == {"tape_received_ns", "exchange_ns", "reference", "price",
                                                                    "size", "aggressor"}
    carrying = dict(stand(5, 30, 40), best_bid_size=3, best_ask_size=4)
    assert quote_line(carrying)["bid_size"] == 3 and quote_line(carrying)["ask_size"] == 4
    series = MarketSeries(tmp_path, begin_ns=10)
    for m in (stand(1, 30, 40), stand(2, 30, 41),  # before the stretch: read, not written
              stand(10, 30, 41), stand(11, 30, 41, "T", "A", 41, 1), stand(12, 31, 41), stand(13, None, 41),
              stand(14, 31, 41, period="vendor snapshot"), stand(15, 31, 41, "T", "N", 31, 5)):
        series.add(m, m["raw"]["ts_recv"] >= 10)
    series.close()
    quotes = [json.loads(x) for x in (tmp_path / "quotes.jsonl").read_text().splitlines()]
    prints = [json.loads(x) for x in (tmp_path / "prints.jsonl").read_text().splitlines()]
    assert [q["tape_received_ns"] for q in quotes] == [10, 12, 13, 15] and quotes[2]["bid"] is None  # 15: the bid is back after 13 had none; the snapshot at 14 is not written or read
    assert [(p["tape_received_ns"], p["price"], p["size"], p["aggressor"]) for p in prints] == [(11, 41, 1, "A"), (15, 31, 5, "N")]
    assert series.count == dict(quotes=4, prints=2)
    done = series.finish(10, 20)
    from paper_platform.session import digest
    assert done["sha256"]["quotes.jsonl"] == digest(tmp_path / "quotes.jsonl") and done["failure"] is None
    meta = json.loads((tmp_path / "market-series.json").read_text())
    assert meta["counts"] == dict(quotes=4, prints=2) and meta["stretch_tape_ns"] == [10, 20]
    assert (meta["first_tape_received_ns"], meta["last_tape_received_ns"]) == (10, 15)
    assert meta["sha256"] == {k: v for k, v in done["sha256"].items() if k != "market-series.json"}
    assert not any("mid" in k for q in quotes for k in q)


def test_a_failing_series_write_stops_the_series_not_the_trial(tmp_path):
    from paper_platform.trial import MarketSeries, decided
    m = dict(source_sha256="s", record_index=1, period="trade day", best_bid=1, best_ask=2, in_session=True,
             raw=dict(ts_recv=5, ts_event=4, action="T", side="B", price=1, size=1))
    series = MarketSeries(tmp_path, 0)
    series.add(m, True)
    series.quotes.close()  # the next write raises ValueError
    series.add(dict(m, best_bid=3), True)
    series.add(dict(m, best_bid=4), True)  # nothing more is tried; nothing raises
    series.close()
    done = series.finish(0, 10)
    assert "ValueError" in done["failure"] and done["quotes"] == 1
    assert json.loads((tmp_path / "market-series.json").read_text())["failure"] == done["failure"]
    # the boundary is the session's own: receive clock after a paced start, exchange clock (in_session) always
    assert decided(m, 10, 0) and not decided(m, 10, 1) and not decided(dict(m, in_session=False), 0, 0)


# ---- fixes of the third review: nothing the run holds is left unnamed; the state's hash; counts; building time.
def test_an_attribute_the_code_has_never_heard_of_is_in_state_or_in_left_out():
    from types import SimpleNamespace
    s = _state_session(dict(position=11, received=5), _Stand(a=1))
    s.engine = SimpleNamespace(last=s.engine.last, p=None, inputs=_Stand(a=1), cl=_Stand(), rules={"CL01": {"id": 1}},
                               cx=[{"id": "CX1"}], brand_new=dict(x=1), brand_new_blob=b"x", brand_new_obj=len)
    venue = _Stand(inventory={}, cash={}, peak={}, exit_attempts={}, holding=set(), active_orders={"t1": {"o1"}},
                   orders={"o1": (_Stand(), "t1", "open", None), "o0": (_Stand(), "t0", "close", "reason")},
                   book_quote=dict(bid=1), reference=["sha", 3], received=5, known_received_ns=5, venue_new=[1, 2],
                   venue_new_blob=b"y", clock=_Stand(), instrument=_Stand())
    s.execution = _Stand(give_back=None, also_before_profit=False, venues={"CL01": venue}, policies={"CL01": {"legs": 1}},
                         followers={"CL01": []}, exec_new=7, exec_new_blob=b"z", instrument=_Stand())
    s.rules = {"CL01": {}}
    state, why = s._state_at(dict(position=11, received=5))
    assert why is None
    r = state["rules"]
    assert r["rules"] == {"CL01": {"id": 1}} and r["cx"] == [{"id": "CX1"}] and r["brand_new"] == dict(x=1)
    names = {x["name"] for x in state["left_out"]}
    assert {"rules.brand_new_blob", "rules.brand_new_obj", "paper.exec_new_blob", "paper.instrument",
            "paper.venues.CL01.venue_new_blob", "paper.venues.CL01.clock", "paper.venues.CL01.instrument",
            "paper.venues.*.engine.fill_model/fee_model"} <= names
    p = state["paper"]
    assert p["exec_new"] == 7 and p["policies"] == {"CL01": {"legs": 1}} and p["followers"] == {"CL01": []}
    v = p["venues"]["CL01"]
    assert v["venue_new"] == [1, 2] and v["book_quote"] == dict(bid=1) and v["reference"] == ["sha", 3]
    assert v["received"] == 5 and v["known_received_ns"] == 5
    assert set(v["orders"]) == {"o0", "o1"} and v["orders"]["o0"]["active"] is False and v["orders"]["o0"]["action"] == "close"
    assert v["orders"]["o0"]["administrative_reason"] == "reason" and v["orders"]["o1"]["active"] is True
    # every attribute the stand-ins hold is written or named
    for attr in vars(s.engine):
        assert attr in ("inputs", "last", "p") or attr in r or any(n == f"rules.{attr}" for n in names), attr
    for attr in vars(s.execution):
        assert attr in ("give_back",) or attr in p or any(n == f"paper.{attr}" for n in names), attr
    for attr in vars(venue):
        assert attr in v or any(n == f"paper.venues.CL01.{attr}" for n in names), attr
    json.dumps(state, allow_nan=False)


def test_the_instrument_definition_dated_after_the_tick_is_left_out_and_named():
    from root.calendar import trade_day_bounds
    from datetime import date
    opens, halts = trade_day_bounds(date(2023, 10, 2))
    tick = dict(position=11, received=opens + 5, exchange=opens + 4, settlement=False)
    s = _state_session(tick, _Stand(a=1, start=1, end=2, end_of_day=3))
    s.trade_day, s.paced_from_ns = "2023-10-02", None
    s.market = dict(price_scale=10**9, price_unit="u", contract_size=1, contract_unit="c")
    base = dict(raw_symbol="STAND", price_increment="0.001", maker_fee="0", taker_fee="0", margin_init="0", margin_maint="0")
    for stamped, expect_in in ((dict(ts_event=opens + 4, ts_init=opens), True), (dict(ts_event=opens + 5), False),
                               (dict(ts_event=opens, ts_init=opens + 9), False)):
        s.initial = dict(execution_plan=dict(databento_instrument_id=1, instrument=dict(base, **stamped)))
        state, _ = s._state_at(dict(position=11, received=opens + 5))
        got = state["conditions"].get("instrument")
        assert (got is not None) == expect_in
        if not expect_in:
            assert any(x["name"] == "conditions.instrument" and "dated after" in x["reason"] for x in state["left_out"])


def test_the_state_hash_ignores_the_wall_clock_fields_and_names_them():
    from paper_platform.session import WALL_CLOCK_FIELDS, _without_wall_clock
    state = dict(hash_excludes=list(WALL_CLOCK_FIELDS),
                 mbo_window=dict(messages=[dict(position_on_tape=1, received_by_replay_ns=111)]), known_at_tape_ns=5)
    other = json.loads(json.dumps(state))
    other["mbo_window"]["messages"][0]["received_by_replay_ns"] = 222
    assert state != other and _without_wall_clock(state) == _without_wall_clock(other)
    assert state["mbo_window"]["messages"][0]["received_by_replay_ns"] == 111  # the state itself keeps the field
    # through the gate: two questions whose states differ only in that field carry the same state_sha256
    from types import SimpleNamespace
    shas = []
    for wall in (111, 222):
        rows = []
        s = gate_session(SimpleNamespace(ask=lambda q: dict(action="skip", by="x", asked_at_sequence=q["sequence"])), rows)
        s._state_at = lambda e, wall=wall: (dict(book=None, inputs=dict(strategy_inputs={}, tick={}), hash_excludes=list(
            WALL_CLOCK_FIELDS), mbo_window=dict(count=1, messages=[dict(received_by_replay_ns=wall)])), None)
        s.record(dict(action="open", rule="CL01", side="buy", trade_id="t", position=11, received=5, exchange=4))
        shas.append(rows[1]["state_sha256"])
    assert shas[0] == shas[1]
    assert "hash_excludes" in _state_session(dict(position=11, received=5), _Stand(a=1))._state_at(
        dict(position=11, received=5))[0]


def test_the_time_spent_building_the_question_is_taken_out_of_the_runs_clock_too():
    import time as _time
    from types import SimpleNamespace
    rows = []
    s = gate_session(SimpleNamespace(ask=lambda q: dict(action="skip", by="x", asked_at_sequence=q["sequence"])), rows)

    def slow(e):
        _time.sleep(0.05)
        return None, "stand-in"

    s._state_at = slow
    s.record(dict(action="open", rule="CL01", side="buy", trade_id="t", position=11, received=5, exchange=4))
    assert s.waited_ns >= 50_000_000 and rows[2]["latency_ns"] < s.waited_ns


def test_the_receipts_answer_rows_add_up_with_failure_rows_counted_apart():
    import hashlib
    rows = []
    s = bare_session(None, rows, decider=None, run="r", decided=dict(asked=0, take=0, skip=0, none=0), waited_ns=0,
                     decider_hash=hashlib.sha256(), decider_mbo_window_s=None)
    s.answers = Handing(dict(id="a1", action="rule_off", rule="CL01", by="x", packet=None, asked_at_sequence=None),
                        dict(id="a2", action="rule_off", rule="CL02", by="x", packet=None, asked_at_sequence=None))
    s.answers.waiting = lambda: 0
    s.rules = {"CL01": {}, "CL02": {}}
    s._orders_for = lambda rule: True
    original = s._apply_answer

    def break_second(answer, row):
        if answer["id"] == "a2":
            raise RuntimeError("boom")
        original(answer, row)

    s._apply_answer = break_second
    s._take_answers(5)
    receipt = s.answers_receipt()
    answer_rows = [r for r in rows if r["kind"] == "answer"]
    assert receipt["taken"] == 2 and receipt["applied"] == 1 and receipt["failure_rows"] == 1
    assert receipt["rows_for_taken"] == 2 and len(answer_rows) == receipt["rows_for_taken"] + receipt["failure_rows"] == 3
    assert len(answer_rows) > receipt["taken"] + receipt["decider"]["asked"]  # the numbers differ, and now say why
