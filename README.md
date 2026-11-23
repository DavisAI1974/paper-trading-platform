# Paper trading platform

Replay purchased market-by-order data as a paper feed. The first supported market is NYMEX Henry Hub natural gas futures. The platform evaluates 30 versioned strategies on the recorded tape clock and shows paper orders, signals and observations through one HTML dashboard and API.

This repository holds the platform code and the ROOT and crypto calculation modules it imports. Purchased tape and run receipts live in [markets-data](https://github.com/DavisAI1974/markets-data). The strategy definitions retain links to their original findings there. GitHub Actions handles verification and Greg's manually approved paper sessions.

[Platform guide](paper_platform/README.md) · [Strategy definitions](paper_platform/rules) · [Verification workflow](.github/workflows/verify_paper_platform.yml)
