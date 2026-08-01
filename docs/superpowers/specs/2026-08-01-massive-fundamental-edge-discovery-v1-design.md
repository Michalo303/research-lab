# Massive Fundamental Edge Discovery V1 Design

**Status:** Approved by the user's standing profit-first authorization

**Primary objective:** Run one bounded, point-in-time, development-only fundamental search on the existing EODHD US-equity universe and determine whether any predeclared fundamental mechanism is economically credible enough to justify portfolio construction work.

## 1. Why this milestone exists

The genuine-Qlib price/volume pilot returned `NO_PRICE_VOLUME_EDGE`. Its strongest apparent results were dominated by one 2021 instrument and one year, so that branch is stopped rather than tuned.

The next authorized question is narrower: do historically available accounting statements add a robust return signal? Infrastructure is permitted only where it is necessary to answer that question. Passing tests, deterministic artifacts, or successful downloads do not count as edge.

## 2. Provider decision

No additional data subscription is purchased for V1.

- EODHD remains the immutable price, liquidity, listing, and return source.
- Massive's paid legacy `/vX/reference/financials` endpoint is the primary fundamental source for this bounded pilot because live probes established historical annual and quarterly statements, CIK identity, filing dates, and standardized financial fields.
- The newer bulk statement endpoints are not entitled and returned HTTP 403.
- EODHD fundamentals are not entitled and returned HTTP 403.
- Sharadar SF1 is not configured locally and the previously verified entitlement is not broad enough for this universe.
- SEC EDGAR is used only for a bounded identity/date/value audit of a deterministic sample before economic results are trusted.

The provider decision is explicit, not a silent fallback. Massive observations never overwrite EODHD prices.

## 3. Immutable input and acquisition boundary

The acquisition request binds:

- the existing EODHD dataset manifest and expected SHA-256;
- the exact 3,914-instrument time-union contained in that manifest;
- filing dates from 2009-01-01 through 2022-12-31;
- annual and quarterly statements only;
- the fixed Massive legacy endpoint identity;
- a maximum of three pages and 300 provider records per requested ticker;
- a maximum of 10,000 provider request units;
- a minimum 12.5-second interval between provider calls;
- bounded timeouts, retries, and redacted failures.

Preparation performs no provider call. Execution requires an explicit command and reads `MASSIVE_API_KEY` from process environment only. Credentials may not enter requests on disk, logs, artifacts, exceptions, or source control.

Every raw response is stored as deterministic gzip JSON and hashed. SQLite records completed, empty, ambiguous, invalid, and failed ticker states so execution is resumable. A completed bundle contains the request, source-manifest identity, raw-response manifest, normalized statement manifest, coverage report, checksums, result, and `COMPLETE` marker.

## 4. Issuer identity and point-in-time rules

The requested EODHD ticker is only a lookup key, not sufficient issuer authority. Massive CIK is the issuer identity.

- A ticker with one nonempty CIK across usable results may be bound to that CIK.
- Rows without a ticker may be retained only after the page's unique CIK is established.
- Multiple CIKs for the same requested ticker are `AMBIGUOUS_ISSUER_IDENTITY`; the entire ticker is excluded rather than guessed.
- Records lacking a filing date, period end, supported timeframe, or nonempty standardized financial statement are diagnostics-only.
- Vendor TTM rows and vendor ratios are excluded.
- A filing becomes signal-eligible only on the first verified SPY session strictly after its filing date.
- Restatements become visible only from their later filing date and never rewrite earlier snapshots.
- EODHD listing intervals and verified-session membership remain authoritative for price eligibility.
- The sealed interval beginning 2023-01-01 is never read.

Before economic evaluation, a separate immutable SEC audit must pass. It selects 30 distinct CIKs deterministically, uses each issuer's latest auditable annual filing, and checks the exact SEC accession, filing date, period end, USD units, and at least three independently matching values from revenue, gross profit, operating income, net income, assets, liabilities, and operating cash flow. The official responses and hashes are retained privately. The coordinator binds both the audit file SHA-256 and its canonical audit SHA-256; a failed or modified audit stops before prices are loaded or trials are counted.

## 5. Frozen fundamental catalog

Quarterly records are used to construct trailing-four-quarter flows and latest-quarter balance-sheet values. A flow requires four distinct quarterly periods within 400 days. Year-over-year comparisons require the corresponding prior four quarters. Cross-statement components must share the same period end. Missing values remain missing; no sector median or zero imputation is allowed.

Exactly these ten trials are allowed:

1. `GROSS_PROFITABILITY`: trailing gross profit divided by latest total assets.
2. `OPERATING_RETURN_ON_CAPITAL`: trailing operating income divided by latest total assets minus current liabilities.
3. `CASH_PROFITABILITY`: trailing operating cash flow divided by latest total assets.
4. `ACCRUAL_QUALITY`: trailing operating cash flow minus trailing net income, divided by latest total assets.
5. `REVENUE_GROWTH`: trailing revenue change versus the prior trailing-four-quarter value.
6. `EARNINGS_IMPROVEMENT`: trailing net-income change versus the prior year, scaled by latest total assets.
7. `MARGIN_STABILITY`: negative standard deviation of the latest eight valid quarterly operating margins.
8. `LOW_LEVERAGE`: negative latest total liabilities divided by total assets.
9. `LOW_ASSET_GROWTH`: negative year-over-year growth in latest total assets.
10. `QUALITY_MOMENTUM`: equal-weight cross-sectional percentile average of gross profitability, operating return on capital, cash profitability, accrual quality, low leverage, and the already frozen 12-1 momentum definition.

Definitions and directions are immutable before the first economic output. Unavailable free-cash-flow fields are not approximated with total investing cash flow.
Market-cap value factors are also excluded in V1 because the snapshot does not yet bind historical share counts to independent point-in-time split lineage; mixing pre-split shares with post-split prices would create false value signals.

## 6. Economic evaluation

The evaluation interval remains 2019-01-01 through 2022-12-31. Discovery history may begin later than 2006 because standardized SEC XBRL coverage begins around 2009, but the existing partition boundary is not moved. The sealed interval remains unopened.

Each Friday/last verified session of the week:

- rank only instruments eligible in the EODHD point-in-time universe with a finite factor;
- require at least 500 ranked instruments and disclose the coverage ratio;
- hold the top 15 equal-weight instruments from the next verified session through the next rebalance;
- allow cash when fewer than 15 instruments pass;
- cap every position below 10% by construction;
- calculate turnover exactly and apply 15, 30, and 50 basis points one way;
- compare against SPY, the eligible-universe return, and cash;
- calculate net CAGR, maximum drawdown, Sharpe, Sortino, Calmar, worst year, worst rolling 12 months, turnover, holdings, exposure, and profit concentration.

No sector cap or volatility overlay is invented in this discovery milestone because historical sector lineage is not yet bound. Their absence is disclosed and a surviving candidate can authorize one separately counted portfolio-construction trial.

## 7. Continuation and stop gates

A factor can continue only if all are true on development evidence:

- median weekly coverage is at least 500 instruments;
- at least 156 weekly holding observations exist;
- base-cost net CAGR is at least 10%;
- stress-cost net CAGR is at least 8%;
- maximum drawdown is no worse than 25% at this pre-overlay stage;
- Sharpe is at least 0.75 and Calmar at least 0.50;
- base-cost return is positive in at least three of four calendar years;
- stress active return versus the eligible-universe baseline is positive;
- removal of the best year leaves positive cumulative return;
- no single instrument contributes more than 25% of positive PnL;
- no single year contributes more than 60% of positive PnL.

These are continuation thresholds, not production approval. A survivor must later produce a portfolio with at least 10% net CAGR and at most 15% drawdown under the canonical promotion gate. If none survives, status is `NO_FUNDAMENTAL_EDGE` and the stock-selection branch is stopped; gates are not weakened and factors are not tuned.

## 8. Experiment accounting and outputs

The Qlib pilot's updated ledger is the only previous ledger. Exactly ten new hypotheses and ten trials are appended, irrespective of success. Every rejected, insufficient-data, or failed factor remains counted. No provider observation occurs until ledger capacity and previous-ledger SHA validation pass.

The single scorecard returns only:

- `FUNDAMENTAL_EDGE_CANDIDATE_FOUND` with named survivors; or
- `NO_FUNDAMENTAL_EDGE`; or
- a precise fail-closed data, identity, coverage, runtime, or ledger status.

No result authorizes sealed OOS, RD-Agent, Knihomol expansion, registry promotion, shadow execution, broker access, paper trading, deployment, or live trading.

## 9. Falsification order

1. Provider and identity coverage.
2. Timestamp and SEC audit.
3. Exact factor calculation on synthetic fixtures.
4. Portfolio mechanics and cost accounting on synthetic fixtures.
5. One development-only run of the ten frozen factors.
6. Immediate stop if no factor survives.

This sequence spends provider time and compute, not language-model search credits.
