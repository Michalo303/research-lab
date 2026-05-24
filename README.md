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
- `HERMES_ROLE_CONTRACT.md`
- `SEC_13F_WATCHER_DESIGN.md`
- `DATAROMA_HOLDINGS_SCRAPER_DECISION.md`
- `IBKR_PAPER_RUNBOOK.md`

Deterministic runner:

- `python scripts/run_daily_research.py`
- `python scripts/run_hourly_research.py`
- `python scripts/run_self_improvement.py`
- `python scripts/run_research_orchestrator.py`

The runner creates:

- data manifests in `data/manifests/`
- full backtest JSON in `backtests/runs/`
- append-only registries in `registry/experiments.jsonl` and `registry/strategy_registry.jsonl`
- source discoveries in `registry/source_items.jsonl`
- queued ideas in `registry/hypothesis_queue.jsonl`
- ranked output in `registry/leaderboard.csv`
- model-only allocation suggestions in `registry/allocation_model.csv`
- daily reports in `reports/daily/`
- strategy cards in `reports/strategy_cards/`
- source scan reports in `reports/source_scans/`
- self-improvement reports in `reports/self_improvement/`

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

IBKR paper scaffolding:

```bash
python scripts/check_ibkr_paper_config.py
```

This validates local paper execution configuration only. It does not connect to IBKR or place orders.

Hermes/LLM hypothesis prompt:

```bash
python scripts/write_hermes_hypothesis_prompt.py
```

Hermes is allowed to generate hypotheses and read research. It is not allowed to validate, tier, allocate, or execute.

Smartmoney shortlist import:

```bash
python scripts/import_smartmoney_candidates.py --smartmoney-path C:\Users\lojka\trading\smartmoney
```

The import treats 13F/Dataroma activity as a swing-trading universe filter only. Entries and exits must still come from price/volatility rules and deterministic backtests.

Apify Dataroma holdings import:

```bash
python scripts/run_apify_dataroma_import.py --superinvestors BRK,HC,BAUPOST --max-results 100
```

This is intentionally limited by default. It imports holdings as hypotheses only; it does not validate or trade.

Massive/Polygon Stocks Starter data:

```bash
RESEARCH_LAB_DATA_PROVIDER=massive
MASSIVE_API_KEY=your_key_here
MASSIVE_BASE_URL=https://api.massive.com
MASSIVE_START_DATE=2021-05-24
python scripts/run_daily_research.py
```

The API key belongs only in `.env`, never in git. Starter's 5-year history is useful for data pipeline validation and swing/rotation experiments, but long-term/rotation strategies remain capped below promotion until they have at least 10 years of EOD evidence.

Optional source scanning:

```bash
RESEARCH_LAB_NETWORK=1 python scripts/run_hourly_research.py
```

Network scanning is off by default. The configured source scanner uses public arXiv/RSS-style sources and treats forums as watchlists unless a compliant adapter is added later.

Hetzner 24/7 install helper:

```bash
cd /opt/trading/research-lab
bash deploy/install_systemd_timers.sh
```
