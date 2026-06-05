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


def test_true_walk_forward_emits_progress_when_callback_is_supplied(monkeypatch):
    import research_lab.walk_forward as wf

    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    close = panel.xs("close", level=1, axis=1)
    expected_windows = _rolling_calendar_windows(close.index, 5, 1, 1)
    messages = []
    times = iter([10.0, 12.5])

    monkeypatch.setattr(wf, "perf_counter", lambda: next(times))

    result = wf.run_true_walk_forward(
        _buy_and_hold_spec("SPY"),
        panel,
        None,
        close,
        cost_bps=0.0,
        periods_per_year=252,
        progress_log=messages.append,
    )

    assert result["status"] == "ok"
    assert messages[0] == f"true walk-forward start strategy=TEST_BUY_HOLD windows={len(expected_windows)}"
    assert messages[1].startswith(f"true walk-forward done strategy=TEST_BUY_HOLD windows={len(expected_windows)} elapsed=")


def test_true_walk_forward_does_not_build_weights_from_full_history(monkeypatch):
    import research_lab.walk_forward as wf

    panel = _daily_panel(("SPY",), start="2016-01-01", end="2023-12-31")
    close = panel.xs("close", level=1, axis=1)
    full_history_end = close.index[-1]
    expected_windows = _rolling_calendar_windows(close.index, 5, 1, 1)
    seen_slices = []
    spec = _buy_and_hold_spec("SPY")

    def leaking_detector(spec_arg, daily_arg, intraday_arg):
        expected = expected_windows[len(seen_slices)]
        seen_slices.append((daily_arg.index[0], daily_arg.index[-1]))
        assert daily_arg.index[0] == expected["train_start"]
        assert daily_arg.index[-1] == expected["test_end"]
        assert daily_arg.index[-1] < full_history_end
        assert intraday_arg is None
        return pd.DataFrame({"SPY": 1.0}, index=daily_arg.index)

    monkeypatch.setattr(wf, "build_weights", leaking_detector)

    result = wf.run_true_walk_forward(spec, panel, None, close, cost_bps=0.0, periods_per_year=252)

    assert result["status"] == "ok"
    assert seen_slices == [(window["train_start"], window["test_end"]) for window in expected_windows]


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
