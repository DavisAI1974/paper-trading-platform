# Paper trading on the tastytrade sandbox: the list of gaps and misses to take care of

Started 2026-11-24 on Greg's word ("Start a list and save as md file in paper trade repo so it can be added to and
is accessible"; "Meaning start list of gaps and misses to take care of"). Every line is something that is missing,
wrong, late, unproven or undecided in the paper runner, its rules or its feed, and is to be taken care of. It is a
list, not a record: the records are in `DavisAI1974/markets-data`, branch `ccr-349e2ffe-07p8od`, folder
`tasks/family-research-20261022/research-union-current/` (named below as DATA).

**How to add to it.** Add a line at the end of the section it belongs to, with the next number, the date and who
(Greg, Claude, Codex). Say what was seen and where the record is. Do not rewrite another line; when a line is
settled, add "DONE" or "DROPPED", the date and one sentence under it. Nothing from the sandbox feed is scored, so no
line here is a result.

## A. The rules, for the rewrite

1. 2026-11-24, Claude. THE FIFTEEN AND THE PRINT WITH NO SIDE. CL01 to CL15 are never shown a print with no side
   (their rule 5). On the 25-minute recording they passed over triggers such a print showed: CL05 70 (opened 42),
   CL10 and CL11 15 each (opened 12), CL06 11 (opened 37), CL15 5 (opened 17); and 55 of their 292 trades closed
   late. Whether they should read the quote such a print carries is open.
   Record: DATA `paper-trading-20261123/step0-20261124/STEP0_TRACE_20261124.md`.
2. 2026-11-24, Claude. A FEW STRATEGIES DO MOST OF THE TRADING. On the 25-minute recording CL14 (60), CL17 (60),
   CL05 (42), CL06 (37) and CL16 (36) made 235 of the 327 trades. They fire on nearly every step of the quote.
3. 2026-11-24, Claude. CL14 now opens on the first sell or buy since the step, which on this feed is very often the
   same print that shows the step (60 opens in 25 minutes; 37 before the fix).
4. 2026-11-24, Greg and Claude. TWO THINGS AT ONE PRINT. Where a rule says one thing must come first and both show
   at one print, the line names both and claims no first (CL06, CL07, CL08). The quote a print carries stood
   before its trade, so an order between a quote step and a trade on one print could be read; it is not read now.
5. 2026-11-24, Claude. THE START (G3). The first 60 and 20 seconds are counted from the first print of any side.
   A recording that starts exactly at the 6 pm open is still treated as cut at the start.
6. 2026-11-24, Claude. A trade still open when a run ends stays open on the ledger (`closed: null`).
7. 2026-11-24, Claude. CL13 reads "one exchange event" as prints of one side sharing one feed time at more than
   one price. Whether those prints are one event the feed does not say. Its text says "with one aggressor side";
   a feed time with both sides at more than one price each has not been seen.
8. 2026-11-24, Claude. CANNOT RUN ON THIS FEED: CL01, CL02, CL03, CL04, CL09, CL12 (they need single orders).
   NEVER OPENED on either recording: CX09, CX10. (CX03 opened once in the 10-minute recording and not in the
   25-minute one; CX01 and CX15 the other way round.)
9. 2026-11-24, builder agent. CODEX'S WORDING NOT CARRIED YET: what "end the observation as interrupted and reset"
   does to a trade that is open; "mark the assessment indeterminate and clear its reference"; the "what would
   contradict it on paper" record after q; CX07's "a later higher bid is a distinct observation to log"; what the
   record of a "release" is (CX13, CX14).
10. 2026-11-24, Claude. CODEX'S EXIT INDICATIONS (CX05, CX06, CX11 to CX14). Built so that an exit indication closes
    the position another of his rules opened (one switch, `tasty/paper/replay.py` `EXIT_INDICATION_CLOSES`). His
    text also says an exit in one strategy's record "never erases another strategy's entry".
11. 2026-11-24, builder agent. Readings made in Codex's rules: a management rule may arm at the same print its
    position was opened; a print at which an entry closes may itself arm the next one; "a different exchange
    event" is a different feed trade time.
12. 2026-11-24, Greg. CL16 and CL17 were added for the print with no side ("The 30 is a jumping off point, not the
    limit"). Whether Codex writes one of his own is open.
13. 2026-11-24, Greg. The rules will be rewritten later, with the agent program highlighting what to look at.

## B. The feed

1. 2026-11-23, Claude. The sandbox stream is exactly 900 seconds behind the market. It carries trade prints (price,
   size, side, time in milliseconds, the bid and offer at the print) and quote messages with no time. It carries
   no single orders, no end-of-event mark, no nanoseconds. Record: DATA `paper-trading-20261123/7e/`.
2. 2026-11-24, Claude. A THIRD OF THE PRINTS HAVE NO SIDE (9 of 107, then 141 of 440). No other field of the
   message tells them apart. Every one trades at its own bid or its own offer. On the 25-minute recording 108 are
   at the offer and 33 at the bid, while only 74 prints are marked buys and 225 sells: if many are unmarked buys,
   the dipole and the exhaustion on this feed lean toward selling more than the market did. Not provable here.
3. 2026-11-24, Claude. The rules read the quote from the prints' own bid and offer only, so a step of the quote
   is seen only when a trade prints.
4. 2026-11-24, Claude. The feed's day volume runs ahead of the prints (21 lots in 10 minutes, 81 in 25): some
   trades are never sent as prints. The prints' sequence number never skips (it steps by 1 or 2), so by Greg's
   definition (a gap is only a real break) there were 0 gaps.

## C. The runner and the platform

1. 2026-11-24, Claude. IT TRADES FASTER THAN THE ORDER PATH CAN FOLLOW. 327 trades in 25 minutes is about 26 orders
   a minute; each order is a fresh sign-in, a dry run and a submit, one at a time.
2. 2026-11-24, Claude. The two lines are worked out again from all the trades at every print, in two copies. Fine
   for a 30-minute run; it will fall behind on a long or busy stretch.
3. 2026-11-24, Claude. NOT YET SEEN ON REAL DATA, so not yet proven: the settlement window (2:28 to 2:30 pm
   Eastern), the 4:44 pm stop, the halt hour, a gap, a dropped stream (the runner ends the run; no reconnect).
4. 2026-11-24, Claude. The hump code in `tasty/paper/lines.py` is the builder's own. The humps traced by hand
   begin and end where ROOT's definition says; no check compares the two over a whole recording.
5. 2026-11-23, Claude. The sandbox says its accounts are "not approved for futures" and fills them anyway; it
   refuses futures orders from 5 to 6 pm Eastern; an order can stay "Live" after its fill is recorded, so fills
   and positions are read, never status alone. tastytrade's page says the simulator fills at the middle of its
   quote: a sandbox fill price is written down as what the sandbox said and is never a result.
6. 2026-11-24, Claude. Each ledger line carries its rule's own "right" or "wrong". They have not been tallied:
   the feed is not scored.

## D. Runs

(Each live run is added here when it has been made: the date, how long, how many orders, where its record is, and
the gaps and misses it showed. Each of those also gets its own line in section A, B or C.)
