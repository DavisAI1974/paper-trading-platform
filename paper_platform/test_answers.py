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
