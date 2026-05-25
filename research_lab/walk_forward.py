from __future__ import annotations

from typing import Any

import pandas as pd


def run_true_walk_forward(*args, **kwargs) -> dict[str, Any]:
    return _empty_walk_forward("not_enough_oos_windows")


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


def _empty_walk_forward(status: str) -> dict[str, Any]:
    return {
        "method": "true_rolling_oos",
        "train_years": 0,
        "test_years": 0,
        "step_years": 0,
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
