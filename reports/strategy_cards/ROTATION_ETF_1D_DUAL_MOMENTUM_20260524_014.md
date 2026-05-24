# Strategy Card: ROTATION_ETF_1D_DUAL_MOMENTUM_20260524_014

## Hypothesis
Monthly top-N momentum rotation across equity, bond, gold, and growth ETFs may improve risk-adjusted return.

## Rules
At month end rank by 126-day momentum and hold the top two assets equally for the next month.

## Asset Universe
ETF

## Data
Source: synthetic; range: 2012-01-02 00:00:00 to 2025-10-17 00:00:00; rows: 3600.

## Costs
Normal cost: 5.0 bps; stress cost: 10.0 bps.

## Results
```json
{
  "train": {
    "total_return": -0.2371405271218825,
    "cagr": -0.04123185620347747,
    "annual_return": -0.04123185620347747,
    "annual_volatility": 0.11755430957266119,
    "sharpe": -0.2993510895547585,
    "max_drawdown": -0.4836026573567114,
    "mar": -0.08525978006168054,
    "dominant_month_profit_share": 0.09391678034303519,
    "trade_count": 1,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "expectancy_per_trade": -0.2371405271218825,
    "max_losing_streak": 1
  },
  "validation": {
    "total_return": 0.21873994864743485,
    "cagr": 0.04723924992485995,
    "annual_return": 0.04723924992485995,
    "annual_volatility": 0.11925046678668506,
    "sharpe": 0.44668564759272156,
    "max_drawdown": -0.2987457240504554,
    "mar": 0.15812527551651812,
    "dominant_month_profit_share": 0.10061739252440728,
    "trade_count": 1,
    "win_rate": 1.0,
    "profit_factor": Infinity,
    "expectancy_per_trade": 0.21873994864743485,
    "max_losing_streak": 0
  },
  "unseen": {
    "total_return": -0.3072085275245241,
    "cagr": -0.09766311735846678,
    "annual_return": -0.09766311735846678,
    "annual_volatility": 0.12968191131817167,
    "sharpe": -0.7274352374424171,
    "max_drawdown": -0.4837502995255094,
    "mar": -0.2018874560992737,
    "dominant_month_profit_share": 0.1355191444372705,
    "trade_count": 1,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "expectancy_per_trade": -0.3072085275245241,
    "max_losing_streak": 1
  }
}
```

## Drawdown
Unseen max drawdown: -48.38%.

## Robustness
Double-cost stress survives: False. Parameter stability is marked as TODO for deeper weekly runs.

## Failure Modes
Synthetic data, low trade count, unstable neighboring parameters, and cost sensitivity invalidate promotion.

## Tier Decision
Tier: Rejected. Reason: Negative unseen result.

## Deployment Readiness
DEPLOYMENT_CANDIDATE: NO
REASON: Research-only lab output. Live deployment is prohibited without explicit approval and paper validation.
