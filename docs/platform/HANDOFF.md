# Paper platform handoff

Continue on `codex/strategy-trades-and-mbo-capture` in [draft PR 5](https://github.com/DavisAI1974/paper-trading-platform/pull/5). The [marked 35-item wishlist](OWN_PLATFORM_WISHLIST_20261123.md) is the running list. Its 16 checked items count work supplied by NautilusTrader or implemented on main. The status under each open item records draft work and the outstanding requirement.

## Where to resume

Start with **item 31, “An order is never sent twice. Each carries its own unique tag.”** The session assigns tags and the relay handles repeat delivery of a journal event. In [the native order submission path](../../paper_platform/paper_execution.py), `RuleVenue.submit` can receive the same tag again. For a close, it can cancel resting orders before it reaches the order registry. Make repeated submission idempotent before any cancellation, journal emission or matching-engine call. Cover strategy entries, exits, manager orders and administrative closes through the existing submission path. Preserve partial fills and each strategy’s own inventory.

For **item 30**, the journal already records the fill’s order ID, the order’s rule commit and the research finding. A separate phone evidence page was discussed and has not been built. Follow the numbered requirements.

## What the draft contains

- **Items 1, 6, 7, 9 and 12.** Purchased replay and captured MBO share the book and calculation path. The recorder identifies each connection and writes durable frame positions and stop reasons. The runner records the last input it processed separately from the recorder endpoint. An uninterrupted live week and live/replay parity remain open.
- **Items 2, 16, 17, 20 and 27.** Native L3 paper execution supports resting and crossing orders, partial fills and independent manager inventory. The strategy view lists entry and exit quotes, reasons and fills. On feed loss the draft cancels resting orders and records any remaining exposure. Frankie and the agents choose each strategy’s placement and size. Ccode owns the agents.
- **Items 23–26 and 28.** The HTML/htmx phone view includes terminal evidence for the selected run and strategy. Journal publication retries the same Git commit. Render and Pushover are Greg’s selected services. Hosted delivery and phone receipt remain open.

## Operating instructions

Keep development on GitHub. Use the purchased MBO in `DavisAI1974/markets-data` and write run output there. Preserve ROOT and the archived originals. Update the existing wishlist as work lands. Greg requested implementation without checks, validation or tests. The earlier 26 passing tests and four-trade-day cutoff result apply to the merged replay change. The draft changes are untested. No paper session has started. Starting one requires Greg’s explicit go. Claude’s independent review belongs to Ccode under item 35.
