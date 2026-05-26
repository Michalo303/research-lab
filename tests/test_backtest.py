import pandas as pd
import pytest

from research_lab.backtest import weighted_backtest


def test_weighted_backtest_uses_prior_day_weights_for_returns():
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"SPY": [100.0, 110.0, 121.0]}, index=index)
    weights = pd.DataFrame({"SPY": [0.0, 1.0, 1.0]}, index=index)

    result = weighted_backtest(close, weights, cost_bps=0.0, periods_per_year=252)

    assert result["returns"].tolist() == pytest.approx([0.0, 0.0, 0.1])
