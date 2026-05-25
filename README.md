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
- `APIFY_ACTOR_EVALUATION.md`
- `EDGE_THESIS.md`
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
- edge classification audit in `registry/edge_audit.csv`
- daily reports in `reports/daily/`
- strategy cards in `reports/strategy_cards/`
- source scan reports in `reports/source_scans/`
- self-improvement reports in `reports/self_improvement/`
- weekly robustness, stability, parameter-neighborhood, and portfolio candidate CSVs in `reports/weekly/`

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
If `APIFY_TOKEN` is present in `/opt/trading/research-lab/.env`, the weekly deep research timer also runs a limited Apify Dataroma import. Override the weekly limit with `APIFY_DATAROMA_MAX_RESULTS=200`.

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

Weekly validation:

```bash
python scripts/run_weekly_deep_research.py
```

The weekly job runs limited paid Apify Dataroma ingestion when configured, then writes robustness, stability, bounded parameter-neighborhood, and portfolio candidate CSVs. These are conservative research gates only; they cannot authorize deployment.

## Sentiment / Attention Layer (Research-Only)

The sentiment layer is an observability and ranking aid only. It is **not** a trading signal and never grants deployment permission.

Design goals in phase 1:

- provider-neutral sentiment snapshot schema
- deterministic narrative/catalyst tagging (no LLM dependency)
- attention acceleration metrics and coverage states (`available`/`partial`/`missing`/`stale`/`error`)
- price-confirmed sentiment classification for research narratives only
- safe-to-fail adapters and read-only reporting hooks

Run file-based pilot:

```bash
python scripts/run_sentiment_pilot.py --provider file --input tests/fixtures/sentiment_sample.jsonl --write
```

Run Apify scaffold (no scraping unless env is explicitly configured):

```bash
APIFY_TOKEN=... APIFY_SENTIMENT_ACTOR_ID=... python scripts/run_sentiment_pilot.py --provider apify --write
```

Outputs:

- `registry/sentiment_snapshot.csv`
- `registry/sentiment_candidates.csv`
- `reports/weekly/<stem>_sentiment_candidates.csv`

Weekly report includes a read-only `Sentiment / Attention` section. If sentiment files are missing, it writes `sentiment layer not available` and continues without failing the weekly run.

### Current implementation status

Implemented now (phase-1 scaffold):

- provider-neutral snapshot schema and CSV writers
- file/mock input adapter
- deterministic narrative/catalyst tagging
- deterministic lexicon sentiment scoring
- attention metrics scaffold
- price-confirmed sentiment classifier
- weekly read-only sentiment availability hook
- controlled Apify scaffold (token/actor checks, safe-to-fail)

Not implemented yet:

- real Apify scraping and actor payload normalization
- concrete Reddit/Stocktwits/News actor selection
- production-grade cost accounting from real actor runs
- real provider coverage report from fetched data
- live sentiment feed for IREN/CRWV/NBIS pilot universe

Safety remains unchanged:

- research-only
- no trading signal permission
- no paper/live changes
- no broker integrations
- no deployment-gate changes

## Apify Sentiment Sources Pilot

The Apify sentiment pilot is fixture-first. The first implementation normalizes stored raw payload samples for:

- Reddit: `logiover/reddit-search-scraper`
- Stocktwits primary: `saswave/stocktwits-stock-ticker-news-scraper`
- Stocktwits fallback: `shahidirfan/stocktwits-sentiment-scraper`
- News: `vnx0/google-news-actor` with fallback status because the actor may be under maintenance

Fixture mode never performs a live Apify call:

```bash
python scripts/run_sentiment_pilot.py --provider apify --source reddit --fixture tests/fixtures/apify_reddit_raw.json --tickers IREN,WULF --max-items 50 --write
```

Live Apify is opt-in only and remains bounded:

```bash
APIFY_TOKEN=your_token_here
APIFY_REDDIT_ACTOR_ID=logiover/reddit-search-scraper
python scripts/run_sentiment_pilot.py --provider apify --source reddit --tickers IREN,CRWV,NBIS,WULF,VRT,CEG,OKLO,SMR --max-items 25 --max-cost-usd 1 --live-apify --write
```

Without `--live-apify`, the CLI either uses a fixture or returns controlled missing coverage. Raw payload samples are saved under `registry/sentiment_raw_samples/`, normalized outputs are written to `registry/sentiment_snapshot.csv`, `registry/sentiment_candidates.csv`, and source coverage is written to `registry/sentiment_source_coverage.csv`.
