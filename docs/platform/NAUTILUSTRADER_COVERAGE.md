# NautilusTrader coverage of the 35 platform requirements

This comparison uses the repository's pinned NautilusTrader **v1.231.0**. “Project implementation” describes source in main and draft PR 5. A feature available upstream still needs an application caller, configuration and project-specific evidence.

The Databento adapter loads DBN and subscribes to live MBO. The native L3 book maintains orders by ID. The strategy API supports shared backtest and live code. The sandbox execution client supplies a simulated exchange with configurable fill assumptions. These capabilities let us reuse the market engine while keeping the original feed, ROOT decision evidence and Greg's pricing policy under project control. [Databento adapter](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/integrations/databento.md) · [Order book](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/concepts/order_book.md) · [Strategies](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/concepts/strategies.md) · [Sandbox execution](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/adapters/sandbox/execution.py) · [Fill models](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/docs/concepts/backtesting/fill-models.md)

| Item | Requirement | Upstream capability or scope | Project implementation and remaining work |
| --- | --- | --- | --- |
| 1 | NG futures now, options later | Futures instruments, DBN adapter and sandbox execution | NG purchased replay uses the native book. Live capture is in the draft. Options are deferred. |
| 2 | 30 independent rules | Strategy IDs and multiple strategy instances | The platform loads 30 versioned definitions. Six manager rules assess a parent entry independently. They do not own that position. |
| 3 | Version each rule | Strategy source and configuration | The repository keeps one JSON file per rule and records its Git revision. |
| 4 | Open-source platform | NautilusTrader v1.231.0 | The native L3 book is integrated. The platform owns ROOT and its operating rules. |
| 5 | Every order and sided trade | DBN MBO maps to native deltas. Trade ticks and order IDs are supported. | The archive reader exposes raw fields alongside native quotes. The recorder retains DBN deliveries. |
| 6 | Continuous weekly book | Stateful L3 order book | Our Sunday clear, UTC restatement handling and queue comparison define the NG week. |
| 7 | Lossless delivery and receive time | Decoded live and historical market data | Our journal retains frame bytes, control messages and host timestamps. Exact recovery after host loss needs evidence. |
| 8 | Real inputs only | DBN file loader | The project pins purchased NG files in markets-data. Existing strategy checks use that archive. |
| 9 | Second ROOT alongside original | Custom strategy and data components | The strategy adapter uses the project's ROOT calculations. Upstream has no Greg-specific formulas. |
| 10 | 60 s dipole and 20 s exhaustion | Nanosecond events and timers | The shared adapter uses the crypto calculation code and tape receive times. |
| 11 | Cutoff causality | Historical replay and clocks | Our physical cutoff comparison covers four purchased trade days. The draft head has not been run. |
| 12 | Same live and replay decisions | Shared strategy code for backtest and live | The weekly book adapter is shared. Runtime integration requires feeding recorded and newly arriving frames through the same session. |
| 13 | Eastern trade day and break | Instrument and timestamp types | ROOT defines the named NG day, settlement window and message-only break. |
| 14 | Bid and ask only | Native best bid and ask | The strategy adapter and journal use sided quotes. |
| 15 | No research costs | Configurable execution and fee models | The research ledger records observations without fees or slippage. |
| 16 | One record per trade | Order and position events and reports | The draft phone list pairs individual orders. It computes no pooled score. |
| 17 | Fill pricing | Sandbox simulated exchange and fill models | Greg's policy controls activation. Unpriced orders and both quotes are supported. |
| 18 | Mark missing evidence | Custom strategy events | The engine emits missing-input reasons. The draft carries them to each affected trade. |
| 19 | GitHub Actions hosting | Application runtime deployable on a host | This repository supplies the workflows, permissions and destinations. |
| 20 | 5.75-hour stop and flatten | Clocks, timers and order management | Session and watchdog enforce a host-clock deadline and identify administrative closes. |
| 21 | Fresh run | Configurable strategy state | Each session has a fresh engine, run ID and evidence directory. |
| 22 | Greg starts each run | Node lifecycle | The workflow requires a manual dispatch, Greg's account and explicit go. |
| 23 | Stop at budgets and notify | Risk controls and events | Custom message and journal budgets stop the runner. Platform code owns phone delivery. Ccode owns agent budgets. |
| 24 | Real-time phone page | Event/message bus | The relay and phone HTML stream journal events. End-to-end delivery requires a configured host. |
| 25 | Publish events to markets-data | Persistence and events | The publisher pushes journal changes on orders and each minute. Operational delivery requires a run. |
| 26 | Phone notices and named services | Custom event subscribers | The repo names Render and Pushover. Credentials, service approval and actual delivery are separate. |
| 27 | Per-strategy trade list | Position and fill events | The draft joins entry, exit, times, both quotes and reasons. Manager and administrative exits have distinct labels. |
| 28 | Append-only provenance | Timestamped data and execution events | Our journal adds the source cursor, rule commit and research finding. |
| 29 | Run receipt | Configuration and persistence | Session records code, inputs, constants and output fingerprints. |
| 30 | Fill-to-finding trace | Client order and trade identifiers | Our order links its signal, rule and evidence. Fill linkage depends on the approved pricing policy. |
| 31 | Unique order tag | Client order IDs | Run ID plus signal sequence identifies an order. Relay ingest handles identical retries. |
| 32 | Keep credentials secret | Adapter credential configuration | Workflows use secret stores. The historical sandbox credential is outside this build at Greg's direction. |
| 33 | Write only to markets-data | Storage adapters | The writer restricts the Git remote and publishes one branch per run. |
| 34 | Preserve ROOT and its gate | External Python components | The platform imports ROOT. The four-trade-day reference gate remains alongside platform code. |
| 35 | Codex build, Claude review | Project ownership decision | Codex implementation is in PR 5. Independent Claude review belongs to Ccode and is pending. |

[Original wishlist](OWN_PLATFORM_WISHLIST_20261123.md) · [Implementation evidence](README.md) · [Platform code](../../paper_platform) · [Actions workflows](../../.github/workflows)
