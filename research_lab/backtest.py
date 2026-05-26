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
        "walk_forward": rolling_walk_forward(net, weights, periods_per_year),
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


def rolling_walk_forward(
    returns: pd.Series,
    weights: pd.DataFrame,
    periods_per_year: int,
    train_periods: int | None = None,
    test_periods: int | None = None,
    step_periods: int | None = None,
) -> dict:
    returns = returns.dropna().sort_index()
    weights = weights.reindex(returns.index).fillna(0.0)
    n = len(returns)
    if n < 4:
        return _empty_walk_forward("not_enough_data")

    train_periods = train_periods or _default_train_periods(n, periods_per_year)
    remaining = n - train_periods
    if remaining <= 0:
        return _empty_walk_forward("not_enough_oos_data")
    test_periods = test_periods or _default_test_periods(remaining, periods_per_year)
    step_periods = step_periods or test_periods
    if train_periods <= 0 or test_periods <= 0 or step_periods <= 0:
        return _empty_walk_forward("invalid_window_config")

    windows = []
    start = 0
    while start + train_periods + test_periods <= n:
        train_idx = returns.index[start : start + train_periods]
        test_idx = returns.index[start + train_periods : start + train_periods + test_periods]
        train_returns = returns.loc[train_idx]
        test_returns = returns.loc[test_idx]
        test_in_market = weights.loc[test_idx].sum(axis=1) > 0
        train_metrics = performance_metrics(
            train_returns,
            periods_per_year,
            _trade_returns(train_returns, weights.loc[train_idx].sum(axis=1) > 0),
        )
        test_metrics = performance_metrics(test_returns, periods_per_year, _trade_returns(test_returns, test_in_market))
        windows.append(
            {
                "window": len(windows) + 1,
                "train_start": _format_index_value(train_idx[0]),
                "train_end": _format_index_value(train_idx[-1]),
                "test_start": _format_index_value(test_idx[0]),
                "test_end": _format_index_value(test_idx[-1]),
                "train_cagr": train_metrics["cagr"],
                "train_max_drawdown": train_metrics["max_drawdown"],
                "test_cagr": test_metrics["cagr"],
                "test_sharpe": test_metrics["sharpe"],
                "test_max_drawdown": test_metrics["max_drawdown"],
                "test_trade_count": test_metrics["trade_count"],
                "test_average_exposure": float(weights.loc[test_idx].sum(axis=1).mean()),
                "passed": test_metrics["cagr"] > 0 and test_metrics["max_drawdown"] >= -0.15,
            }
        )
        start += step_periods

    if not windows:
        return _empty_walk_forward("not_enough_oos_windows")

    test_cagrs = [float(window["test_cagr"]) for window in windows]
    test_drawdowns = [float(window["test_max_drawdown"]) for window in windows]
    positive = sum(1 for value in test_cagrs if value > 0)
    passed = sum(1 for window in windows if window["passed"])
    return {
        "method": "rolling_train_then_test",
        "train_periods": train_periods,
        "test_periods": test_periods,
        "step_periods": step_periods,
        "window_count": len(windows),
        "positive_windows": positive,
        "passed_windows": passed,
        "pass_rate": passed / len(windows),
        "positive_rate": positive / len(windows),
        "worst_test_cagr": min(test_cagrs),
        "worst_test_drawdown": min(test_drawdowns),
        "median_test_cagr": float(pd.Series(test_cagrs).median()),
        "windows": windows,
        "status": "ok",
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


def _default_train_periods(n: int, periods_per_year: int) -> int:
    preferred = max(int(periods_per_year), int(n * 0.45))
    return min(preferred, max(1, n - 2))


def _default_test_periods(remaining: int, periods_per_year: int) -> int:
    preferred = max(1, periods_per_year // 2)
    return min(preferred, max(1, remaining // 3 or remaining))


def _empty_walk_forward(status: str) -> dict:
    return {
        "method": "rolling_train_then_test",
        "train_periods": 0,
        "test_periods": 0,
        "step_periods": 0,
        "window_count": 0,
        "positive_windows": 0,
        "passed_windows": 0,
        "pass_rate": 0.0,
        "positive_rate": 0.0,
        "worst_test_cagr": 0.0,
        "worst_test_drawdown": 0.0,
        "median_test_cagr": 0.0,
        "windows": [],
        "status": status,
    }


def _format_index_value(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
