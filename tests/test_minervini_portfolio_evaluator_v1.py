from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_lab.research.minervini_portfolio_evaluator_v1 import (
    run_minervini_portfolio_v1,
)


def _panel(
    *,
    symbols: tuple[str, ...] = ("AAA",),
    periods: int = 65,
) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=periods)
    data: dict[tuple[str, str], np.ndarray] = {}
    for symbol in symbols:
        close = np.full(periods, 105.0)
        data[(symbol, "open")] = close.copy()
        data[(symbol, "high")] = close + 1.0
        data[(symbol, "low")] = close - 1.0
        data[(symbol, "close")] = close
        data[(symbol, "volume")] = np.full(periods, 1_000_000.0)
    return pd.DataFrame(data, index=index)


def _signals(panel: pd.DataFrame) -> pd.DataFrame:
    symbols = sorted(set(panel.columns.get_level_values(0)))
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frames[symbol] = pd.DataFrame(
            {
                "signal": False,
                "pivot": np.nan,
                "structural_stop": np.nan,
                "atr20": np.nan,
                "rs_percentile": np.nan,
            },
            index=panel.index,
        )
    return pd.concat(frames, axis=1)


def test_next_open_entry_risk_sizing_and_gap_through_stop():
    panel = _panel(periods=6)
    signals = _signals(panel)
    signal_day, entry_day, stop_day = panel.index[:3]
    signals.loc[signal_day, ("AAA", "signal")] = True
    signals.loc[signal_day, ("AAA", "pivot")] = 100.0
    signals.loc[signal_day, ("AAA", "structural_stop")] = 94.0
    signals.loc[signal_day, ("AAA", "atr20")] = 2.0
    signals.loc[signal_day, ("AAA", "rs_percentile")] = 1.0
    panel.loc[entry_day, ("AAA", ["open", "high", "low", "close"])] = [
        100.0,
        103.0,
        99.0,
        102.0,
    ]
    panel.loc[stop_day, ("AAA", ["open", "high", "low", "close"])] = [
        92.0,
        93.0,
        90.0,
        91.0,
    ]

    result = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={"AAA": "Common Stock"},
        initial_cash=100_000.0,
        cost_bps_per_side=0.0,
    )

    trade = result["trades"][0]
    assert trade["entry_timestamp"] == entry_day.isoformat()
    assert trade["exit_timestamp"] == stop_day.isoformat()
    assert trade["quantity"] == 83
    assert trade["initial_account_risk"] == 498.0
    assert trade["entry_notional"] == 8_300.0
    assert trade["exit_price"] == 92.0
    assert trade["exit_reason"] == "PROTECTIVE_STOP"
    assert result["no_same_bar_fill_proof"] is True
    assert result["maximum_drawdown"] < 0
    assert result["cash_reconciled"] is True


@pytest.mark.parametrize(
    ("open_price", "stop", "atr", "reason"),
    [
        (103.0, 94.0, 2.0, "GAP_ABOVE_PIVOT"),
        (100.0, 97.0, 2.0, "STOP_INSIDE_TWO_ATR"),
        (100.0, 92.0, 2.0, "STOP_BEYOND_SEVEN_PERCENT"),
    ],
)
def test_invalid_entries_are_rejected(open_price, stop, atr, reason):
    panel = _panel(periods=4)
    signals = _signals(panel)
    signal_day, entry_day = panel.index[:2]
    signals.loc[signal_day, ("AAA", "signal")] = True
    signals.loc[signal_day, ("AAA", "pivot")] = 100.0
    signals.loc[signal_day, ("AAA", "structural_stop")] = stop
    signals.loc[signal_day, ("AAA", "atr20")] = atr
    signals.loc[signal_day, ("AAA", "rs_percentile")] = 1.0
    panel.loc[entry_day, ("AAA", "open")] = open_price

    result = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={"AAA": "Common Stock"},
        cost_bps_per_side=0.0,
    )

    assert result["trade_count"] == 0
    assert reason in {
        item["reason"] for item in result["rejected_entries"]
    }


def test_two_r_moves_stop_to_break_even_then_sma50_exit_is_next_open():
    panel = _panel()
    signals = _signals(panel)
    signal_day = panel.index[50]
    entry_day = panel.index[51]
    two_r_day = panel.index[52]
    weak_close_day = panel.index[53]
    exit_day = panel.index[54]
    signals.loc[signal_day, ("AAA", "signal")] = True
    signals.loc[signal_day, ("AAA", "pivot")] = 100.0
    signals.loc[signal_day, ("AAA", "structural_stop")] = 95.0
    signals.loc[signal_day, ("AAA", "atr20")] = 2.0
    signals.loc[signal_day, ("AAA", "rs_percentile")] = 1.0
    panel.loc[entry_day, ("AAA", ["open", "high", "low", "close"])] = [
        100.0,
        104.0,
        99.0,
        103.0,
    ]
    panel.loc[two_r_day, ("AAA", ["open", "high", "low", "close"])] = [
        104.0,
        112.0,
        103.0,
        111.0,
    ]
    panel.loc[
        weak_close_day, ("AAA", ["open", "high", "low", "close"])
    ] = [105.0, 106.0, 102.0, 103.0]
    panel.loc[exit_day, ("AAA", "open")] = 102.0

    result = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={"AAA": "Common Stock"},
        cost_bps_per_side=0.0,
    )

    trade = result["trades"][0]
    assert trade["break_even_activated"] is True
    assert trade["exit_reason"] == "SMA50_EXIT"
    assert trade["exit_timestamp"] == exit_day.isoformat()
    assert trade["exit_price"] == 102.0


def test_position_and_portfolio_caps_reject_ninth_concurrent_trade():
    symbols = tuple(f"S{i}" for i in range(9))
    panel = _panel(symbols=symbols, periods=5)
    signals = _signals(panel)
    signal_day = panel.index[0]
    for rank, symbol in enumerate(symbols, start=1):
        signals.loc[signal_day, (symbol, "signal")] = True
        signals.loc[signal_day, (symbol, "pivot")] = 100.0
        signals.loc[signal_day, (symbol, "structural_stop")] = 94.0
        signals.loc[signal_day, (symbol, "atr20")] = 2.0
        signals.loc[signal_day, (symbol, "rs_percentile")] = rank / 9
        panel.loc[panel.index[1], (symbol, "open")] = 100.0

    result = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={symbol: "Common Stock" for symbol in symbols},
        cost_bps_per_side=0.0,
    )

    assert result["maximum_concurrent_positions"] == 8
    assert "MAXIMUM_POSITIONS" in {
        item["reason"] for item in result["rejected_entries"]
    }
    assert result["maximum_gross_exposure_fraction"] <= 1.0


def test_costs_reduce_equity_and_are_reported():
    panel = _panel(periods=6)
    signals = _signals(panel)
    signal_day = panel.index[0]
    signals.loc[signal_day, ("AAA", "signal")] = True
    signals.loc[signal_day, ("AAA", "pivot")] = 100.0
    signals.loc[signal_day, ("AAA", "structural_stop")] = 94.0
    signals.loc[signal_day, ("AAA", "atr20")] = 2.0
    signals.loc[signal_day, ("AAA", "rs_percentile")] = 1.0
    panel.loc[panel.index[1], ("AAA", "open")] = 100.0

    zero = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={"AAA": "Common Stock"},
        cost_bps_per_side=0.0,
    )
    costly = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types={"AAA": "Common Stock"},
        cost_bps_per_side=15.0,
    )

    assert costly["transaction_costs"] > 0
    assert costly["ending_equity"] < zero["ending_equity"]
