from __future__ import annotations

import statistics
from typing import Any

import pandas as pd

from research_lab.metrics import performance_metrics
from research_lab.strategies.baselines import StrategySpec, build_weights


def run_true_walk_forward(
    spec: StrategySpec,
    daily_panel: pd.DataFrame,
    intraday_panel: pd.DataFrame | None,
    close: pd.DataFrame,
    cost_bps: float,
    periods_per_year: int,
    train_years: int = 5,
    test_years: int = 1,
    step_years: int = 1,
) -> dict[str, Any]:
    close = close.sort_index()
    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        return _empty_walk_forward("not_enough_data", train_years, test_years, step_years)

    windows = []
    for number, bounds in enumerate(_rolling_calendar_windows(close.index, train_years, test_years, step_years), start=1):
        window = _evaluate_window(number, bounds, spec, daily_panel, intraday_panel, close, cost_bps, periods_per_year)
        if window is not None:
            windows.append(window)

    if not windows:
        return _empty_walk_forward("not_enough_oos_windows", train_years, test_years, step_years)

    test_cagrs = [float(row["test_cagr"]) for row in windows]
    test_mars = [float(row["test_mar"]) for row in windows]
    test_drawdowns = [float(row["test_max_drawdown"]) for row in windows]
    passed = sum(1 for row in windows if row["passed"])
    positive = sum(1 for value in test_cagrs if value > 0)
    return {
        "method": "true_rolling_oos",
        "train_years": train_years,
        "test_years": test_years,
        "step_years": step_years,
        "window_count": len(windows),
        "positive_windows": positive,
        "passed_windows": passed,
        "pass_rate": passed / len(windows),
        "positive_rate": positive / len(windows),
        "median_test_cagr": float(statistics.median(test_cagrs)),
        "median_test_mar": float(statistics.median(test_mars)),
        "worst_test_cagr": min(test_cagrs),
        "worst_test_drawdown": min(test_drawdowns),
        "regime_summary": _regime_summary(windows),
        "windows": windows,
        "status": "ok",
    }


def _evaluate_window(
    number: int,
    bounds: dict[str, pd.Timestamp],
    spec: StrategySpec,
    daily_panel: pd.DataFrame,
    intraday_panel: pd.DataFrame | None,
    close: pd.DataFrame,
    cost_bps: float,
    periods_per_year: int,
) -> dict[str, Any] | None:
    slice_start = bounds["train_start"]
    slice_end = bounds["test_end"]
    test_start = bounds["test_start"]
    test_end = bounds["test_end"]
    sliced_daily = daily_panel.loc[(daily_panel.index >= slice_start) & (daily_panel.index <= slice_end)]
    sliced_intraday = None
    if intraday_panel is not None:
        sliced_intraday = intraday_panel.loc[(intraday_panel.index >= slice_start) & (intraday_panel.index <= slice_end)]
    sliced_close = close.loc[(close.index >= slice_start) & (close.index <= slice_end)]
    if sliced_close.empty:
        return None

    weights = build_weights(spec, sliced_daily, sliced_intraday)
    weights = weights.reindex(sliced_close.index).fillna(0.0).clip(lower=0.0, upper=1.0)
    test_mask = (sliced_close.index >= test_start) & (sliced_close.index <= test_end)
    test_close = sliced_close.loc[test_mask]
    if test_close.empty:
        return None

    asset_returns = sliced_close.pct_change().fillna(0.0)
    gross = (weights.shift(1).fillna(0.0) * asset_returns).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * (cost_bps / 10_000.0)
    test_returns = net.loc[test_close.index]
    test_weights = weights.loc[test_close.index]
    test_in_market = test_weights.sum(axis=1) > 0
    metrics = performance_metrics(test_returns, periods_per_year, _trade_returns(test_returns, test_in_market))
    test_cagr = float(metrics["cagr"])
    test_dd = float(metrics["max_drawdown"])
    return {
        "window": number,
        "train_start": _format_index_value(bounds["train_start"]),
        "train_end": _format_index_value(bounds["train_end"]),
        "test_start": _format_index_value(test_start),
        "test_end": _format_index_value(test_end),
        "test_cagr": test_cagr,
        "test_max_drawdown": test_dd,
        "test_mar": float(metrics["mar"]),
        "test_trade_count": int(metrics["trade_count"]),
        "test_average_exposure": float(test_weights.sum(axis=1).mean()),
        "regime": _regime_for_window(close, test_close.index),
        "passed": test_cagr > 0 and test_dd >= -0.20,
    }


def _rolling_calendar_windows(
    index: pd.Index,
    train_years: int = 5,
    test_years: int = 1,
    step_years: int = 1,
) -> list[dict[str, pd.Timestamp]]:
    clean_index = pd.DatetimeIndex(index).dropna().sort_values().unique()
    if clean_index.empty:
        return []

    windows: list[dict[str, pd.Timestamp]] = []
    train_start_target = clean_index[0]
    last_date = clean_index[-1]
    while True:
        train_end_target = train_start_target + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        test_start_target = train_end_target + pd.Timedelta(days=1)
        test_end_target = test_start_target + pd.DateOffset(years=test_years) - pd.Timedelta(days=1)
        if test_end_target > last_date:
            break

        train_idx = clean_index[(clean_index >= train_start_target) & (clean_index <= train_end_target)]
        test_idx = clean_index[(clean_index >= test_start_target) & (clean_index <= test_end_target)]
        if len(train_idx) > 0 and len(test_idx) > 0:
            windows.append(
                {
                    "train_start": train_idx[0],
                    "train_end": train_idx[-1],
                    "test_start": test_idx[0],
                    "test_end": test_idx[-1],
                }
            )
        train_start_target = train_start_target + pd.DateOffset(years=step_years)

    return windows


def _regime_for_window(close: pd.DataFrame, test_index: pd.Index) -> str:
    if "SPY" not in close.columns:
        return "unknown"
    spy = close.loc[test_index, "SPY"].dropna()
    if len(spy) < 2:
        return "unknown"
    returns = spy.pct_change().fillna(0.0)
    equity = (1.0 + returns).cumprod()
    max_dd = float((equity / equity.cummax() - 1.0).min())
    total_return = float(spy.iloc[-1] / spy.iloc[0] - 1.0)
    if max_dd <= -0.25:
        return "crisis"
    if total_return < 0 and max_dd <= -0.15:
        return "bear"
    if total_return > 0.10:
        return "bull"
    if abs(total_return) <= 0.10:
        return "sideways"
    return "unknown"


def _regime_summary(windows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    passed: dict[str, int] = {}
    for window in windows:
        regime = str(window.get("regime", "unknown"))
        counts[regime] = counts.get(regime, 0) + 1
        if window.get("passed"):
            passed[regime] = passed.get(regime, 0) + 1
    return ";".join(f"{regime}:{passed.get(regime, 0)}/{count}" for regime, count in sorted(counts.items()))


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


def _empty_walk_forward(status: str, train_years: int = 0, test_years: int = 0, step_years: int = 0) -> dict[str, Any]:
    return {
        "method": "true_rolling_oos",
        "train_years": train_years,
        "test_years": test_years,
        "step_years": step_years,
        "window_count": 0,
        "positive_windows": 0,
        "passed_windows": 0,
        "pass_rate": 0.0,
        "positive_rate": 0.0,
        "median_test_cagr": 0.0,
        "median_test_mar": 0.0,
        "worst_test_cagr": 0.0,
        "worst_test_drawdown": 0.0,
        "regime_summary": "",
        "windows": [],
        "status": status,
    }


def _format_index_value(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
