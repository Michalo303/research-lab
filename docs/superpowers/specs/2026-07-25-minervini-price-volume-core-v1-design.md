# Minervini Price/Volume Core V1 Design

## Goal

Build and evaluate one deterministic, long-only US-stock system derived from
Mark Minervini's Stage 2, Trend Template, volatility-contraction, pivot-entry,
and risk-control rules. The research target is at least 10% annualized
out-of-sample return with no more than 15% portfolio drawdown.

V1 tests the price-and-volume core only. It must not claim to be the complete
SEPA method because timestamp-safe historical earnings, sales, estimates, and
release dates are not yet available.

## Source basis

- Mark Minervini, *Trade Like a Stock Market Wizard*, especially chapters 5,
  10, 12, and 13.
- Mark Minervini, *Think & Trade Like a Champion*.
- The user-supplied `minervi.docx`, which summarizes the Trend Template, VCP,
  pivot, stop, position-sizing, and exit rules.

The source books are evidence for the trading concepts. All numerical
mechanization choices below are explicitly preregistered V1 interpretations,
not attributed verbatim to the author.

## Required data

The evaluation input is an immutable local daily OHLCV snapshot for US common
stocks plus SPY as the market proxy. It must provide:

- adjusted and unadjusted price identity or explicit corporate-action lineage;
- volume;
- symbol identity and exchange;
- delisting status when available;
- an explicit point-in-time universe classification;
- source hash and observation range.

The preferred source is the existing EODHD subscription. A read-only
capability check must establish whether it supplies the required US universe,
daily history, corporate actions, and delisted symbols. No new paid source is
authorized by this design.

If the available universe is current-membership-only or omits delisted stocks,
the result must carry `SURVIVORSHIP_BIAS_PRESENT` and cannot establish edge.

## Eligibility at signal close

A symbol is eligible only when all conditions are known by that close:

- US common stock, not ETF, fund, preferred share, warrant, or OTC instrument;
- close at least USD 5;
- 20-session mean dollar volume at least USD 10 million;
- at least 252 valid prior sessions;
- close above SMA50, SMA150, and SMA200;
- SMA50 above SMA150 and SMA200;
- SMA150 above SMA200;
- SMA200 above its value 20 sessions earlier;
- close at least 30% above the trailing 252-session low;
- close no more than 25% below the trailing 252-session high;
- trailing 252-session return at or above the cross-sectional 80th percentile
  among eligible symbols.

Missing values make the symbol ineligible. They are never forward-filled
across listing, suspension, or delisting boundaries.

## Mechanical VCP approximation

V1 searches for a consolidation lasting 40 to 100 sessions within the eligible
Stage 2 trend.

- Divide the most recent 60 sessions into three consecutive 20-session blocks.
- For each block, compute `(highest high / lowest low) - 1`.
- The three ranges must contract strictly.
- The final range must be no more than 60% of the first range.
- Mean volume over the final 10 sessions must be no more than 70% of mean
  volume over the final 50 sessions.
- The pivot is the highest high of the 20 sessions preceding the signal close.
- A breakout signal requires close above the prior pivot and signal-day volume
  at least 1.5 times the prior 50-session mean.

All rolling values exclude information after the signal close.

## Entry and initial risk

- Enter at the next session open.
- Skip the trade when the open is more than 2% above the pivot.
- The structural stop is immediately below the lowest low of the final
  10-session contraction.
- Reject the setup when entry-to-stop distance is less than 2 ATR20 or more
  than 7% of entry.
- Risk budget per trade is 0.5% of current portfolio equity.
- Position notional is the lesser of risk-budget sizing and 12.5% of equity.
- Maximum concurrent positions is eight.
- Gross exposure cannot exceed 100%; leverage and shorting are prohibited.
- Orders that cannot satisfy price, liquidity, cash, and risk bounds are
  skipped rather than resized beyond the declared limits.

## Position management and exits

- A stop touched intraday exits at the worse of stop price and available open,
  with costs and slippage.
- After close reaches +2R from entry, the protective stop moves to at least
  break-even for the following session.
- After +2R, a close below SMA50 exits at the next session open.
- No averaging down, discretionary override, same-bar re-entry, or use of
  future extrema is allowed.
- Delisting and missing-price exits fail closed using declared terminal-value
  evidence; an absent terminal value is a blocking data-quality failure.

## Portfolio accounting

The evaluator must maintain one chronological portfolio ledger, not combine
independent per-symbol equity curves. It includes cash, open positions,
realized and unrealized P&L, turnover, costs, slippage, exposure, and corporate
actions.

Base execution cost is 15 basis points per side. A result that passes the
primary gate is subsequently stress-checked at 30 basis points per side; this
stress check does not authorize parameter selection.

## Evaluation protocol

The specification is frozen before inspecting performance.

- Use chronological evaluation with no randomized split.
- Report the exact data interval and point-in-time classification.
- Report CAGR, cumulative return, maximum drawdown, MAR, trade count, win rate,
  average win/loss, exposure, turnover, and cost drag.
- Preserve immutable input, strategy, and result hashes.
- Do not search parameter combinations after viewing the result.

The primary verdict is `CANDIDATE` only when all of these hold:

- annualized OOS return at least 10%;
- portfolio maximum drawdown no worse than -15%;
- at least 100 completed OOS trades;
- no look-ahead, corporate-action, identity, or survivorship blocker.

Otherwise the verdict is `FAIL` or `INSUFFICIENT_EVIDENCE`. A `CANDIDATE`
verdict is not proof of edge and does not authorize paper or live trading.

## Safety boundaries

V1 is local, read-only, and research-only. It performs no broker action,
registry write, promotion, deployment, paper trading, or live trading. Provider
access is permitted only as a separately explicit, bounded, read-only data
acquisition step after the EODHD capability check.

## Validation

Implementation tests must cover no-look-ahead rolling features, eligibility,
strict contraction, breakout timing, gap rejection, ATR/7% stop bounds,
risk-based sizing, portfolio exposure, stop and +2R transitions, delisting
handling, transaction costs, deterministic replay, and all fail-closed data
conditions.
