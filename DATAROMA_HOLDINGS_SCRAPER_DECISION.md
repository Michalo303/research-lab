# Dataroma Holdings Scraper Decision

We do not yet have a full Dataroma holdings scraper equivalent to the Apify actor.

## What We Have

Local `smartmoney` currently supports:

- Dataroma quarterly activity page parsing
- Dataroma grid page parsing
- investor whitelist scoring
- fundamentals enrichment
- top candidates / watchlist reports
- import into research-lab as swing hypotheses

This is enough for a smart-money shortlist, but not enough for full portfolio reconstruction.

## What The Apify Actor Adds

The described `parsebird/dataroma-superinvestor-scraper` extracts one row per holding across Dataroma superinvestor portfolios:

- superinvestor name and ID
- portfolio value
- portfolio date / period
- stock ticker and name
- percent of portfolio
- recent activity
- shares
- reported price
- current price
- change from reported price
- 52-week high/low
- sector breakdown

This is more complete than our current `smartmoney` parser.

## Build vs Buy

### Build Ourselves

Pros:

- no vendor dependency
- full control over output schema
- can keep request rate very conservative
- can align directly with our whitelist

Cons:

- Dataroma HTML can change
- must parse portfolio pages and sector tables
- needs tests and failure handling
- still relies on public website scraping

### Use Apify Actor

Pros:

- already built for full holdings
- cheap for occasional quarterly runs
- can scrape all 82 investors quickly
- returns structured dataset

Cons:

- pay-per-event
- external dependency
- still scraping Dataroma through a third party
- must review Dataroma terms and Apify actor behavior

## Recommendation

For the research lab:

1. Keep SEC EDGAR as the official unattended watcher.
2. Keep `smartmoney` as curated scoring.
3. Add our own minimal Dataroma holdings parser only for whitelisted managers.
4. Use Apify only if we want quick quarterly full-universe snapshots and accept the scraping dependency.

Implementation note:

```bash
python scripts/run_apify_dataroma_import.py --superinvestors BRK,HC,BAUPOST --max-results 100
```

Default script limits are deliberately small to avoid accidental spend.

Estimated Apify cost from the actor description:

```text
2,000-4,000 holdings * $2 / 1,000 = about $4-$8 per full all-investor scrape
```

That is cheap enough for quarterly research snapshots, but not necessary for hourly monitoring.

## Research Use

Full holdings help swing trading mainly as a universe and conviction filter:

- high portfolio weight
- new/add activity
- multiple respected holders
- current price below reported price
- sector concentration context

They do not provide timing. Timing still comes from deterministic price/volatility rules and backtests.
