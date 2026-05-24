# Apify Actor Evaluation

Status date: 2026-05-24

The research lab already runs `parsebird/superinvestor-scraper` weekly through the deterministic weekly research cycle when `APIFY_TOKEN` is set. The actor is used only to generate smart-money hypotheses and watchlist symbols.

## Decision Rules

- Prefer official/free sources first when they provide the same raw data.
- Use paid Apify actors only when they add a distinct signal or save meaningful maintenance time.
- Every paid actor must have a row/result limit in `.env`.
- Actor output may create hypotheses, source items, and watchlists. It may not validate strategies or place orders.
- Timing, tiering, and allocation remain deterministic and backtest-driven.

## Current Choices

| Actor | Decision | Why |
|---|---|---|
| `parsebird/superinvestor-scraper` | Keep | Full Dataroma holdings are useful as smart-money universe and conviction filters. Runs weekly with `APIFY_DATAROMA_MAX_RESULTS=200`. |
| `johnvc/us-congress-financial-disclosures-and-stock-trading-data` | Pilot later | Congressional disclosure data is a distinct event/sentiment source. Use only for source discovery and post-disclosure drift hypotheses. |
| `lulzasaur/stockanalysis-scraper` | Pilot later | Broad US stock/ETF screener snapshot could help universe construction and sanity checks. It overlaps with fundamentals providers, so run monthly at most. |
| `architjn/yahoo-finance` | Defer | Overlaps with Massive, yfinance, and future fundamentals APIs. Cheap, but not a unique signal. |
| `ryanclinton/finnhub-stock-data` | Defer | Useful only if we separately choose Finnhub as a data vendor. Do not pay Apify on top of a direct API unless it saves real engineering time. |
| `young_billionaires/stock-trends-analyzer` | Defer | Interesting for narrative context, but it mixes scraped market data with AI explanations. Too noisy for validation. |
| `fortuitous_pirate/seekingalpha-stock-analysis-scraper` | Avoid for now | Expensive per article and likely noisy/opinion-heavy. Also more terms/paywall-sensitive than we need. |
| `mscraper/tradingview-stock-scraper` and similar TradingView actors | Avoid for now | Technical indicators are easy to compute ourselves from price data; scraped TradingView signals add little and can be costly. |
| `fastcrawler/stock-crypto-kol-tracker-discover-top-twitter-influencers` | Avoid for now | Social/KOL data is noisy and subscription-based. Revisit only for crypto sentiment after deterministic crypto research improves. |
| Eastmoney China actors | Avoid for now | Out of current broker/data scope unless China A-shares become a deliberate research universe. |

## Next Integration Order

1. Keep weekly Dataroma holdings import running for 4 weeks.
2. Add a monthly StockAnalysis screener pilot with a hard result limit if fundamentals/universe coverage is still weak.
3. Add a weekly or daily Congress disclosure pilot only if its schema is stable and cost is controlled.
4. Do not add Seeking Alpha, TradingView, or KOL/Twitter until the lab has stronger walk-forward and parameter-stability gates.

## Environment Guardrails

Suggested optional limits:

```bash
APIFY_DATAROMA_MAX_RESULTS=200
APIFY_STOCKANALYSIS_MAX_RESULTS=500
APIFY_CONGRESS_MAX_RESULTS=200
```

These are not execution permissions. They only bound research imports.
