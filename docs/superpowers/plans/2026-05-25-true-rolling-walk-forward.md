# True Rolling Walk-Forward Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a true rolling out-of-sample walk-forward validation layer that rebuilds strategy weights per calendar train/test window and replaces legacy pseudo-WF as the single `result.json["walk_forward"]` source.

**Architecture:** Add `research_lab/walk_forward.py` for window construction, sliced per-window weight generation, test-only evaluation, aggregate metrics, and deterministic regime tagging. Keep full-history backtests in `research_lab/backtest.py` for existing metrics, but integrate true WF in `research_lab/runner.py`, `research_lab/robustness.py`, `research_lab/deployment_gate.py`, and weekly reporting.

**Tech Stack:** Python, pandas, pytest, existing `research_lab.metrics.performance_metrics`, existing `research_lab.strategies.baselines.build_weights`.

---

## File Structure

- Create `research_lab/walk_forward.py`: true rolling OOS WF API, calendar window creation, per-window evaluation, regime tagging, aggregate metrics, and empty-status helpers.
- Create `tests/test_walk_forward.py`: focused tests for window boundaries, no leakage, metrics, aggregation, and SPY/unknown regime tags.
- Modify `research_lab/runner.py`: replace persisted `walk_forward` with `run_true_walk_forward(...)` output before tiering.
- Modify `research_lab/robustness.py`: expose true WF method, MAR, regime summary, and require true WF for pass.
- Modify `tests/test_robustness.py`: update fixtures and add missing/legacy failure tests.
- Modify `research_lab/deployment_gate.py`: add `min_walk_forward_windows`, env override, strict WF method check, and stop using rebalance observations as WF window count.
- Modify `tests/test_deployment_gate.py`: cover strict method behavior and config override.
- Modify `scripts/run_weekly_deep_research.py`: include true WF and regime summary text in robustness findings without changing portfolio/paper/Apify behavior.
- Modify `tests/test_weekly_pipeline.py`: assert weekly report includes true WF summary/regime breakdown.
- Modify `research_lab/tiering.py`: require true WF method before promotion if a `walk_forward` dict is provided.
- Keep `research_lab/backtest.py` and `tests/test_backtest.py` behavior compatible; do not edit them unless a focused compatibility failure proves the existing legacy contract changed.

## Task 1: True WF Test Scaffolding

**Files:**
- Create: `tests/test_walk_forward.py`
- No production code changes in this task

- [ ] **Step 1: Write failing tests for calendar windows and empty import**

Add this initial test file:

```python
import pandas as pd

from research_lab.strategies.baselines import StrategySpec
from research_lab.walk_forward import _rolling_calendar_windows, run_true_walk_forward


def _daily_panel(symbols=("SPY",), start="2016-01-01", end="2023-12-31"):
    index = pd.bdate_range(start, end)
    data = {}
    for symbol in symbols:
        close = pd.Series(100.0, index=index)
        data[(symbol, "open")] = close
        data[(symbol, "high")] = close * 1.01
        data[(symbol, "low")] = close * 0.99
        data[(symbol, "close")] = close
        data[(symbol, "volume")] = 1_000_000
    return pd.DataFrame(data, index=index)


def _buy_and_hold_spec(symbol="SPY"):
    return StrategySpec(
        family="LONGTERM",
        asset_class="ETF",
        timeframe="1D",
        short_name="TEST_BUY_HOLD",
        hypothesis="Test strategy",
        parameters={"symbol": symbol, "sma": 1},
        rules="Hold when close is above one-day SMA.",
        builder="long_term_trend_filter",
    )


def test_calendar_windows_use_date_offsets_and_valid_index_boundaries():
    index = pd.bdate_range("2016-01-01", "2023-12-31")

    windows = _rolling_calendar_windows(index, train_years=5, test_years=1, step_years=1)

    assert len(windows) == sum(1 for _ in windows)
    assert len(windows) >= 2
    first = windows[0]
    assert first["train_start"] == index[0]
    assert first["train_end"] <= pd.Timestamp("2020-12-31")
    assert first["test_start"] >= pd.Timestamp("2021-01-01")
    assert first["test_end"] <= pd.Timestamp("2021-12-31")
    assert windows[1]["train_start"] >= pd.Timestamp("2017-01-01")
    assert windows[1]["test_start"] >= pd.Timestamp("2022-01-01")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_walk_forward.py::test_calendar_windows_use_date_offsets_and_valid_index_boundaries -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'research_lab.walk_forward'`.

- [ ] **Step 3: Commit failing test**

```bash
git add tests/test_walk_forward.py
git commit -m "test: define true walk-forward window contract"
```

## Task 2: Window Construction

**Files:**
- Create: `research_lab/walk_forward.py`
- Test: `tests/test_walk_forward.py`

- [ ] **Step 1: Implement minimal calendar window construction**

Create `research_lab/walk_forward.py`:

```python
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
```

- [ ] **Step 2: Run window test**

Run:

```bash
pytest tests/test_walk_forward.py::test_calendar_windows_use_date_offsets_and_valid_index_boundaries -v
```

Expected: PASS.

- [ ] **Step 3: Commit window construction**

```bash
git add research_lab/walk_forward.py tests/test_walk_forward.py
git commit -m "feat: add calendar walk-forward windows"
```

## Task 3: True WF Evaluation Metrics

**Files:**
- Modify: `tests/test_walk_forward.py`
- Modify: `research_lab/walk_forward.py`

- [ ] **Step 1: Add failing metrics and aggregation test**

Append to `tests/test_walk_forward.py`:

```python
def test_true_walk_forward_returns_window_and_aggregate_metrics():
    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    index = panel.index
    trend = pd.Series(range(len(index)), index=index, dtype=float)
    panel[("SPY", "close")] = 100.0 + trend * 0.05
    close = panel.xs("close", level=1, axis=1)
    spec = _buy_and_hold_spec("SPY")

    result = run_true_walk_forward(spec, panel, None, close, cost_bps=0.0, periods_per_year=252)

    expected_windows = _rolling_calendar_windows(close.index, 5, 1, 1)
    assert result["status"] == "ok"
    assert result["method"] == "true_rolling_oos"
    assert result["train_years"] == 5
    assert result["test_years"] == 1
    assert result["step_years"] == 1
    assert result["window_count"] == len(expected_windows)
    assert result["pass_rate"] == 1.0
    assert result["median_test_cagr"] > 0
    assert result["median_test_mar"] > 0
    assert result["worst_test_cagr"] > 0
    assert result["worst_test_drawdown"] >= -0.20
    first = result["windows"][0]
    assert first["test_cagr"] > 0
    assert first["test_max_drawdown"] >= -0.20
    assert first["test_mar"] > 0
    assert first["test_trade_count"] >= 1
    assert 0.0 <= first["test_average_exposure"] <= 1.0
    assert first["passed"] is True
```

- [ ] **Step 2: Run metrics test to verify it fails**

Run:

```bash
pytest tests/test_walk_forward.py::test_true_walk_forward_returns_window_and_aggregate_metrics -v
```

Expected: FAIL because `run_true_walk_forward` returns status `not_enough_oos_windows` instead of `ok`.

- [ ] **Step 3: Implement true WF evaluation**

Replace `research_lab/walk_forward.py` with:

```python
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
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_walk_forward.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit evaluation logic**

```bash
git add research_lab/walk_forward.py tests/test_walk_forward.py
git commit -m "feat: evaluate true walk-forward windows"
```

## Task 4: No-Leakage and Regime Tests

**Files:**
- Modify: `tests/test_walk_forward.py`
- Modify: `research_lab/walk_forward.py`

- [ ] **Step 1: Add hard no-leakage and regime tests**

Append:

```python
def test_true_walk_forward_does_not_build_weights_from_full_history(monkeypatch):
    import research_lab.walk_forward as wf

    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    close = panel.xs("close", level=1, axis=1)
    full_history_end = close.index[-1]
    seen_slice_ends = []
    spec = _buy_and_hold_spec("SPY")

    def leaking_detector(spec_arg, daily_arg, intraday_arg):
        seen_slice_ends.append(daily_arg.index[-1])
        assert daily_arg.index[-1] < full_history_end
        return pd.DataFrame({"SPY": 1.0}, index=daily_arg.index)

    monkeypatch.setattr(wf, "build_weights", leaking_detector)

    result = wf.run_true_walk_forward(spec, panel, None, close, cost_bps=0.0, periods_per_year=252)

    assert result["status"] == "ok"
    assert seen_slice_ends
    assert all(slice_end < full_history_end for slice_end in seen_slice_ends)


def test_regime_tag_uses_unknown_when_spy_is_missing():
    panel = _daily_panel(("QQQ",), start="2016-01-01", end="2023-12-31")
    close = panel.xs("close", level=1, axis=1)
    spec = _buy_and_hold_spec("QQQ")

    result = run_true_walk_forward(spec, panel, None, close, cost_bps=0.0, periods_per_year=252)

    assert {row["regime"] for row in result["windows"]} == {"unknown"}
    assert "unknown:" in result["regime_summary"]


def test_regime_precedence_marks_crisis_before_bull():
    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    close = panel.xs("close", level=1, axis=1)
    test_index = close.loc["2021-01-01":"2021-12-31"].index
    close.loc[test_index, "SPY"] = [100.0, 70.0] + [120.0] * (len(test_index) - 2)

    from research_lab.walk_forward import _regime_for_window

    assert _regime_for_window(close, test_index) == "crisis"
```

- [ ] **Step 2: Run no-leakage/regime tests**

Run:

```bash
pytest tests/test_walk_forward.py::test_true_walk_forward_does_not_build_weights_from_full_history tests/test_walk_forward.py::test_regime_tag_uses_unknown_when_spy_is_missing tests/test_walk_forward.py::test_regime_precedence_marks_crisis_before_bull -v
```

Expected: PASS. If the no-leakage assertion fails, change `_evaluate_window` so `build_weights` receives `sliced_daily` and `sliced_intraday`, never the full panels.

- [ ] **Step 3: Commit no-leakage coverage**

```bash
git add tests/test_walk_forward.py research_lab/walk_forward.py
git commit -m "test: guard walk-forward leakage and regimes"
```

## Task 5: Runner Integration

**Files:**
- Modify: `research_lab/runner.py`
- Modify: tests if an existing runner test covers backtest contract

- [ ] **Step 1: Add runner assertion**

In `tests/test_backtest_contract.py`, update `test_daily_results_include_required_paper_contract` by adding this assertion inside the `for result in results:` loop:

```python
assert result["walk_forward"]["method"] == "true_rolling_oos"
```

- [ ] **Step 2: Run runner-focused test**

Run:

```bash
pytest tests/test_backtest_contract.py -v
```

Expected: FAIL until `runner.py` uses `run_true_walk_forward`.

- [ ] **Step 3: Integrate true WF before tiering**

In `research_lab/runner.py`, add import:

```python
from research_lab.walk_forward import run_true_walk_forward
```

Inside the strategy loop, after `backtest` and `stress` are computed and before `classify_strategy(...)`, add:

```python
        walk_forward = run_true_walk_forward(
            spec,
            daily_bundle.data,
            intraday_bundle.data if spec.family == "INTRADAY" else None,
            close,
            cost_bps,
            periods_per_year,
        )
```

Then change tiering and result persistence from `backtest["walk_forward"]` to `walk_forward`:

```python
            walk_forward,
```

and:

```python
            "walk_forward": walk_forward,
```

- [ ] **Step 4: Run runner/backtest tests**

Run:

```bash
pytest tests/test_backtest_contract.py tests/test_backtest.py tests/test_walk_forward.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit runner integration**

```bash
git add research_lab/runner.py tests/test_backtest_contract.py tests/test_walk_forward.py
git commit -m "feat: persist true walk-forward results"
```

## Task 6: Tiering True WF Method Check

**Files:**
- Modify: `research_lab/tiering.py`
- Add/modify: a test file if tiering has no direct tests

- [ ] **Step 1: Add failing tiering test**

Create `tests/test_tiering.py`:

```python
from research_lab.tiering import classify_strategy


def _metrics():
    split = {
        "cagr": 0.12,
        "max_drawdown": -0.05,
        "sharpe": 1.2,
        "mar": 2.0,
        "profit_factor": 1.5,
        "trade_count": 150,
    }
    return {"train": split, "validation": split, "unseen": split}


def test_tiering_does_not_promote_legacy_walk_forward():
    tier, reason = classify_strategy(
        "ROTATION",
        _metrics(),
        {"survives_double_cost": True},
        "massive",
        22.0,
        {
            "method": "rolling_train_then_test",
            "status": "ok",
            "window_count": 12,
            "pass_rate": 1.0,
            "median_test_cagr": 0.12,
            "worst_test_drawdown": -0.05,
        },
    )

    assert tier == "C"
    assert "walk-forward" in reason.lower()
```

- [ ] **Step 2: Run tiering test**

Run:

```bash
pytest tests/test_tiering.py -v
```

Expected: FAIL if legacy method currently passes.

- [ ] **Step 3: Add method check**

In `research_lab/tiering.py`, update `_walk_forward_passes`:

```python
def _walk_forward_passes(walk_forward: dict) -> bool:
    if walk_forward.get("method") != "true_rolling_oos":
        return False
    if walk_forward.get("status") != "ok":
        return False
    window_count = int(walk_forward.get("window_count", 0) or 0)
    if window_count < 3:
        return False
    pass_rate = float(walk_forward.get("pass_rate", 0.0) or 0.0)
    worst_drawdown = float(walk_forward.get("worst_test_drawdown", 0.0) or 0.0)
    median_cagr = float(walk_forward.get("median_test_cagr", 0.0) or 0.0)
    return pass_rate >= 0.67 and median_cagr > 0 and worst_drawdown >= -0.20
```

- [ ] **Step 4: Run tiering test**

Run:

```bash
pytest tests/test_tiering.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit tiering check**

```bash
git add research_lab/tiering.py tests/test_tiering.py
git commit -m "fix: require true walk-forward for tiering"
```

## Task 7: Robustness Reporting

**Files:**
- Modify: `research_lab/robustness.py`
- Modify: `tests/test_robustness.py`

- [ ] **Step 1: Update robustness fixture and assertions**

In `tests/test_robustness.py`, update `_result(...)"walk_forward"` to include:

```python
            "method": "true_rolling_oos",
            "median_test_mar": 1.5,
            "regime_summary": "bull:2/2;bear:1/1",
```

Add assertions:

```python
    assert rows[0]["walk_forward_method"] == "true_rolling_oos"
    assert rows[0]["median_test_mar"] == 1.5
    assert rows[0]["regime_summary"] == "bull:2/2;bear:1/1"
```

Add a legacy failure test:

```python
def test_build_robustness_rows_fails_legacy_walk_forward_method():
    item = _result("A", 0.12)
    item["walk_forward"]["method"] = "rolling_train_then_test"

    rows = build_robustness_rows([item])

    assert rows[0]["walk_forward_method"] == "rolling_train_then_test"
    assert rows[0]["robustness_verdict"] == "fail"
```

- [ ] **Step 2: Run robustness tests to verify failure**

Run:

```bash
pytest tests/test_robustness.py -v
```

Expected: FAIL because new columns/strict method are not implemented.

- [ ] **Step 3: Update robustness columns and verdict**

In `research_lab/robustness.py`, add columns near existing WF fields:

```python
    "walk_forward_method",
    "pass_rate",
    "median_test_mar",
    "regime_summary",
```

In `_robustness_row`, add:

```python
        "walk_forward_method": walk_forward.get("method", "missing"),
        "pass_rate": walk_forward_score,
        "median_test_mar": float(walk_forward.get("median_test_mar", 0.0) or 0.0),
        "regime_summary": walk_forward.get("regime_summary", ""),
```

In `_robustness_verdict`, add method check first:

```python
    if walk_forward.get("method") != "true_rolling_oos":
        return "fail"
```

Update `summarize_weekly_robustness` to include true WF/regime summary:

```python
        f"- true WF method rows: {sum(1 for row in robustness_rows if row.get('walk_forward_method') == 'true_rolling_oos')}",
```

For the best row, append:

```python
            f" regime={best.get('regime_summary', '')}"
```

- [ ] **Step 4: Run robustness tests**

Run:

```bash
pytest tests/test_robustness.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit robustness update**

```bash
git add research_lab/robustness.py tests/test_robustness.py
git commit -m "feat: report true walk-forward robustness"
```

## Task 8: Deployment Gate Strict WF

**Files:**
- Modify: `research_lab/deployment_gate.py`
- Modify: `tests/test_deployment_gate.py`

- [ ] **Step 1: Add failing deployment gate tests**

Append to `tests/test_deployment_gate.py`:

```python
def test_paper_gate_config_reads_min_walk_forward_windows(monkeypatch):
    monkeypatch.setenv("PAPER_GATE_MIN_WALK_FORWARD_WINDOWS", "4")

    config = PaperGateConfig.from_env()

    assert config.min_walk_forward_windows == 4


def test_deployment_gate_rejects_legacy_walk_forward_method_even_with_good_metrics():
    item = {
        "strategy_id": "S1",
        "family": "ROTATION",
        "short_name": "DUAL_MOMENTUM",
        "tier": "B",
        "hypothesis": "Momentum rotation edge",
        "data_manifest": {"source": "massive", "years": 22.0},
        "data_source": "massive",
        "data_start": "2003-01-01",
        "data_end": "2025-12-31",
        "history_length": 22.0,
        "cost_model": {"type": "turnover_bps"},
        "universe": ["SPY", "QQQ", "TLT", "GLD"],
        "return_series": [{"date": "2025-01-01", "value": 0.01}],
        "target_weight_series": [{"date": "2025-01-01", "SPY": 1.0}],
        "latest_signal": {"as_of": "2025-01-01", "target_weights": {"SPY": 1.0}},
        "cost_stress": {"survives_double_cost": True},
        "split_metrics": {"unseen": {"cagr": 0.2, "max_drawdown": -0.05, "trade_count": 150}},
        "walk_forward": {
            "method": "rolling_train_then_test",
            "status": "ok",
            "window_count": 4,
            "pass_rate": 1.0,
            "median_test_cagr": 0.05,
            "worst_test_drawdown": -0.05,
        },
    }
    robustness = {"robustness_verdict": "pass"}
    parameter_by_group = {("ROTATION", "DUAL_MOMENTUM"): "pass"}
    portfolio = {"portfolio_score": 1.0, "suggested_weight_pct": 5.0}

    row = _gate_row(item, robustness, parameter_by_group, portfolio, PaperGateConfig())

    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]
```

- [ ] **Step 2: Run deployment gate tests to verify failure**

Run:

```bash
pytest tests/test_deployment_gate.py -v
```

Expected: FAIL because `min_walk_forward_windows` and strict method check are missing.

- [ ] **Step 3: Add config and strict gate checks**

In `PaperGateConfig`, add field:

```python
    min_walk_forward_windows: int = 3
```

In `from_env`, add:

```python
            min_walk_forward_windows=int(os.getenv("PAPER_GATE_MIN_WALK_FORWARD_WINDOWS", "3")),
```

In `DEPLOYMENT_GATE_COLUMNS`, add:

```python
    "minimum_walk_forward_windows",
```

In `_gate_row`, replace the WF verdict with:

```python
    walk_forward_verdict = (
        robustness.get("robustness_verdict") == "pass"
        and walk_forward.get("method") == "true_rolling_oos"
        and walk_forward.get("status") == "ok"
        and int(walk_forward.get("window_count", 0) or 0) >= config.min_walk_forward_windows
        and float(walk_forward.get("pass_rate", 0.0) or 0.0) >= config.min_wf_pass_rate
        and float(walk_forward.get("median_test_cagr", 0.0) or 0.0) > 0
        and float(walk_forward.get("worst_test_drawdown", 0.0) or 0.0) >= -0.20
    )
```

Remove this old WF-window misuse:

```python
    if family in {"LONGTERM", "ROTATION"} and int(walk_forward.get("window_count", 0) or 0) < config.min_rebalance_observations:
        reasons.append("insufficient_rebalance_observations")
```

Add row field:

```python
        "minimum_walk_forward_windows": config.min_walk_forward_windows,
```

- [ ] **Step 4: Run deployment gate tests**

Run:

```bash
pytest tests/test_deployment_gate.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit deployment gate update**

```bash
git add research_lab/deployment_gate.py tests/test_deployment_gate.py
git commit -m "fix: gate on true walk-forward windows"
```

## Task 9: Weekly Report Summary

**Files:**
- Modify: `scripts/run_weekly_deep_research.py`
- Modify: `tests/test_weekly_pipeline.py`

- [ ] **Step 1: Add failing weekly report assertions**

In `tests/test_weekly_pipeline.py`, update the monkeypatched robustness rows in the first test:

```python
    monkeypatch.setattr(weekly, "write_weekly_robustness_outputs", lambda root, stem: {
        "robustness_rows": [{"walk_forward_method": "true_rolling_oos", "regime_summary": "bull:2/3"}],
        "stability_rows": [],
        "robustness_path": "robust.csv",
        "stability_path": "stab.csv",
    })
```

Add report assertions:

```python
    assert "true walk-forward" in report.lower()
    assert "bull:2/3" in report
```

- [ ] **Step 2: Run weekly pipeline test to verify failure**

Run:

```bash
pytest tests/test_weekly_pipeline.py::test_weekly_pipeline_applies_dedupe_and_reports_dashboard_smoke -v
```

Expected: FAIL until weekly summary emits the explicit text.

- [ ] **Step 3: Add weekly true WF summary helper**

In `scripts/run_weekly_deep_research.py`, add helper:

```python
def summarize_true_walk_forward_regimes(robustness_rows: list[dict]) -> list[str]:
    true_rows = [row for row in robustness_rows if row.get("walk_forward_method") == "true_rolling_oos"]
    if not true_rows:
        return ["- true walk-forward: no true rolling OOS rows available"]
    summaries = [str(row.get("regime_summary", "")).strip() for row in true_rows if str(row.get("regime_summary", "")).strip()]
    unique = sorted(set(summaries))
    regime_text = "; ".join(unique) if unique else "no regime summary"
    return [
        f"- true walk-forward rows: {len(true_rows)}",
        f"- true walk-forward regime breakdown: {regime_text}",
    ]
```

In the `## Robustness Findings` section, after `summarize_weekly_robustness(...)`, add:

```python
        *summarize_true_walk_forward_regimes(robustness["robustness_rows"]),
```

- [ ] **Step 4: Run weekly tests**

Run:

```bash
pytest tests/test_weekly_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit weekly summary update**

```bash
git add scripts/run_weekly_deep_research.py tests/test_weekly_pipeline.py
git commit -m "feat: summarize true walk-forward regimes weekly"
```

## Task 10: Compatibility and Full Verification

**Files:**
- Verify: `research_lab/walk_forward.py`
- Verify: `research_lab/runner.py`
- Verify: `research_lab/robustness.py`
- Verify: `research_lab/deployment_gate.py`
- Verify: `scripts/run_weekly_deep_research.py`
- Verify: `tests/`

- [ ] **Step 1: Run focused suite**

Run:

```bash
pytest tests/test_walk_forward.py tests/test_robustness.py tests/test_deployment_gate.py tests/test_weekly_pipeline.py tests/test_tiering.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest
```

Expected: PASS. For any failure, inspect whether the failure is caused by this PR before changing code, and record the exact test name and assertion in the final summary.

- [ ] **Step 3: Run a local sample daily research smoke if test suite passes**

Run:

```bash
python scripts/run_daily_research.py
```

Expected: command completes and writes new `backtests/runs/*/result.json` files. Inspect one result:

```bash
Get-ChildItem backtests/runs -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { Get-Content "$($_.FullName)\result.json" | Select-String '"walk_forward"|"method"|"true_rolling_oos"|"median_test_mar"|"regime_summary"' }
```

Expected: output includes `"method": "true_rolling_oos"`, `"median_test_mar"`, and `"regime_summary"`.

- [ ] **Step 4: Inspect git diff for scope control**

Run:

```bash
git diff --stat HEAD
git diff -- research_lab/walk_forward.py research_lab/runner.py research_lab/robustness.py research_lab/deployment_gate.py scripts/run_weekly_deep_research.py
```

Expected: diff is limited to true WF, tests, gate/report integration, and no portfolio/paper/live/broker/Apify behavior changes.

- [ ] **Step 5: Confirm there are no uncommitted scoped fixes**

```bash
git status --short
```

Expected: no uncommitted true-WF implementation files remain. If scoped fixes remain from verification, commit them with:

```bash
git add research_lab tests scripts
git commit -m "test: verify true walk-forward integration"
```
