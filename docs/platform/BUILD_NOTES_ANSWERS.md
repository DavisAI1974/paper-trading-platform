2026-11-24 piece 1 done: paper_platform/answers.py and test_answers.py (7 checks). Next: piece 2, the session takes and applies answers (session.py, one line in paper_execution.py).
2026-11-24 piece 2 done: session takes and applies answers (session.py; one clause in paper_execution.py instructions). Piece 3 done: --answers in trial.py and run.py. Next: piece 4, the words in paper_platform/README.md.
2026-11-24 piece 4 done: README section 'Answers taken during a run'. All four pieces committed locally; nothing run on tape.
2026-11-24 piece 2b done: take/skip, the gate in Session.record, MailboxDecider, --decider-mailbox in trial.py, README. Not run on tape.
2026-11-24 review fixes 1, 2, 5 (86ecd21): a bad answer never stops a run; a close by answer closes the paper trade only (no engine change, no signal row). Not run on tape.
2026-11-24 review fixes 3, 4, 6, 7, 8: decider wait taken out of the run's clock (waited_ns), event precheck counts question and answer rows, used mailbox folder refused, README words, 16 checks in test_answers.py. Not run on tape.
2026-11-24 second review fixes 1 to 8: _malformed key always malformed, by required, gate check inside the try, per-line parse catch, manager rows follow their parent, closing by answer not built (no --answer-close), budget words, checks. Not run on tape.
2026-11-24 piece 9: the decider's question and its journal row carry exchange_ns and lines (dipole and exhaustion value and width, read from engine.last). Not run on tape.
2026-11-24 piece 10: the decider's question carries state (every inputs attribute, engine.last, engine.p, book depth when feed.book exists; the book is not yet reachable, named in left_out). Not run on tape.
