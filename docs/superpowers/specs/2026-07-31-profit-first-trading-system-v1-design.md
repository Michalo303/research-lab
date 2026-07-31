# Profit-First Trading System V1 Design

**Status:** Approved master design

**Primary objective:** Discover, falsify, validate, and eventually operate a deterministic long-only US equity system targeting 10-20% net CAGR with maximum drawdown no worse than 15%, without weakening the repository's stricter canonical promotion policy.

**First implementation milestone:** `REAL_QLIB_EODHD_EDGE_DISCOVERY_PILOT_V1`

## 1. Decision hierarchy

Work is prioritized in this order:

1. net economic edge after realistic costs;
2. capital preservation and drawdown containment;
3. out-of-sample and regime robustness;
4. point-in-time data validity;
5. deterministic execution reliability;
6. automation;
7. supporting infrastructure.

A passing code suite, contract audit, replay, or orchestration run does not establish economic edge. Infrastructure work may proceed only when it is necessary to evaluate or operate a candidate that has reached the corresponding economic gate.

## 2. Existing policy remains authoritative

The implementation must reuse `research_lab/research/research_objective_promotion_gate_v1.py` without relaxing its thresholds.

The current canonical scopes are:

- `PRIMARY_PORTFOLIO`: at least 15% net CAGR, at most 12% max drawdown, Sharpe at least 1.20, Sortino at least 1.70, Calmar at least 1.20, DSR confidence at least 0.95, and PBO at most 0.20;
- `MINIMUM_VIABLE_PORTFOLIO`: at least 10% net CAGR, at most 15% max drawdown, Sharpe at least 0.90, Calmar at least 0.70, DSR confidence at least 0.90, and PBO at most 0.25;
- `STANDALONE_STRATEGY`: a continuation gate, not authorization for live deployment;
- `PORTFOLIO_CONTRIBUTION`: admission only when a component adds measurable return, risk reduction, or regime value without breaching concentration, liquidity, turnover, or drawdown vetoes.

Look-ahead, survivorship bias, point-in-time violations, sealed-OOS contamination, excessive drawdown, PBO/DSR failures, cost fragility, parameter spikes, profit concentration, insufficient sample size, invalid lineage, and unbounded trial counts remain hard vetoes.

## 3. Non-goals

V1 does not attempt to:

- reproduce Renaissance Technologies or EquiLibre performance;
- deploy reinforcement learning, transformers, or deep neural networks;
- allow an LLM to make live trading decisions;
- rescue Minervini or another named strategy through additional fidelity work;
- run an unbounded factor or hyperparameter search;
- open sealed OOS to improve an unsuccessful development result;
- build production broker, dashboard, or deployment features before edge validation;
- buy additional books or data products without a gate-specific need.

## 4. Initial strategy shape

The initial candidate is a daily-signal, weekly-rebalanced, long-only US common-stock portfolio.

The point-in-time universe must:

- contain active and delisted US common stocks;
- exclude OTC securities, funds, and instruments without reliable corporate-action lineage;
- require a price of at least USD 5;
- require at least 252 prior trading days;
- require trailing point-in-time median dollar volume of at least USD 10 million;
- retain at most the 1,500 most liquid eligible instruments;
- avoid present-day index membership as a historical universe definition.

The candidate portfolio must:

- hold 10-20 instruments;
- execute a signal created on day T no earlier than the next eligible session;
- allow cash;
- use no leverage and no short positions in V1;
- cap a single position at 10%;
- cap a sector at 25%;
- cap participation at 1% of trailing average daily volume;
- target approximately 10-12% annualized volatility;
- use deterministic turnover and liquidity limits.

## 5. Data responsibilities

### 5.1 EODHD

EODHD is the primary OHLCV, dividend, split, ETF benchmark, and active/delisted price source. Raw or normalized inputs used in a trial must be immutable and content-hashed. API keys must be read from environment variables and must never appear in URLs stored in logs, artifacts, errors, or source control.

### 5.2 Sharadar

Sharadar is the preferred point-in-time fundamentals and issuer-identity source. The currently verified free entitlement is sufficient only for adapter and temporal-join tests on the Dow 30 sample; it is not a valid profitability universe.

When full access is activated, the implementation must verify entitlement and coverage before acquisition. Fundamental values may enter a signal only according to their availability or filing date, with a minimum one-session lag. As-reported dimensions are preferred for historical research. Restated values may be retained for diagnostics but must not replace what was knowable at the decision time.

### 5.3 Massive/Polygon

The repository already has paid, working Massive/Polygon access and a timestamp-aware fundamentals parser. Massive is a secondary validation source for selected filings, prices, corporate actions, and later execution-cost diagnostics. It is not a silent fallback and may not overwrite EODHD or Sharadar observations. Cross-provider disagreement must produce a review artifact.

### 5.4 Free secondary sources

AQR factor datasets provide external factor benchmarks. SEC EDGAR provides filing-date and selected-value checks. FRED/ALFRED and ECB may be added only in a later, separately gated macro-regime milestone.

## 6. Temporal partitions and experiment accounting

Before the first economic trial, an immutable experiment manifest must freeze exact discovery, development, and sealed-OOS intervals. The intended initial split is:

- discovery/training: 2006-01-01 through 2018-12-31;
- development walk-forward: 2019-01-01 through 2022-12-31;
- sealed OOS: 2023-01-01 through the final complete month present in the frozen snapshot.

The dates may change only before the first trial if verified coverage requires it. Once the first material trial is recorded, changing a partition creates a new research program and consumes a new sealed-OOS policy decision.

Every material change to factor definition, label, model, universe, portfolio construction, regime rule, or parameter selected after observing a result counts as a trial. The existing global experiment ledger is authoritative.

Hard budgets before sealed OOS are:

- no more than 20 price/volume trials;
- no more than 10 fundamental trials;
- no more than 10 model or portfolio-construction trials;
- no more than 40 human-designed material trials in total;
- no more than 10 later RD-Agent proposals, allowed only after a credible development candidate exists.

Failed and duplicate trials remain recorded. A hard budget breach returns `UNBOUNDED_EXPERIMENT_COUNT` and blocks sealed OOS.

## 7. Economic baselines and execution assumptions

Every candidate must be compared against:

- SPY total return;
- an equal-weight eligible-universe baseline;
- a deterministic risk-controlled SPY baseline;
- cash return when the strategy is uninvested.

Research execution uses the next eligible session, never the same close that generated the signal. The initial one-way cost assumptions are:

- base: 15 basis points;
- stress: 30 basis points;
- severe stress: 50 basis points.

The first Qlib milestone must reproduce a small independent reference backtest within frozen tolerances before Qlib metrics are trusted.

## 8. Factor discovery sequence

Factors are tested individually before combination. The first bounded price/volume families are:

- medium-term momentum: 12-1, 6-1, and 3-1 variants within the shared trial budget;
- market- and sector-relative strength;
- trend stability and distance from long moving averages;
- proximity to a 52-week high and volume-confirmed breakouts;
- realized, downside, and residual volatility;
- drawdown and volatility contraction;
- short-horizon reversal;
- liquidity, gaps, and tradability penalties.

A factor continues only if it has a consistent economic direction in most walk-forward windows, survives the stress-cost scenario, is not dominated by one year/sector/instrument, and adds information not already represented by a retained factor. RankIC near or above 0.015 is a useful target, not a substitute for net economic spread and stability.

After full Sharadar access is verified, the bounded fundamental families are:

- gross profitability;
- ROIC and ROE;
- free-cash-flow yield;
- earnings and revenue growth;
- margin stability;
- leverage and debt burden;
- accruals;
- value;
- a bounded quality composite;
- profitability combined with momentum.

Price/volume, fundamentals-only, and combined variants must be compared under the same universe, dates, labels, execution, and costs. Fundamentals remain only if they add measurable portfolio value.

## 9. Model and portfolio sequence

Only two model classes are allowed in the first program:

1. transparent weighted factor ranking;
2. strongly constrained LightGBM.

The program does not use automated hyperparameter search. Portfolio sizes are limited to 10, 15, and 20 instruments. Weighting is limited to bounded score weighting and inverse-volatility weighting. Each observed-choice change counts as a trial.

A standalone development candidate must first pass the repository's existing standalone continuation gate. A combined portfolio may proceed to sealed OOS only after passing at least `MINIMUM_VIABLE_PORTFOLIO` on its frozen development evidence. `PRIMARY_PORTFOLIO` is the preferred production target.

## 10. Risk overlay

Risk controls may use market trend, volatility regime, breadth, portfolio drawdown, sector concentration, position correlation, and exposure. Output exposure states are limited to 100%, 75%, 50%, 25%, or 0%.

The overlay is evaluated by ablation. It is retained only if it produces an accepted portfolio contribution under the existing canonical gate. It may not be described as edge merely because it reduces gross exposure.

## 11. Robustness and sealed OOS

The final development candidate must pass rolling walk-forward analysis, DSR, PBO, parameter-neighborhood stability, factor ablation, doubled and tripled costs, start-date perturbation, universe-size perturbation, removal of the best year and best instruments, and bull/bear/sideways/high-volatility regime diagnostics.

The candidate is frozen before sealed OOS: code SHA, data hashes, universe rules, factor definitions, model, parameters, portfolio rules, risk rules, costs, and trial accounting.

Sealed OOS is opened once. Failure does not authorize repair against sealed results. `MINIMUM_VIABLE_PORTFOLIO_GATE_PASS` is the lowest state that can proceed to shadow review; live scale beyond the initial bounded pilot requires the stricter applicable promotion and human-approval gates.

## 12. Bounded use of existing agents and orchestration

Knihomol may explain an already observed mechanism, identify documented failure modes, or propose one precise falsification. Every accepted evidence claim requires a book identity, page, and source excerpt. Knihomol does not create an unbounded candidate list.

RD-Agent remains disabled until a genuine Qlib development candidate exists. If enabled, it uses `fin_factor` only, fixed data/model/portfolio inputs, no sealed OOS, at most ten proposals, a fixed USD 20-30 model budget, global-ledger accounting, and no automatic merge.

The existing orchestrator, immutable bundle, verifier, and replay are applied after candidate freeze. They protect a validated candidate; they are not credited with discovering edge.

## 13. Shadow, paper, and live sequence

The frozen candidate first runs on Hetzner for at least 60 trading days and at least 100 order intents. Shadow validates timestamps, real next-session prices, modeled versus observed slippage, missing data, idempotency, daily risk state, and deterministic replay. At least 30 consecutive days must complete without a critical operational failure.

IBKR paper trading follows to validate broker lifecycle behavior, not profitability. It requires zero duplicate or unauthorized orders, daily reconciliation, correct partial-fill/reject handling, and deterministic restart behavior.

Live capital scales only through:

1. EUR 5,000;
2. EUR 10,000;
3. EUR 15,000;
4. EUR 25,000.

At EUR 5,000, 4% drawdown halves exposure, 6% blocks new entries, and 8% stops the bot pending review. Scaling requires at least six months or 100 real fills, positive net results, no critical incident, and observed costs consistent with the stress model. At EUR 25,000, 6% drawdown halves exposure, 8% blocks new positions, 10% stops the bot, and 15% remains the absolute boundary that normal controls are designed to avoid.

No live model self-modification is permitted.

## 14. Failure routing

- If bounded price/volume discovery fails, run the bounded Sharadar fundamental branch.
- If fundamentals add no value, remove them rather than optimize them indefinitely.
- If the combined stock system fails, permit one separately specified diversified ETF trend-following fallback.
- If the fallback fails its gates, do not deploy a bot. Keep capital outside the experiment.

No failure route weakens a gate, expands sealed-OOS access, or authorizes an unbounded search.

## 15. Single economic scorecard

Every candidate produces one comparable scorecard containing net CAGR, maximum drawdown, Calmar, Sharpe, Sortino, worst year, worst rolling 12 months, exposure, turnover, trade count, base/stress/severe costs, each walk-forward result, DSR, PBO, PnL concentration, SPY comparison, risk-controlled-SPY comparison, and a deterministic PASS/STOP reason.

This scorecard is the primary program output. Other artifacts may explain it but may not override it.

## 16. First implementation milestone boundary

`REAL_QLIB_EODHD_EDGE_DISCOVERY_PILOT_V1` contains only:

- an isolated, pinned real-Qlib environment;
- an immutable, bounded EODHD US price/volume input contract;
- active/delisted point-in-time universe construction from approved local snapshots;
- Qlib conversion and independent reference-backtest parity;
- frozen benchmark and transaction-cost assumptions;
- a bounded first price/volume factor screen;
- global experiment-ledger binding;
- the single economic scorecard;
- final `EDGE_CANDIDATE_FOUND`, `NO_PRICE_VOLUME_EDGE`, or fail-closed data/evaluator status.

It does not contain provider acquisition without a separate approved request, Sharadar fundamental optimization, RD-Agent, Knihomol extraction, RL, broker calls, registry promotion, deployment, or a dashboard.
