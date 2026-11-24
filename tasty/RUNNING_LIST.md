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

7. 2026-11-24, Greg. RICHER DATA IS COMING: "we will have richer data to run from in a little while so we don't need
   to make system around thin data". So nothing more is built to fit this feed's thinness. What exists only
   because the feed is thin, and is to be dropped or redone when the richer feed arrives: the stand-ins S1 to S7
   (the feed's millisecond clock, no event mark, the quote read from the prints only, the late closes), the gap
   read from the sequence number, the handling of prints with no side in the fifteen, and the six rules that
   cannot run. The rules themselves take plain events and know nothing of tastytrade, so they carry over.

8. 2026-11-24, Claude, from the first run with orders. THE SANDBOX REFUSES EVERY BUY WHILE THE ACCOUNT IS SHORT. Its
   dry run answers HTTP 422 `illegal_buy_and_sell_on_same_symbol` ("Cannot buy and sell against the same symbol").
   After the first sell filled, no buy got through: 26 refused in the first four minutes. So the one account can
   only go one way, every strategy's buy (an open, or the close of a sell) is lost, and the flatten is at risk too.
   Not yet known: whether it is the short position itself, a sell order whose status stays "Live" after its fill,
   or the action word ("Buy" where the sandbox wants a closing action). One account netting all the strategies
   does not work on this sandbox as built.
9. 2026-11-24, Claude, same run. THE SANDBOX DOES NOT FILL FROM THE FEED WE TRADE FROM. Six sells filled at 3.547 and
   3.548 while the print that triggered each showed a bid of 3.536 to 3.542. The stream is 900 seconds behind; the
   fills look like the market as it is now. A rule decides on a 15-minute-old quote and is filled on today's.
   The comparison against the feed 15 minutes later is to be made from the run's own feed record.
10. 2026-11-24, Claude, same run. THE SANDBOX THROWS SERVER ERRORS OFTEN (HTTP 500): in the first four minutes 14 on
    dry runs, 6 on the live-orders read, 3 on order reads and 5 on SUBMITS. A submit answered with a 500 is not
    re-sent (as built) and the look-up did not find it, so that trade never opened. The retries on reads and dry
    runs each delay the orders queued behind them.
11. 2026-11-24, Claude, same run. An order's status still reads "Live" after its fill is recorded (seen again on
    2208125 and 2208127). The runner reads fills, so it was not misled.
12. 2026-11-24, reviewer agent. NOT FIXED YET in the order path: a second Ctrl-C during the flatten can lose the
    flatten's record and the receipt; nothing stamps how late an order goes out after its signal; the lock made
    for the counts is never used (each count is written by one thread only).
13. 2026-11-24, Claude. FOUND ON THE REAL RECORDS: the sandbox's "live orders" list also holds the day's filled and
    cancelled orders. The start check and the flatten now count only an order that is not terminal and not fully
    filled. Fixed before the first run (`ef58c85`).

14. 2026-11-24, Claude, after the first run with orders. THE ACCOUNT IS STUCK SHORT 13 LOTS OF /NGF7. The run's own
    flatten (buy 13) was refused at the dry run with the same 422. A close-out by hand (`tasty/paper/close_out.py`)
    then asked the sandbox to cancel its six unfinished sell orders: five of the run's sells whose status stayed
    "Live" after they filled, and order 2207911 of 2026-11-23, which has read "Cancel Requested" since then. The
    five cancels were accepted and those orders now read "Cancel Requested" too; the buy was refused again, and so
    was a dry run of "Buy to Close". Most likely cause: the sandbox refuses a buy while any sell order on the symbol
    is not finished, and 2207911 was already standing before the run began, so every buy of the run was refused
    from the first. They are Day orders. Ways out: try the buy again after the trade day ends (5 pm Eastern
    Tuesday), or reset the sandbox account on tastytrade's tool page (Greg).
    Record: DATA `paper-trading-20261123/live-runs/closeout-20261124T011335Z/`.
15. 2026-11-24, Claude, same run. NO TRADE WAS EVER CLOSED BY AN ORDER. Every close was either a buy (refused) or the
    sell that closes a buy which had never opened. 28 closes were held back for that second reason.
16. 2026-11-24, Claude, same run. ORDERS GO OUT LATE. From the signal to the order going out: 1 to 61 seconds over
    the 19 submits, growing as the queue filled (the last two waited 59 and 61 seconds). One at a time, each with a
    fresh sign-in, a dry run and reads that the sandbox often answers with a server error.
17. 2026-11-24, Claude, same run. THE ORDER BUDGET ENDED THE RUN AFTER SEVEN MINUTES, so the feed was not recorded
    past the fills and the fill prices could not be set beside the market from the run's own record. A feed-only
    recording was started nine minutes later to catch it. The budget stop should stop the ORDERS and let the feed
    and the ledger run on to the end of the time asked for.
18. 2026-11-24, Claude, same run. 6 of the 19 submits were answered with a server error (HTTP 500). None was re-sent
    and none was found afterwards, so those six trades never opened, and each still used one of the 20 orders.

19. 2026-11-24, Claude, from the feed-only recording after the run. C9 MEASURED: THE SANDBOX FILLS AT THE MARKET AS
    IT IS NOW, NOT AT THE FEED IT GIVES US. Each of the 13 fills, set beside the feed's own prints at the market
    time it filled (the recording was exactly 900.1 seconds behind), is at the bid or the offer of the print just
    before or just after it: for example CL13-0001 sold at 3.547 with the market at 3.547 bid, 3.548 offered,
    while the print it acted on showed 3.538 and 3.539. So on this sandbox a rule decides on a quote that is 15
    minutes old and is filled on a different one. Entries and exits cannot be tested this way; a fill here says
    nothing about the quote the rule saw. Each fill is on its own line in DATA
    `paper-trading-20261123/live-runs/feed-only-20261124T011440Z/fills-beside-the-market.json`.

20. 2026-11-24, Claude. THE START CHECK LET THE RUN BEGIN WITH ORDER 2207911 STANDING. Handoff V had it down as
    unverified ("Cancel Requested", its final status never read). Before the run Claude changed the start check to
    count only an order that is not terminal AND not fully filled, because the sandbox's live list also holds
    filled orders; 2207911 had its fill, so it passed. If C14's cause is right, that order is what made the
    sandbox refuse every buy. The check should refuse on ANY unfinished order on the symbol, filled or not.
21. 2026-11-24, Claude. THE RUN'S RECEIPT DOES NOT SHOW WHAT WAS LEFT IN THE ACCOUNT. `receipt.json` of the first run
    has no final positions or live orders in it, and its `account_positions_left_not_flattened` reads empty (that
    field lists other symbols only) while the account was short 13. The final pull is in `orders.jsonl` only.
    Someone reading the receipt alone would think the run ended flat.

22. 2026-11-24, Greg. SANDBOX-ONLY ISSUES ARE PARKED: "The sandbox specific issues won't be a problem soon so don't
    spend time on them". No time is spent on these lines; they stay written down and are dropped when the sandbox
    is left behind: C5 (the sandbox's quirks), C8 (it refuses buys while short), C9 and C19 (it fills at the live
    market, not at its own feed), C10 and C18 (its server errors), C11 (a filled order still reads "Live"), C13
    (its live list holds finished orders), C14 (the account left short 13 lots: left as it is), C15 (no trade
    closed by an order) and C20 (the start check and order 2207911).
    STILL OURS, whatever the feed or the broker: C2 (the lines worked out again at every print), C3 (the stops
    and windows not yet seen on real data), C4 (the hump check), C12 (the reviewer's findings not fixed), C16
    (orders one at a time behind a queue), C17 (the order budget ends the whole run), C21 (the receipt does not
    show what was left in the account), and everything in section A.

23. 2026-11-24, Greg. FROM NOW, RUNS ARE LEDGER-ONLY: "We can just observe on ledger". The runner is started
    without `--orders`: it reads the live feed, runs all 32 strategies and writes each paper trade with both quotes
    at each end; nothing is sent to the sandbox. Each run still needs his go.

24. 2026-11-24, Greg asked: "Is it creating the graphs for the ledger?" NO. The runner writes files of lines only
    (`ledger.jsonl`, `events.jsonl`, `feed.jsonl`, `orders.jsonl`, `receipt.json`). It draws no picture of a trade
    or of a run, and it writes nothing into the knowledge graph (the plan of 2026-11-23 left "the graph links" out
    of the build). Nothing in the runner draws or links anything yet. To be built, on his word for which he means:
    a picture for each paper trade (the prints, both quotes and the two lines from its open to its close), and/or
    the links from each ledger line to its rule version and the finding the rule rests on.

25. 2026-11-24, Greg: "I thought the new agent program was supposed to convert the handoffs into graphs so theere
    isn't so much to read?" IT IS SUPPOSED TO, AND THAT PART IS NOT BUILT. In the graph adoption plan (Markets
    `research/kalshi/frankie_boss/CLAUDE_ARCH_PLAN_GRAPH_ENGINEERING_ADOPTION_20261123.md`) turning the handoffs
    into the knowledge graph is stage 4 (4b: the scout on Haiku extracts each handoff into claims tied to their
    source) and starting a session from the graph instead of the handoff pile is stage 5. Only stage 1 (the four
    agents) and stage 7 (paper trading) have been started; stages 2 to 6 and 8 are filed, waiting on his word.
    No graph exists yet (markets-data has no `graph/` folder). Handoff W was written as prose, like the ones
    before it.

26. 2026-11-24, Greg. NO MORE PAPER TRADING UNTIL THE AGENT PROGRAM IS BUILT: "We need to finish building everything
    for the agents before we do any more paper trading". The steps are stages 2 to 6, 7d, 7f and 8 of the graph
    adoption plan; the next session is to finish all of them. Handoff: DATA `NEXT_SESSION_HANDOFF_20261124X.md`.

## D. Runs

1. 2026-11-23 evening (trade day Tue Nov 24, 2026), 8:05 pm to 8:12 pm Eastern. THE FIRST RUN WITH ORDERS, on Greg's
   go ("Just do run 2 with trades"): the live sandbox feed, all 32 strategies, 1 lot a trade, at most 20 orders,
   account 5WY79188, code `ef58c85`. Not scored.
   - It stopped itself at the order budget after 7 minutes (19 submits, the 20th kept for the flatten).
   - 62 paper trades on the ledger from 13 strategies (CL14 17, CL06 12, CL15 7, CL13 5, CX07 4, CL05 3, CL07 3,
     CX01 3, CL10 2, CL11 2, CX03 2, CX08 1, CX15 1). 120 intended orders.
   - 19 submitted, ALL SELLS. 13 filled (3.545 to 3.548). 6 answered with a server error and never became orders.
   - 101 not sent: 43 buys refused by the sandbox at the dry run; 34 closes held back because their open had not
     filled; 23 cut off by the budget stop; 1, the flatten itself, refused.
   - The account was left SHORT 13 LOTS. The close-out by hand did not get through either (C14).
   - What it showed: C8 to C11 and C14 to C18.
   Records: DATA `paper-trading-20261123/live-runs/orders-20261124T010517Z/` (the run) and
   `closeout-20261124T011335Z/` (the close-out and two dry-run probes).

2. 2026-11-23 evening (trade day Tue Nov 24, 2026), 8:14 pm to 8:31 pm Eastern. A FEED-ONLY RECORDING, no orders
   (the runner without `--orders`), to catch the feed as it reached the moments the first run's orders filled.
   440 messages, 183 paper trades on the ledger, 364 intended orders written and none sent. It gave C19.
   Record: DATA `paper-trading-20261123/live-runs/feed-only-20261124T011440Z/`. Not scored.

(Each live run is added here when it has been made: the date, how long, how many orders, where its record is, and
the gaps and misses it showed. Each of those also gets its own line in section A, B or C.)
