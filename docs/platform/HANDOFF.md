# Ccode handoff for draft PR 5

Work in [DavisAI1974/paper-trading-platform](https://github.com/DavisAI1974/paper-trading-platform) on `codex/strategy-trades-and-mbo-capture` in [draft PR #5](https://github.com/DavisAI1974/paper-trading-platform/pull/5). The implementation handoff point is [af4ce2b](https://github.com/DavisAI1974/paper-trading-platform/commit/af4ce2b). Start from the latest branch head so subsequent commits are included.

The [marked 35-item checklist](OWN_PLATFORM_WISHLIST_20261123.md) is the sole running list. It has 16 checked items. Update the text beneath the relevant number in the same commit as the work. A draft implementation stays unchecked until the checklist's upstream or main criterion is met.

## Ccode's five items

Start with **17**. Work in the order below, one number at a time. If the current item needs a decision, credential or permission only Greg can supply, record the exact blocker under that number before taking up another. Ccode owns the agents. Frankie and the agents choose placement and size for every strategy.

| Order | Wishlist item | Where it stands and what to do |
| --- | --- | --- |
| 1 | **17 · Paper execution** | PR #5 accepts per-rule entry and exit instructions, contract quantities, posted and crossing orders, and partial fills. The branch currently has an execution-plan README. Coordinate Frankie and the agents to supply their actual decisions in a versioned plan under [execution_plans](../../paper_platform/execution_plans/README.md). Use the contract definition and Databento instrument ID for the selected purchased NG tape. Include manager parent rules and their close instructions where applicable. Record any missing author decisions by rule ID. Execution evidence waits for Greg's go. |
| 2 | **2 · All 30 independent strategies** | CL01–CL15 and CX01–CX15 are in the [catalogue](../../paper_platform/rules). A configured rule gets its own venue and inventory. Use the item 17 plan to account for all 30 and finish any wiring a selected rule or manager needs. Preserve separate order, fill and trade records for each. End-to-end operation remains unproven. |
| 3 | **23 · Budget stops, including agents** | The paper runner has message, event and 5.75-hour limits. It starts the Pushover stop notice independently of receipt and relay delivery. Ccode owns the remaining agent-run budget handling. Apply the authorized budget in the agent launcher, stop work when it is reached and route the reason to Greg's phone. If the applicable budget is unspecified, identify the missing decision. |
| 4 | **26 · Phone notices** | Greg selected Render for the HTML/htmx phone UI and Pushover for budget stops, run stops and failures. The repo has the two Pushover Actions secret names configured. Render connection settings and actual phone receipt are outstanding. Finish the service wiring for the named events. Keep secret values in the service secret stores. Phone delivery evidence waits for permission to exercise it. [Render blueprint](../../paper_platform/render.yaml) · [Notice sender](../../paper_platform/run.py) |
| 5 | **35 · Claude's independent review** | Codex's implementation is in this draft. Ccode owns arranging the independent Claude review after the build and recording any adjustments against their original wishlist numbers. Greg's current instruction defers checks, validation and tests. Leave the review pending during this build-only pass. |

## Codex's current item

**12 · Stored replay.** The replay workflow defaults to the pinned purchased NG archive. It can restore a purchased or live MBO checkpoint from markets-data. A compatible original live-run receipt supplies its agent plan, first decision-eligible cursor, last processed frame and stop reason. The replay starts fresh inventory and writes its own journal. Live/replay parity still needs operational evidence. Codex retains this item.

**31 · Repeated order tags.** [8ca3f04](https://github.com/DavisAI1974/paper-trading-platform/commit/8ca3f04) put the session-wide tag claim ahead of cancellation, journaling and matching. Strategy, manager and administrative orders share that guard. [3821f1e](https://github.com/DavisAI1974/paper-trading-platform/commit/3821f1e) consolidated stop liquidation per trade. These changes are on the draft branch.

## Operating instructions

Keep development on GitHub and use the same draft PR. Purchased MBO and run output belong in [DavisAI1974/markets-data](https://github.com/DavisAI1974/markets-data). Preserve ROOT and the archived originals. Greg requested implementation without running checks, validation or tests. Wait for Greg's explicit go before starting a paper session. No paper session has started. The earlier 26 passing tests and four-trade-day cutoff evidence belong to the merged replay work.
