# Trading Research Lab

This project is separate from the running EURUSD and crypto dry-run bots.

Purpose:

- generate new strategy candidates
- backtest them reproducibly
- punish overfitting
- rank strategies by return, drawdown, stability, and trade quality
- produce allocation suggestions by risk tier
- never place live trades

Primary file:

- `HERMES_RESEARCH_LAB_PROMPT.md`

Deterministic runner:

- `python scripts/run_daily_research.py`

The runner creates:

- data manifests in `data/manifests/`
- full backtest JSON in `backtests/runs/`
- append-only registries in `registry/experiments.jsonl` and `registry/strategy_registry.jsonl`
- ranked output in `registry/leaderboard.csv`
- model-only allocation suggestions in `registry/allocation_model.csv`
- daily reports in `reports/daily/`
- strategy cards in `reports/strategy_cards/`

Default behavior is intentionally conservative. If real market data is not enabled, the runner uses deterministic synthetic OHLCV data only as a smoke test. Synthetic results cannot become deployment candidates, and normal rejection rules still apply.

Target deployment:

- Hetzner server
- Docker/systemd scheduled jobs
- research outputs written to `reports/`, `strategies/`, `backtests/`, and `registry/`

Current rule:

- The research lab may propose strategies.
- It may not modify production bots or enable live execution.
- A strategy becomes deployable only after passing out-of-sample, walk-forward, cost, drawdown, and stability gates.

Optional real EOD data:

```bash
pip install -e ".[data]"
RESEARCH_LAB_USE_YFINANCE=1 python scripts/run_daily_research.py
```

No broker/exchange execution libraries or live keys are required.
