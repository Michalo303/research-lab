from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def split_index(index: pd.Index, train_frac: float = 0.45, validation_frac: float = 0.30) -> dict[str, pd.Index]:
    n = len(index)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + validation_frac))
    return {
        "train": index[:train_end],
        "validation": index[train_end:val_end],
        "unseen": index[val_end:],
    }


def performance_metrics(returns: pd.Series, periods_per_year: int, trade_returns: list[float] | None = None) -> dict:
    returns = returns.dropna()
    if returns.empty:
        return _empty_metrics()
    equity = (1.0 + returns).cumprod()
    years = max(len(returns) / periods_per_year, 1e-9)
    total_return = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    vol = float(returns.std(ddof=0) * math.sqrt(periods_per_year))
    sharpe = float((returns.mean() / returns.std(ddof=0)) * math.sqrt(periods_per_year)) if returns.std(ddof=0) > 0 else 0.0
    mdd = max_drawdown(equity)
    mar = float(cagr / abs(mdd)) if mdd < 0 else 0.0
    monthly = returns.resample("ME").sum() if isinstance(returns.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    positive_months = monthly[monthly > 0]
    dominant_month_share = float(positive_months.max() / positive_months.sum()) if positive_months.sum() > 0 else 0.0
    trade_stats = trade_metrics(trade_returns or [])
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annual_return": cagr,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "mar": mar,
        "dominant_month_profit_share": dominant_month_share,
        **trade_stats,
    }


def trade_metrics(trade_returns: list[float]) -> dict:
    if not trade_returns:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_per_trade": 0.0,
            "max_losing_streak": 0,
        }
    wins = [x for x in trade_returns if x > 0]
    losses = [x for x in trade_returns if x <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    losing_streak = 0
    max_losing_streak = 0
    for item in trade_returns:
        if item <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "trade_count": len(trade_returns),
        "win_rate": len(wins) / len(trade_returns),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy_per_trade": float(np.mean(trade_returns)),
        "max_losing_streak": max_losing_streak,
    }


def _empty_metrics() -> dict:
    return {
        "total_return": 0.0,
        "cagr": 0.0,
        "annual_return": 0.0,
        "annual_volatility": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "mar": 0.0,
        "dominant_month_profit_share": 0.0,
        "trade_count": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "expectancy_per_trade": 0.0,
        "max_losing_streak": 0,
    }

