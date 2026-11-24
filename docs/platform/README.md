# Paper platform requirements

Use the [35-item platform wishlist](OWN_PLATFORM_WISHLIST_20261123.md) when changing the platform. Its DECIDED, OPEN and SUGGESTED labels record Greg's decisions. Development and verification take place in this repository. Purchased market data, strategy research evidence and run receipts live in `DavisAI1974/markets-data`.

## What already works and what needs building

| Wishlist items | Platform scope | Evidence and remaining work |
| --- | --- | --- |
| 1–4 | Market, independent strategies, versioning, open-source platform | The purchased NG replay loads the 30 [versioned rules](../../paper_platform/rules). [NautilusTrader’s L3 order book](../../paper_platform/nautilus_book.py) supplies the bid and ask for historical replay. The [DBN recorder](../../paper_platform/record_mbo.py) accepts a manually selected live subscription. The paper runner still takes its input from purchased replay. Options are future work. |
| 5–8 | Order-level data, week-long book, lossless capture, real inputs | [Replay tests](../../paper_platform/test_replay.py) check purchased DBN fields and source order, compare Sunday’s native quotes to ROOT and verify the carried native order queues against ROOT after the UTC boundary. Each delivered archive record has a replay-host receive timestamp. The [capture journal](../../paper_platform/capture.py) stores delivered DBN frames and host receive times. On restart it compares the MBO replay against the committed prefix before extending the tape. Live interruption and recovery still need operational evidence. |
| 9–12 | ROOT calculations, causal decisions, shared live and replay behavior | The [strategy adapter](../../paper_platform/inputs.py) supplies the two flow windows. [Cutoff checks](../../paper_platform/causal_gate.py) compare decisions on full and physically truncated purchased inputs. Captured MBO and archive replay use the same weekly book and tape projection. Live decision parity still needs evidence. |
| 13–18 | Trade-day calendar, prices and per-trade evidence | The runner uses the named Eastern trade day and records quotes, missing evidence and unpriced paper orders. Fill pricing remains Greg's open decision. |
| 19–23 | Manual start, fresh runs, budgets and the clock stop | The [paper workflow](../../.github/workflows/purchased_mbo_paper.yml) and [session](../../paper_platform/session.py) implement these controls. Verification uses bounded offline checks. |
| 24–27 | Phone view, publication, notifications and per-strategy trade list | The [viewer](../../paper_platform/relay.py) streams events and filters by run and rule. Selecting a rule shows its paired entry and exit orders across the full run, with quotes, reasons, open positions, manager exits and administrative closes. End-to-end notification and publication during an operational run require separate evidence. |
| 28–33 | Journal, receipts, traceability, unique order tags and credentials | The journal records rule and source references. The receipt fingerprints run files. The data writer is scoped to `markets-data`. Fill-level traceability awaits a fill policy. |
| 34–35 | Preserve ROOT and review the first build | The platform imports ROOT alongside its own code. Use the [GitHub verification workflow](../../.github/workflows/verify_paper_platform.yml) and record review of changed behavior. |

## Earlier sandbox design

The [sandbox runner plan](RUNNER_PLAN_20261123.md) and [thin-feed rule inventory](RULES_ON_TASTY_FEED_20261123.md) preserve how the earlier tastytrade work was specified. The implementation, tests and [running list](https://github.com/DavisAI1974/paper-trading-platform/blob/e077b2e462421eb30f1ab7288bf0e33b57df291e/tasty/RUNNING_LIST.md) are already in this repository on `claude/tasty-runner`. The [static phone preview](previews/ng-paper.html) is a historical design artifact.

The three dated documents are byte-identical to their [archived sources](https://github.com/DavisAI1974/markets-data/tree/111e46bc156ecf05bfa25814cea3b9c7040a5fc9/tasks/family-research-20261022/research-union-current/paper-trading-20261123). Their references to sibling evidence directories refer to that archive. The preview is byte-identical to the [archived HTML](https://github.com/DavisAI1974/markets-data/blob/80e90122571f1957945570d7fa2562820bb3d8ed/paper-trading/previews/ng-paper.html).
