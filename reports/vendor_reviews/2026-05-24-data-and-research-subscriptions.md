# Data And Research Subscription Review - 2026-05-24

## Recommendation

Do not buy paid strategy-code repositories first.

Buy data access first, then let the lab generate and reject its own strategies. Paid strategy repos are usually hard to audit, easy to overfit, and often impossible to trust without clean data and reproducible validation.

## Priority 1: Low-Cost Market Data API

### Polygon / Massive

Use case:

- US equities and ETFs
- daily, minute, and second aggregate research
- flat-file historical downloads

Why it fits:

- Official pricing page lists a free tier and paid stock plans starting with broader historical access.
- Paid plans include flat-file downloads, which are useful for a server-side research lab.

Risks:

- Validate corporate actions, missing bars, and adjusted/unadjusted behavior before using results.
- Treat vendor aggregates as data input, not truth.

Sources:

- https://polygon.io/pricing
- https://polygon.io/docs/rest/quickstart
- https://polygon.io/blog/flat-files/

Initial decision:

- Best first paid experiment if the lab focuses on US ETF/equity rotation and swing systems.

## Priority 2: Futures / Tick-Grade Data Later

### Databento

Use case:

- futures
- options
- tick/trade/quote data
- deeper intraday research after EOD and minute systems prove worthwhile

Why it fits:

- Offers real-time and historical market data across futures, equities, options, and multiple venues.
- Usage-based pricing can be useful for targeted experiments.

Risks:

- Data volume and storage grow quickly.
- Tick/quote research is a bad first step unless the lab already has proven simpler edges.

Sources:

- https://databento.com/

Initial decision:

- Buy later, not first. Use when intraday hypotheses need better-than-aggregate data.

## Priority 3: Fundamentals / Alternative Data

### Nasdaq Data Link / Sharadar

Use case:

- fundamentals
- financial statement history
- longer-term equity selection
- quality/value/momentum blends

Why it fits:

- Nasdaq Data Link provides API access to free and premium datasets.
- Sharadar is listed by Nasdaq as a standardized source for US equity and fund pricing, fundamentals, insider and institutional holdings.

Risks:

- Premium pricing varies by dataset and may require logged-in quote pages or sales contact.
- Fundamental datasets are more useful for long-term and rotation systems than intraday.

Sources:

- https://docs.data.nasdaq.com/docs/getting-started
- https://www.nasdaq.com/solutions/data/nasdaq-data-link
- https://help.data.nasdaq.com/article/568-how-much-does-nasdaq-data-link-data-cost-how-do-i-find-pricing

Initial decision:

- Good second purchase if long-term/rotation becomes the main research direction.

## Priority 4: Platform Subscription

### QuantConnect

Use case:

- external validation
- independent backtest engine comparison
- access to cloud datasets and LEAN ecosystem

Why it fits:

- Has research/backtesting platform tiers and dataset access options.
- Useful as a second opinion, not as the primary source of truth.

Risks:

- Porting every strategy to another framework can slow research.
- The local lab should remain the canonical registry and report source.

Sources:

- https://www.quantconnect.com/pricing

Initial decision:

- Optional. Consider only after the local runner has real-data candidates worth cross-checking.

## Priority 5: Bulk Historical Intraday Files

### Kibot / Algoseek

Use case:

- large historical intraday files
- institutional-style historical datasets
- offline backtesting without API rate concerns

Why it fits:

- Kibot advertises long intraday histories for stocks, ETFs, futures, and forex.
- Algoseek advertises broad institutional market-data packages.

Risks:

- Bulk files require cleaning, normalization, manifests, and storage discipline.
- Higher-quality datasets can become expensive quickly.

Sources:

- https://www.kibot.com/
- https://www.kibot.com/api/
- https://www.algoseek.com/
- https://algoseek.com/financial-data/packages

Initial decision:

- Useful later for serious intraday validation; not the first spend.

## What Not To Buy Yet

- Black-box strategy repositories.
- Paid Discord/forum signal rooms.
- Any strategy bundle that does not include reproducible code, exact data assumptions, transaction cost model, and out-of-sample evidence.
- Tick/L2 datasets before the lab can exploit minute/daily data.

## Purchase Order

1. Polygon/Massive or similar low-cost US equity/ETF API.
2. Nasdaq Data Link/Sharadar if long-term and rotation research needs fundamentals.
3. Databento only for targeted futures or tick-grade experiments.
4. QuantConnect only as an external validation platform.
5. Kibot/Algoseek bulk data once storage and cleaning pipelines are ready.

