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
        parameters={"symbol": symbol, "sma": 2},
        rules="Hold when close is above two-day SMA.",
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


def test_true_walk_forward_returns_window_and_aggregate_metrics():
    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    index = panel.index
    trend = pd.Series(range(len(index)), index=index, dtype=float)
    pullback = pd.Series([0.0 if i % 40 else -1.0 for i in range(len(index))], index=index)
    panel[("SPY", "close")] = 100.0 + trend * 0.05 + pullback
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
