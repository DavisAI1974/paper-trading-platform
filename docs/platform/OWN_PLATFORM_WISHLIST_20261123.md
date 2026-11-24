# The 35-item paper platform checklist

**16 of 35 checked off.** A checked box means the requirement is supplied by NautilusTrader or implemented on main. An open box names the remaining work. The original DECIDED, OPEN and SUGGESTED labels record the November 23 wishlist. The status beneath each item records subsequent decisions and progress. [NautilusTrader comparison](NAUTILUSTRADER_COVERAGE.md) · [Draft PR 5](https://github.com/DavisAI1974/paper-trading-platform/pull/5) · [Archived original](https://github.com/DavisAI1974/markets-data/blob/111e46bc156ecf05bfa25814cea3b9c7040a5fc9/tasks/family-research-20261022/research-union-current/paper-trading-20261123/OWN_PLATFORM_WISHLIST_20261123.md)

[Ccode handoff for items 17, 2, 23, 26 and 35](HANDOFF.md) · [Draft implementation branch](https://github.com/DavisAI1974/paper-trading-platform/tree/codex/strategy-trades-and-mbo-capture)

## WHAT IT IS

- [ ] **1.** Paper trades natural gas futures (and their options later) on a live feed. No real money, no real orders.

  **In progress.** NG replay is on main. PR 5 connects captured live MBO to the paper runner and native execution. The draft has not been run. Options are deferred.

- [ ] **2.** All 30 strategies run (Claude's CL01 to CL15, Codex's CX01 to CX15), each on its own, never mixed. DECIDED

  **In progress.** All 30 rule files are available. End-to-end operation depends on Frankie’s and the agents’ execution choices. Six manager rules reference a parent entry.

  **Ccode, November 24. Waiting on item 17.** The item 17 plan names which rules take part and each manager's parent entries. No plan exists yet, so the wiring for a selected rule or manager has not been started.

- [x] **3.** A strategy is a rule that opens and closes a trade. Each rule is a file in git; each change is a commit. DECIDED

  **Our build.** Each rule has its own tracked JSON file. [Rule catalogue](../../paper_platform/rules)

- [x] **4.** Our own, open source, changed to fit. NautilusTrader is the candidate (reads Databento order-by-order data and has a paper sandbox). SUGGESTED

  **Upstream + our build.** NautilusTrader supplies DBN support, the L3 book and a simulated exchange. Main integrates the L3 book. [Native adapter](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/integrations/databento.md) · [Our integration](../../paper_platform/nautilus_book.py)

## THE DATA

- [x] **5.** Order-by-order feed: every single order with its id, every trade with its buy/sell side, nanosecond times. The tastytrade sandbox feed gave best bid/ask and last trade only so far, which is why we build our own.

  **Included upstream.** Databento MBO and NautilusTrader’s adapter support order-level data. Our purchased reader preserves the raw fields, including unavailable aggressor information. [MBO adapter](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/integrations/databento.md) · [Reader](../../paper_platform/feed.py)

- [ ] **6.** The exact order book kept for the whole week, seated at the exchange's Sunday start-up, carried straight through. Never restarted at midnight UTC. DECIDED

  **In progress.** Main seats the book on Sunday and checks the next UTC restatement. A full uninterrupted live week is outstanding.

- [ ] **7.** Everything the feed sends is recorded whole, as it arrives, with our receive time in nanoseconds. Raw, nothing dropped. DECIDED

  **In progress.** PR 5 records DBN deliveries and host receive times and adds recovery checkpoints. A replacement runner can restore encrypted checkpoints from markets-data. The recorder persists the connection ID, last durable frame position and termination reason. The final publisher also retries a locally committed checkpoint after a failed push, even when the capture has not changed. A hard loss between checkpoints can lose original receive timestamps.

- [x] **8.** Real data only. No made-up numbers, not even to test code. DECIDED

  **Our build.** The verification workflow uses the pinned purchased NG archive for strategy evidence. [Workflow](../../.github/workflows/verify_paper_platform.yml)

## THE CALCULATIONS

- [ ] **9.** A SECOND VERSION OF ROOT does the live calculations. It sits beside ROOT, never replaces it, and uses ROOT's own calculation code so live and historical numbers come from the same math. DECIDED

  **In progress.** The strategy adapter uses ROOT calculations on replay. PR 5 feeds captured MBO through the same session. Live operation is unproven.

- [x] **10.** Dipole and exhaustion are the crypto formulas on the natural gas tape (60 and 20 seconds), read at the nanosecond. DECIDED

  **Our build.** The adapter uses the crypto calculation code for both tape-clock windows. [Strategy inputs](../../paper_platform/inputs.py)

- [x] **11.** Every rule sees only what is known at that tick. Check built in: the trades opened on a tape cut at a tick equal the trades opened on the whole tape up to that tick. DECIDED

  **Our build.** The physical cutoff gate compares ordered decisions on four purchased trade days. The existing evidence applies to main. [Cutoff gate](../../paper_platform/causal_gate.py)

- [ ] **12.** Replay mode: the same platform runs a stored trade day's tape, tick by tick, with the same code as live. SUGGESTED

  **Built in draft, awaiting parity evidence.** The manual replay workflow defaults to the pinned purchased NG archive. It can also restore a weekly purchased or live MBO checkpoint from markets-data and feed its records through the same book, calculations and native venues. For receipt replay the workflow loads the original platform commit and installs its requirements. The selected Git ref must contain that commit. The live receipt supplies the agent plan, first eligible cursor, last processed frame and stop reason. Replay seats from Sunday, warms through the original start, and caps delivery at the original endpoint. It records the checkpoint commit and original run ID. Receipts predating the eligibility field support whole-tape replay only. New capture receipts fingerprint the ordered MBO frames consumed and retain strategy timer expiries admitted after the final processed frame. Receipt replay applies those timers before the recorded stop. A [manual journal comparison](../../.github/workflows/compare_recorded_replay.yml) reads a completed pair from markets-data, compares the recorded input window and ordered strategy decisions, and identifies the first differing signal by rule, journal sequence and source reference. Evidence from an authorized live run and its replay remains outstanding.

## THE RULES OF THE HOUSE

- [x] **13.** A TRADE DAY is 6 pm Eastern the evening before to 5 pm Eastern on the named date. The 5 to 6 pm hour is its own marked stretch: no trades, messages kept. Never a bare "day" or date. DECIDED

  **Our build.** ROOT labels the Eastern trade day and retains the break as its own period. [Calendar](../../root/calendar.py)

- [x] **14.** NO MID PRICE anywhere. DECIDED

  **Our build.** Paper decisions carry the bid and ask. [Decision engine](../../paper_platform/engine.py)

- [x] **15.** NO TRADE COST in any research figure: no fee, slippage, or fill assumption. DECIDED

  **Our build.** The research ledger records the observed quotes without a cost or return calculation. [Journal](../../paper_platform/session.py)

- [ ] **16.** NO AVERAGING, pooling, ranking or labels. Each trade is its own line. DECIDED

  **Built in draft.** The phone page and trades API keep one chronological row per run, strategy and trade ID in both the full run and the strategy filter. A signal can open a row before an order exists. Later orders, partial fills and administrative closes update that row. A manager’s trade retains its own row and can reference its parent entry. Review and merge are pending.

- [ ] **17.** How a paper fill is priced: OPEN, Greg's word. Until then each paper trade carries both quotes (bid and ask) at its entry and its exit, and no result figure.

  **Greg decided the execution behavior.** Paper orders can post a bid or offer, lift an offer or hit a bid. Frankie and the agents choose placement and size for each strategy. PR 5 implements native L3 queue tracking, liquidity consumption and partial-fill records. The two family agents have supplied their execution plan. Operational evidence is pending.

  **Earlier Ccode handoff, November 24.** The contract definition and the Databento instrument ID (150611, NGX3) for the purchased archive are recorded with their sources. [Contract record](../../paper_platform/execution_plans/NG_OCT2023_CONTRACT.md) Two decider agents, one for each rule family, were briefed to choose placement and size from each rule's own words, with "no decision" allowed. Both were stopped at the session change before they recorded a choice in that pass. The [item 17 handoff on Ccode's markets-data branch](https://github.com/DavisAI1974/markets-data/blob/ccr-349e2ffe-07p8od/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124/README.md) now includes the family briefs, both authors' rule text and open questions, the plan format and the NGX3 contract record. Each family brief names its answer file.

  **Codex took over #17 and both family decision agents.** The [versioned NGX3 plan](../../paper_platform/execution_plans/ngx3-599eb8c0d48926292bbb4ef83fd518d5d2753daf.json) contains decisions for all 30 strategies, including parent entry IDs and parent-close behavior for CX05, CX06, CX11, CX12, CX13 and CX14. The [CL decisions](https://github.com/DavisAI1974/markets-data/blob/599eb8c0d48926292bbb4ef83fd518d5d2753daf/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124/CL-answer.json) and [CX decisions](https://github.com/DavisAI1974/markets-data/blob/599eb8c0d48926292bbb4ef83fd518d5d2753daf/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124/CX-answer.json) record the agents' reasons by rule ID. Both agents chose one contract per opening order and crossing exits. Frankie's separate input remains pending for all 30. His launch and the first paper session still await Greg's go. The draft has no execution evidence.

- [x] **18.** If the feed lacks what a rule needs, that rule's trades are marked "not scored: the feed lacks &lt;what&gt;" and stay out of all scoring. DECIDED

  **Our build.** Missing inputs produce a reason on the affected observation. [Decision engine](../../paper_platform/engine.py)

## THE RUN

- [x] **19.** Runs on GitHub Actions. No AWS. DECIDED

  **Our build.** The repository defines the manual GitHub Actions runner. [Paper workflow](../../.github/workflows/purchased_mbo_paper.yml)

- [ ] **20.** A run STOPS ITSELF AT 5.75 HOURS, flattens every position, and marks each closing order "flattened at the 5.75-hour stop": not trade data. DECIDED

  **Built in draft, awaiting stop evidence.** The authorized Actions job starts one 5.75-hour clock shared by the recorder, preflight and paper runner. The runner rechecks it after a feed read and before matching. On the clock stop it cancels resting orders, submits one administrative close per trade with filled inventory, and marks the order and resulting fills as excluded from trade data. A failed close leaves the other trades eligible for shutdown. The terminal journal and receipt record elapsed time, outstanding order tags, actual remaining contracts and errors. A failed or incomplete clock-stop liquidation is reported as a shutdown failure. An authorized run is still needed to demonstrate this behavior.

- [x] **21.** Each run starts over new. Nothing carries over. DECIDED

  **Our build.** Each session receives a fresh run ID, engine, inventory and journal. [Session](../../paper_platform/session.py)

- [x] **22.** Nothing starts without Greg's go. DECIDED

  **Our build.** The paper workflow requires Greg’s manual go and restricts the triggering account. [Start gate](../../.github/workflows/purchased_mbo_paper.yml)

- [ ] **23.** A BUDGET: the moment any budget is reached everything stops and Greg is notified on his phone at once. Holds for agent runs too. DECIDED

  **In progress.** The platform has message, event and wall-clock limits. The runner wakes a Pushover worker when the session enters its stop state so receipt writing and Render delivery can proceed independently. Phone delivery and Ccode’s agent-run budget handling need evidence.

  **Ccode, November 24. Agent-run budget built in draft, not run.** The branch had no agent launcher, so Ccode added one. [Agent launcher](../../paper_platform/agent_launcher.py) · [Manual workflow](../../.github/workflows/agent_run.yml) A run started through it sends one brief to a model. The launcher takes the API's own count of the input, adds the output reserve and refuses the call when the total would pass the token budget. A timer on the process ends the run at the clock budget. A budget stop, a run stop, a failure and a completed run each publish a record on an `agent-runs/<run>` branch of markets-data and send the phone notice through the platform's notice sender. The launcher refuses to start without Greg's go, the Pushover secrets and the API key. The workflow reads a brief from the platform repository only. The item 17 briefs and their source files are in markets-data, so those agents cannot go through it as built.

  **Missing decisions.** The budget for an agent run: none is on record for the platform. The launcher has no default, so Greg supplies the token budget, the output reserve and the clock budget at each start. The `ANTHROPIC_API_KEY` Actions secret is Greg's to add. Agents started inside a Claude Code session do not pass through the launcher. In Ccode's sessions so far the token count of such an agent was reported only when it ended, so a budget cannot stop one part-way. That gap is open.

## WHAT GREG SEES

- [ ] **24.** REAL TIME: every signal, order, fill and status the moment it happens, on one page that reads well on a phone. DECIDED

  **Built in draft, awaiting hosted delivery.** The Render phone view follows the latest run across stops and reconnects. A selected run stays pinned. Signals, order instructions, individual fills and status changes appear in journal order, newest first on the page. A strategy filter retains run-wide status events. Earlier events are accessible on the same page. The runner sends pending journal events in sequence to Render alongside required Git pushes. An authorized run and hosted phone observation are still needed to demonstrate delivery.

- [ ] **25.** The same events appended to the git record in markets-data, pushed every minute and at once on every order and fill. DECIDED

  **Built in draft, awaiting publication evidence.** Each journaled order and fill queues its exact event-file and receive-time boundaries for a distinct Git commit on the run’s markets-data branch. The watchdog schedules a snapshot every minute independently of Render. The Git worker publishes in order and retries the same commit after an ambiguous push. The runner waits for outstanding order and fill pushes before admitting the next tick. Shutdown drains administrative events and publishes the receipt. An authorized run is still needed to observe the remote commit sequence and timing.

- [ ] **26.** A phone notice for: a budget stop, a run stop, a failure. The live store and the notice service are named to Greg before anything is set up. OPEN

  **Providers approved.** Greg selected Render for the HTML/htmx dashboard and Pushover for notices. The repository has the two Pushover secret names configured. Render connection settings and receipt of a phone notice remain open.

  **Ccode, November 24. Remaining wiring built in draft, not run.** The runner already sends the notice for a budget stop, a run stop and a failure it can see. [Notice sender](../../paper_platform/run.py) Ccode added a workflow that sends a notice when one of the four manual workflows ends in any way other than success. It covers what the runner cannot report: a step that fails before the runner starts, a cancelled or timed-out job and a lost runner. [Run-end notice](../../.github/workflows/run_end_notice.yml) GitHub starts it only from the default branch, so it takes effect after merge. When the runner or the agent launcher has also reported, Greg receives both notices. That includes an agent run's budget stop and run stop: the launcher ends those with a failing exit, so GitHub reports the run as failed. Agent runs send theirs through the same sender (item 23).

  **Settings only Greg can supply.** On Render, service `ng-paper-viewer`: `PAPER_VIEW_PASSWORD` and `PAPER_RELAY_TOKEN`. In GitHub Actions: the variable `PAPER_RELAY_URL` with the Render service's HTTPS address, and the secret `PAPER_RELAY_TOKEN` with the same value as on Render. Ccode read, set and sent no value. Receipt of a notice on the phone has not been exercised and waits for Greg's permission.

- [ ] **27.** Per strategy, a plain list of its trades: time in, time out, both quotes at each end, why it opened, why it closed. No summary number until Greg sets one. SUGGESTED

  **Built in draft.** Each strategy row shows the first entry fill and the close fill that returns that trade to zero contracts, with Eastern tape time, both observed MBO quotes and the reason from the corresponding order. Partial exits leave the trade open. Signal-only trades show decision times and quotes. Individual fills, administrative closes and unsubmitted instructions remain visible. Older fills without recorded quotes show those quotes as unavailable. Review and merge are pending.

## THE RECORD

- [ ] **28.** Append-only: every signal, order, fill and status change, each with its time in nanoseconds, the rule version (git commit) that made it, and the feed messages it was made from. DECIDED

  **In progress.** Signals and orders have source references and rule revisions. PR 5 adds native fill events with source, order, rule revision and finding. The session resolves the last Git commit that changed each rule file separately from the platform commit. Review is pending.

- [x] **29.** A receipt for every run: code commit, inputs, constants used, files written with their fingerprints. DECIDED

  **Our build.** The session receipt fingerprints the code, inputs and output files. [Receipt writer](../../paper_platform/session.py)

- [ ] **30.** Every order is traceable back: fill to order to rule version to the finding the rule rests on. DECIDED (plan 7d)

  **In progress.** An order links to its signal, rule commit and research finding. PR 5 links each fill to the original order tag and records the commit that changed its rule file. Administrative liquidation links to the closing signal when one exists. Review is pending.

- [ ] **31.** An order is never sent twice. Each carries its own unique tag. DECIDED

  **Built in draft.** All native venues share a session-long order-tag registry. The first submission claims its tag before cancellation, journaling or matching, including a close with zero inventory or a missing quote. Entries, strategy and manager exits, and administrative closes use the same guard. Each venue retains its own partial fills and inventory. Review and merge are pending.

- [ ] **32.** Keys and tokens never in any file, print or commit. DECIDED

  **In progress.** PR 5 fetches public source and purchased inputs anonymously. Where publication is enabled, the Actions secret goes directly to an SSH agent on the hosted runner. Git stores the markets-data push URL and uses the agent socket. Pushover and Render credentials come from secret stores. The historical sandbox credential was excluded from the migration. Its status was set aside at Greg’s request.

- [x] **33.** Output goes only to markets-data, never to the Markets code repository. DECIDED

  **Our build.** The publisher restricts its write destination to markets-data. [Publisher](../../paper_platform/run.py)

## ONLY ADD

- [x] **34.** Replace no existing file or layout. ROOT, its four-trade-day gate and its tests stay as they are. DECIDED

  **Our build.** The original ROOT implementation and four-trade-day gate are preserved. [Platform requirements](README.md)

## WHO BUILDS

- [ ] **35.** Codex does the first build. Claude checks it after and makes the adjustments needed. DECIDED

  **Pending independent review.** Codex built the first changes. Claude’s review and adjustments are owned by Ccode.
