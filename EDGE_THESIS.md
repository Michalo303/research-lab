# Edge Thesis

Status date: 2026-05-24

The research lab should not optimize indicators until it can name the edge. More data can reduce uncertainty, but data itself is not an edge.

## Working Definition

An edge is a repeatable reason why a strategy may have positive expectancy after costs, slippage, taxes/frictions, and realistic drawdowns.

Every candidate must answer:

- Who is plausibly paying us?
- Why should the effect persist?
- What market regime should break it?
- What data would falsify it?
- Can it survive nearby parameters and different time windows?

## Edge Buckets

| Bucket | Use | Failure Mode |
|---|---|---|
| `behavioral_momentum` | Underreaction, trend persistence, relative strength rotation. | Crowded trend crash, regime shift, lookback overfit. |
| `behavioral_mean_reversion` | Overreaction, pullbacks in valid trends. | Catching falling assets, too few trades, hidden regime break. |
| `smart_money_flow` | 13F, insider, congress, holder changes as universe/conviction filters. | Delayed filings, stale positions, survivorship and headline bias. |
| `risk_premia_rotation` | Long-only diversified exposure where return is paid for bearing risk. | Drawdowns are the premium; not a free lunch. |
| `volatility_risk_control` | Sizing/exposure control that improves survival. | Not usually a standalone alpha source. |
| `event_sentiment` | Disclosures, news, earnings, policy shocks. | Data-mined reactions and noisy narratives. |
| `execution_microstructure` | Intraday liquidity, VWAP, spread/flow behavior. | Requires expensive data and excellent cost modeling. |
| `unclear` | Idea lacks a named edge. | Do not promote until clarified. |

## Current Default

The highest-confidence path is:

1. `behavioral_momentum` for ETF rotation.
2. `smart_money_flow` only as a universe filter for swing candidates.
3. `behavioral_mean_reversion` for pullbacks, but only inside trend filters.
4. `volatility_risk_control` as drawdown reduction, not alpha.

Avoid treating forum popularity, AI explanations, or one optimized indicator as edge.

## Buying More Data

Do not buy more data just to feel safer.

Buy only when it unlocks a specific falsification test:

- 10+ years EOD: validates whether rotation/long-term behavior survives different macro regimes.
- Fundamentals: tests whether smart-money candidates have quality/valuation support.
- Intraday/tick: tests microstructure/execution edges; expensive and lower priority.

Until then, the next improvement is better validation on the data already available.
