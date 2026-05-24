# Strategy Card: INTRADAY_BTCUSDT_15M_VWAP_RSI_RECLAIM_20260524_012

## Hypothesis
A VWAP reclaim after weak RSI can capture short intraday continuation if fills survive realistic costs.

## Rules
Enter when close reclaims session VWAP and RSI14 crosses above 50 after sub-45 weakness; exit on VWAP loss or session end.

## Asset Universe
BTCUSDT

## Data
Source: synthetic; range: 2024-01-02 09:30:00 to 2024-12-30 15:45:00; rows: 6760.

## Costs
Normal cost: 8.0 bps; stress cost: 16.0 bps.

## Results
```json
{
  "train": {
    "total_return": -0.10528004019092119,
    "cagr": -0.21306021574537748,
    "annual_return": -0.21306021574537748,
    "annual_volatility": 0.013522239997917152,
    "sharpe": -17.712131447495675,
    "max_drawdown": -0.10528004019092119,
    "mar": -2.0237474772901036,
    "dominant_month_profit_share": 0.0,
    "trade_count": 70,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "expectancy_per_trade": -0.0015879405714286207,
    "max_losing_streak": 70
  },
  "validation": {
    "total_return": -0.07321198326038503,
    "cagr": -0.21779367406907746,
    "annual_return": -0.21779367406907746,
    "annual_volatility": 0.013683169612070185,
    "sharpe": -17.944560251731982,
    "max_drawdown": -0.07246995922776722,
    "mar": -3.005295937652863,
    "dominant_month_profit_share": 0.0,
    "trade_count": 47,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "expectancy_per_trade": -0.0015993600000000496,
    "max_losing_streak": 47
  },
  "unseen": {
    "total_return": -0.06201902590415331,
    "cagr": -0.2198135731843749,
    "annual_return": -0.2198135731843749,
    "annual_volatility": 0.013751439366877243,
    "sharpe": -18.043425877346696,
    "max_drawdown": -0.06201902590415331,
    "mar": -3.5442925776371204,
    "dominant_month_profit_share": 0.0,
    "trade_count": 40,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "expectancy_per_trade": -0.0015993600000000496,
    "max_losing_streak": 40
  }
}
```

## Drawdown
Unseen max drawdown: -6.20%.

## Robustness
Double-cost stress survives: False. Parameter stability is marked as TODO for deeper weekly runs.

## Failure Modes
Synthetic data, low trade count, unstable neighboring parameters, and cost sensitivity invalidate promotion.

## Tier Decision
Tier: Rejected. Reason: Negative unseen result.

## Deployment Readiness
DEPLOYMENT_CANDIDATE: NO
REASON: Research-only lab output. Live deployment is prohibited without explicit approval and paper validation.
