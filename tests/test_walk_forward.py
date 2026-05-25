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
