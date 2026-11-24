# Ccode handoff for draft PR 5

Work in [DavisAI1974/paper-trading-platform](https://github.com/DavisAI1974/paper-trading-platform) on `codex/strategy-trades-and-mbo-capture` in [draft PR #5](https://github.com/DavisAI1974/paper-trading-platform/pull/5). The implementation handoff point is [af4ce2b](https://github.com/DavisAI1974/paper-trading-platform/commit/af4ce2b). Start from the latest branch head so subsequent commits are included.

## The two lists

- [Greg's original 35-item wishlist in markets-data, archived November 23](https://github.com/DavisAI1974/markets-data/blob/111e46bc156ecf05bfa25814cea3b9c7040a5fc9/tasks/family-research-20261022/research-union-current/paper-trading-20261123/OWN_PLATFORM_WISHLIST_20261123.md) preserves the original wording and decisions.
- [Running 35-item checklist on the PR branch, with completed items checked](https://github.com/DavisAI1974/paper-trading-platform/blob/codex/strategy-trades-and-mbo-capture/docs/platform/OWN_PLATFORM_WISHLIST_20261123.md) records current progress. At handoff, 16 of 35 are checked: 3, 4, 5, 8, 10, 11, 13, 14, 15, 18, 19, 21, 22, 29, 33 and 34.

Use the marked checklist as the sole running list. Update the text beneath the relevant number in the same commit as the work. A draft implementation stays unchecked until the checklist's upstream or main criterion is met.

## Ccode's five items

Ccode can commit and push all changes for his five items to this branch and draft PR. Fetch the latest head and integrate any concurrent commits before pushing.

Start with **17**. Work in the order below, one number at a time. If the current item needs a decision, credential or permission only Greg can supply, record the exact blocker under that number before taking up another. Ccode owns the agents. Frankie and the agents choose placement and size for every strategy.

| Order | Wishlist item | Where it stands and what to do |
| --- | --- | --- |
| 1 | **17 · Paper execution** | PR #5 accepts per-rule entry and exit instructions, contract quantities, posted and crossing orders, and partial fills. The branch currently has an execution-plan README. Coordinate Frankie and the agents to supply their actual decisions in a versioned plan under [execution_plans](../../paper_platform/execution_plans/README.md). Use the contract definition and Databento instrument ID for the selected purchased NG tape. Include manager parent rules and their close instructions where applicable. Record any missing author decisions by rule ID. Execution evidence waits for Greg's go. |
| 2 | **2 · All 30 independent strategies** | CL01–CL15 and CX01–CX15 are in the [catalogue](../../paper_platform/rules). A configured rule gets its own venue and inventory. Use the item 17 plan to account for all 30 and finish any wiring a selected rule or manager needs. Preserve separate order, fill and trade records for each. End-to-end operation remains unproven. |
| 3 | **23 · Budget stops, including agents** | The paper runner has message, event and 5.75-hour limits. It starts the Pushover stop notice independently of receipt and relay delivery. Ccode owns the remaining agent-run budget handling. Apply the authorized budget in the agent launcher, stop work when it is reached and route the reason to Greg's phone. If the applicable budget is unspecified, identify the missing decision. |
| 4 | **26 · Phone notices** | Greg selected Render for the HTML/htmx phone UI and Pushover for budget stops, run stops and failures. The repo has the two Pushover Actions secret names configured. Render connection settings and actual phone receipt are outstanding. Finish the service wiring for the named events. Keep secret values in the service secret stores. Phone delivery evidence waits for permission to exercise it. [Render blueprint](../../paper_platform/render.yaml) · [Notice sender](../../paper_platform/run.py) |
| 5 | **35 · Claude's independent review** | Codex's implementation is in this draft. Ccode owns arranging the independent Claude review after the build and recording any adjustments against their original wishlist numbers. Greg's current instruction defers checks, validation and tests. Leave the review pending during this build-only pass. |

## Codex's current item

**27 · Per-strategy trade list.** The phone list and trades API expose first-entry and final-exit fill times, their observed bid and ask, and the order reasons. Partial liquidation stays open until the final filled contract closes. Quote-only rows retain their decision timestamps. This implementation is in draft.

**25 · Git publication.** Each order and fill queues a separate, byte-bounded journal snapshot. The watchdog also schedules minute snapshots while the phone relay is busy. A single worker pushes them in order and includes the final receipt. Operational evidence from markets-data is pending.

**24 · Real-time phone view.** The default page follows the latest run, including after a stop. A run selected from history stays pinned. The draft adds earlier-event paging, visible run-wide status under strategy filters and concurrent Render and Git delivery. Hosted delivery remains open.

**20 · The 5.75-hour stop.** The draft shares one Actions clock across startup, capture and paper execution. Terminal records carry the remaining orders, contracts and any shutdown errors. The implementation awaits authorized operational evidence.

**16 · Individual trades.** The full run and each strategy filter now use the same individual trade rows. Each row retains its own orders and partial fills. The change is built in draft and awaits review and merge.

**12 · Stored replay.** The replay workflow defaults to the pinned purchased NG archive. It can restore a purchased or live MBO checkpoint from markets-data. A compatible original live-run receipt supplies its agent plan, first decision-eligible cursor, last processed frame and stop reason. The replay starts fresh inventory and writes its own journal. Receipt replay uses the original platform commit. The manual comparison of two completed journals is implemented. Live/replay parity still needs operational evidence.

**31 · Repeated order tags.** [8ca3f04](https://github.com/DavisAI1974/paper-trading-platform/commit/8ca3f04) put the session-wide tag claim ahead of cancellation, journaling and matching. Strategy, manager and administrative orders share that guard. [3821f1e](https://github.com/DavisAI1974/paper-trading-platform/commit/3821f1e) consolidated stop liquidation per trade. These changes are on the draft branch.

## Operating instructions

Keep development on GitHub and use the same draft PR. Purchased MBO and run output belong in [DavisAI1974/markets-data](https://github.com/DavisAI1974/markets-data). Preserve ROOT and the archived originals. Greg requested implementation without running checks, validation or tests. Wait for Greg's explicit go before starting a paper session. No paper session has started. The earlier 26 passing tests and four-trade-day cutoff evidence belong to the merged replay work.
