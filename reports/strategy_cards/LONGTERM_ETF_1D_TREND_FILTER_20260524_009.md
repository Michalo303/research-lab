# Strategy Card: LONGTERM_ETF_1D_TREND_FILTER_20260524_009

## Hypothesis
A long-only equity allocation with a 200-day trend filter should reduce drawdown versus always-on exposure.

## Rules
Hold SPY when close is above its 200-day SMA; otherwise hold cash.

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
    "total_return": 0.6529950580230761,
    "cagr": 0.08131780219656704,
    "annual_return": 0.08131780219656704,
    "annual_volatility": 0.12986615804138993,
    "sharpe": 0.6670442356326262,
    "max_drawdown": -0.17239310090525017,
    "mar": 0.4716998636810908,
    "dominant_month_profit_share": 0.07403921164963517,
    "trade_count": 22,
    "win_rate": 0.36363636363636365,
    "profit_factor": 4.885803725350214,
    "expectancy_per_trade": 0.025746749185250696,
    "max_losing_streak": 5
  },
  "validation": {
    "total_return": 0.16645038481367824,
    "cagr": 0.03657834026777995,
    "annual_return": 0.03657834026777995,
    "annual_volatility": 0.11306222086642145,
    "sharpe": 0.3742490172165447,
    "max_drawdown": -0.20120547428855384,
    "mar": 0.1817959496237266,
    "dominant_month_profit_share": 0.1715150594844499,
    "trade_count": 15,
    "win_rate": 0.2,
    "profit_factor": 1.9687600620060213,
    "expectancy_per_trade": 0.012819778162340412,
    "max_losing_streak": 8
  },
  "unseen": {
    "total_return": -0.06491280216876172,
    "cagr": -0.018616863335593647,
    "annual_return": -0.018616863335593647,
    "annual_volatility": 0.09149199956783975,
    "sharpe": -0.15967641924947756,
    "max_drawdown": -0.2313008478818357,
    "mar": -0.08048765711876861,
    "dominant_month_profit_share": 0.25240100121117504,
    "trade_count": 19,
    "win_rate": 0.15789473684210525,
    "profit_factor": 0.7171860331151619,
    "expectancy_per_trade": -0.0031025914935187866,
    "max_losing_streak": 13
  }
}
```

## Drawdown
Unseen max drawdown: -23.13%.

## Robustness
Double-cost stress survives: False. Parameter stability is marked as TODO for deeper weekly runs.

## Failure Modes
Synthetic data, low trade count, unstable neighboring parameters, and cost sensitivity invalidate promotion.

## Tier Decision
Tier: Rejected. Reason: Negative unseen result.

## Deployment Readiness
DEPLOYMENT_CANDIDATE: NO
REASON: Research-only lab output. Live deployment is prohibited without explicit approval and paper validation.
