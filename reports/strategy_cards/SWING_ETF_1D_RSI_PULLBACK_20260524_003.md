# Strategy Card: SWING_ETF_1D_RSI_PULLBACK_20260524_003

## Hypothesis
Buying oversold pullbacks only inside a rising long-term trend may produce positive expectancy with bounded exposure.

## Rules
Enter long when SPY is above SMA100 and RSI14 is below 35; exit when RSI14 exceeds 55 or price closes below SMA100.

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
    "total_return": -0.11395577166967974,
    "cagr": -0.018644421169627723,
    "annual_return": -0.018644421169627723,
    "annual_volatility": 0.04292522463853034,
    "sharpe": -0.4169244072693512,
    "max_drawdown": -0.12060050401111466,
    "mar": -0.15459654437189943,
    "dominant_month_profit_share": 0.33721943894306394,
    "trade_count": 16,
    "win_rate": 0.3125,
    "profit_factor": 0.598475844056528,
    "expectancy_per_trade": -0.007070140339078816,
    "max_losing_streak": 5
  },
  "validation": {
    "total_return": -0.0556697174260915,
    "cagr": -0.013276252324333826,
    "annual_return": -0.013276252324333826,
    "annual_volatility": 0.04398836505258982,
    "sharpe": -0.28178565968832475,
    "max_drawdown": -0.1049932259243832,
    "mar": -0.12644865616278395,
    "dominant_month_profit_share": 0.27053329094106826,
    "trade_count": 10,
    "win_rate": 0.3,
    "profit_factor": 0.5492702689522219,
    "expectancy_per_trade": -0.005474726220248449,
    "max_losing_streak": 3
  },
  "unseen": {
    "total_return": 0.0024230133955858157,
    "cagr": 0.0006778527753452668,
    "annual_return": 0.0006778527753452668,
    "annual_volatility": 0.025548131786063518,
    "sharpe": 0.039325907054048634,
    "max_drawdown": -0.060698869050432536,
    "mar": 0.011167469607746117,
    "dominant_month_profit_share": 0.6055095525717367,
    "trade_count": 3,
    "win_rate": 0.6666666666666666,
    "profit_factor": 1.0890267266563278,
    "expectancy_per_trade": 0.0017401383632535221,
    "max_losing_streak": 1
  }
}
```

## Drawdown
Unseen max drawdown: -6.07%.

## Robustness
Double-cost stress survives: False. Parameter stability is marked as TODO for deeper weekly runs.

## Failure Modes
Synthetic data, low trade count, unstable neighboring parameters, and cost sensitivity invalidate promotion.

## Tier Decision
Tier: C. Reason: Synthetic or non-production data source; usable for runner validation only, not capital research.

## Deployment Readiness
DEPLOYMENT_CANDIDATE: NO
REASON: Research-only lab output. Live deployment is prohibited without explicit approval and paper validation.
