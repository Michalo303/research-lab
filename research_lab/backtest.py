from __future__ import annotations

import pandas as pd

from research_lab.metrics import performance_metrics, split_index


def close_frame(panel: pd.DataFrame) -> pd.DataFrame:
    if isinstance(panel.columns, pd.MultiIndex):
        return panel.xs("close", level=1, axis=1)
    return panel[["close"]]


def weighted_backtest(close: pd.DataFrame, weights: pd.DataFrame, cost_bps: float, periods_per_year: int) -> dict:
    close = close.sort_index()
    weights = weights.reindex(close.index).fillna(0.0).clip(lower=0.0, upper=1.0)
    asset_returns = close.pct_change().fillna(0.0)
    gross = (weights.shift(1).fillna(0.0) * asset_returns).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * (cost_bps / 10_000.0)
    trade_returns = _trade_returns(net, weights.sum(axis=1) > 0)
    splits = split_index(net.index)
    split_metrics = {
        name: performance_metrics(net.loc[idx], periods_per_year, _trade_returns(net.loc[idx], weights.loc[idx].sum(axis=1) > 0))
        for name, idx in splits.items()
    }
    return {
        "returns": net,
        "equity": (1.0 + net).cumprod(),
        "turnover": turnover,
        "metrics": performance_metrics(net, periods_per_year, trade_returns),
        "split_metrics": split_metrics,
        "average_turnover": float(turnover.mean()),
        "average_exposure": float(weights.sum(axis=1).mean()),
        "cost_bps": cost_bps,
    }


def cost_stress(close: pd.DataFrame, weights: pd.DataFrame, cost_bps: float, periods_per_year: int) -> dict:
    normal = weighted_backtest(close, weights, cost_bps, periods_per_year)
    double = weighted_backtest(close, weights, cost_bps * 2.0, periods_per_year)
    return {
        "normal_cost_bps": cost_bps,
        "double_cost_bps": cost_bps * 2.0,
        "normal_unseen_cagr": normal["split_metrics"]["unseen"]["cagr"],
        "double_unseen_cagr": double["split_metrics"]["unseen"]["cagr"],
        "survives_double_cost": double["split_metrics"]["unseen"]["cagr"] > 0,
    }


def _trade_returns(returns: pd.Series, in_market: pd.Series) -> list[float]:
    trades: list[float] = []
    active = False
    current = 1.0
    for ts, exposed in in_market.items():
        if exposed and not active:
            active = True
            current = 1.0
        if active:
            current *= 1.0 + float(returns.loc[ts])
        if active and not exposed:
            trades.append(current - 1.0)
            active = False
            current = 1.0
    if active:
        trades.append(current - 1.0)
    return trades

