from __future__ import annotations

import numpy as np
import pandas as pd

from research_lab.research.minervini_evaluation_gate_v1 import (
    evaluate_minervini_result_v1,
)
from research_lab.research.minervini_portfolio_evaluator_v1 import (
    run_minervini_portfolio_v1,
)
from research_lab.research.minervini_price_volume_core_v1 import (
    build_minervini_signals_v1,
)


def _ten_stock_panel() -> tuple[pd.DataFrame, dict[str, str], pd.Timestamp]:
    index = pd.bdate_range("2024-01-02", periods=390)
    breakout_position = 329
    leaders = {"GAPPER", "STOPPER", "WINNER"}
    symbols = sorted(leaders | {f"BASE{number}" for number in range(7)})
    data: dict[tuple[str, str], np.ndarray] = {}
    for rank, symbol in enumerate(symbols):
        close = np.linspace(30.0, 55.0 + rank, len(index))
        volume = np.full(len(index), 250_000.0)
        spread = np.full(len(index), 0.01)
        if symbol in leaders:
            close = np.linspace(30.0, 110.0, len(index))
            close[breakout_position - 60 : breakout_position - 40] = np.linspace(
                93.0, 99.0, 20
            )
            close[breakout_position - 40 : breakout_position - 20] = np.linspace(
                96.0, 100.0, 20
            )
            close[breakout_position - 20 : breakout_position] = np.linspace(
                98.5, 100.5, 20
            )
            close[breakout_position] = 102.0
            close[breakout_position + 1 :] = 115.0
            volume[breakout_position - 50 : breakout_position - 10] = 200_000.0
            volume[breakout_position - 10 : breakout_position] = 100_000.0
            volume[breakout_position] = 400_000.0
            spread[breakout_position - 60 : breakout_position - 40] = 0.025
            spread[breakout_position - 40 : breakout_position - 20] = 0.014
            spread[breakout_position - 20 : breakout_position] = 0.004
        data[(symbol, "open")] = close.copy()
        data[(symbol, "high")] = close * (1.0 + spread)
        data[(symbol, "low")] = close * (1.0 - spread)
        data[(symbol, "close")] = close
        data[(symbol, "volume")] = volume
    panel = pd.DataFrame(data, index=index)
    entry_position = breakout_position + 1
    panel.loc[index[entry_position], ("GAPPER", "open")] = 104.0
    panel.loc[
        index[entry_position],
        ("STOPPER", ["open", "high", "low", "close"]),
    ] = [101.0, 102.0, 99.0, 101.0]
    panel.loc[
        index[entry_position],
        ("WINNER", ["open", "high", "low", "close"]),
    ] = [101.0, 104.0, 100.0, 103.0]
    panel.loc[
        index[entry_position + 1],
        ("STOPPER", ["open", "high", "low", "close"]),
    ] = [96.0, 97.0, 95.0, 96.0]
    panel.loc[
        index[entry_position + 1],
        ("WINNER", ["open", "high", "low", "close"]),
    ] = [104.0, 112.0, 103.0, 110.0]
    weak_position = breakout_position + 56
    panel.loc[
        index[weak_position],
        ("WINNER", ["open", "high", "low", "close"]),
    ] = [103.0, 104.0, 101.5, 102.0]
    panel.loc[index[weak_position + 1], ("WINNER", "open")] = 103.0
    instrument_types = {symbol: "Common Stock" for symbol in symbols}
    return panel, instrument_types, index[breakout_position]


def _run_pipeline() -> tuple[pd.DataFrame, dict[str, object], dict[str, object]]:
    panel, instrument_types, _ = _ten_stock_panel()
    signals = build_minervini_signals_v1(
        panel,
        instrument_types=instrument_types,
    )
    portfolio = run_minervini_portfolio_v1(
        panel=panel,
        signals=signals,
        instrument_types=instrument_types,
    )
    gate = evaluate_minervini_result_v1(portfolio, data_blockers=[])
    return signals, portfolio, gate


def test_synthetic_pipeline_is_deterministic_chronological_and_side_effect_free():
    signals, portfolio, gate = _run_pipeline()
    signals_again, portfolio_again, gate_again = _run_pipeline()
    _, _, breakout_timestamp = _ten_stock_panel()

    assert sum(
        bool(signals.loc[breakout_timestamp, (symbol, "signal")])
        for symbol in ("GAPPER", "STOPPER", "WINNER")
    ) == 3
    assert {
        item["reason"] for item in portfolio["rejected_entries"]
    } >= {"GAP_ABOVE_PIVOT"}
    assert {item["exit_reason"] for item in portfolio["trades"]} >= {
        "PROTECTIVE_STOP",
        "SMA50_EXIT",
    }
    assert portfolio["output_sha256"] == portfolio_again["output_sha256"]
    assert gate["output_payload_sha256"] == gate_again["output_payload_sha256"]
    pd.testing.assert_frame_equal(signals, signals_again)
    assert portfolio["cash_reconciled"] is True
    assert portfolio["no_same_bar_fill_proof"] is True
    assert portfolio["maximum_gross_exposure_fraction"] <= 1.0
    assert all(
        trade["entry_timestamp"] > trade["signal_timestamp"]
        for trade in portfolio["trades"]
    )
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert portfolio["provider_calls_used"] == 0
    assert portfolio["network_used"] is False
    assert portfolio["broker_actions_used"] == 0
