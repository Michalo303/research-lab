from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_lab.research.minervini_price_volume_core_v1 import (
    MinerviniCoreConfigV1,
    build_minervini_signals_v1,
)


def _panel() -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=330)
    output: dict[tuple[str, str], np.ndarray] = {}
    for symbol, ending in (("LEADER", 100.0), ("SECOND", 75.0), ("LAGGARD", 55.0)):
        close = np.linspace(30.0, ending, len(index))
        if symbol == "LEADER":
            close[-61:-41] = np.linspace(93.0, 99.0, 20)
            close[-41:-21] = np.linspace(96.0, 100.0, 20)
            close[-21:-1] = np.linspace(98.5, 100.5, 20)
            close[-1] = 102.0
        volume = np.full(len(index), 250_000.0)
        if symbol == "LEADER":
            volume[-51:-11] = 200_000.0
            volume[-11:-1] = 100_000.0
            volume[-1] = 400_000.0
        spread = np.full(len(index), 0.01)
        if symbol == "LEADER":
            spread[-61:-41] = 0.045
            spread[-41:-21] = 0.025
            spread[-21:-1] = 0.012
        output[(symbol, "open")] = close * 0.999
        output[(symbol, "high")] = close * (1.0 + spread)
        output[(symbol, "low")] = close * (1.0 - spread)
        output[(symbol, "close")] = close
        output[(symbol, "volume")] = volume
    return pd.DataFrame(output, index=index)


def _types() -> dict[str, str]:
    return {
        "LEADER": "Common Stock",
        "SECOND": "Common Stock",
        "LAGGARD": "Common Stock",
    }


def test_signal_requires_complete_trend_template_vcp_and_rs():
    panel = _panel()

    signals = build_minervini_signals_v1(
        panel,
        instrument_types=_types(),
    )

    timestamp = panel.index[-1]
    assert bool(signals.loc[timestamp, ("LEADER", "eligible")]) is True
    assert signals.loc[timestamp, ("LEADER", "rs_percentile")] >= 0.80
    assert bool(signals.loc[timestamp, ("LEADER", "vcp")]) is True
    assert bool(signals.loc[timestamp, ("LEADER", "signal")]) is True
    assert signals.loc[timestamp, ("LEADER", "pivot")] < panel.loc[
        timestamp, ("LEADER", "close")
    ]


def test_future_mutation_cannot_change_past_signal():
    panel = _panel()
    cutoff = panel.index[-20]
    before = build_minervini_signals_v1(
        panel.loc[:cutoff],
        instrument_types=_types(),
    )
    panel.loc[panel.index > cutoff, ("LEADER", "close")] *= 10.0

    after = build_minervini_signals_v1(
        panel,
        instrument_types=_types(),
    )

    pd.testing.assert_series_equal(
        before.loc[cutoff],
        after.loc[cutoff],
        check_names=False,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("instrument_type", "NOT_COMMON_STOCK"),
        ("price", "MINIMUM_PRICE"),
        ("liquidity", "MINIMUM_DOLLAR_VOLUME"),
        ("trend", "TREND_TEMPLATE"),
        ("vcp", "VCP_CONTRACTION"),
        ("dry_up", "VOLUME_DRY_UP"),
        ("breakout_volume", "BREAKOUT_VOLUME"),
    ],
)
def test_signal_fails_closed_for_each_required_gate(mutation, reason):
    panel = _panel()
    types = _types()
    timestamp = panel.index[-1]
    if mutation == "instrument_type":
        types["LEADER"] = "ETF"
    elif mutation == "price":
        panel.loc[timestamp, ("LEADER", "close")] = 4.0
    elif mutation == "liquidity":
        panel.loc[:, ("LEADER", "volume")] = 1.0
    elif mutation == "trend":
        panel.loc[timestamp, ("LEADER", "close")] = 50.0
    elif mutation == "vcp":
        panel.loc[panel.index[-21:-1], ("LEADER", "high")] *= 1.20
    elif mutation == "dry_up":
        panel.loc[panel.index[-11:-1], ("LEADER", "volume")] = 300_000.0
    elif mutation == "breakout_volume":
        panel.loc[timestamp, ("LEADER", "volume")] = 100_000.0

    signals = build_minervini_signals_v1(
        panel,
        instrument_types=types,
    )

    assert bool(signals.loc[timestamp, ("LEADER", "signal")]) is False
    assert reason in signals.loc[timestamp, ("LEADER", "rejection_reasons")]


def test_unordered_duplicate_or_incomplete_panel_is_rejected():
    panel = _panel()
    with pytest.raises(ValueError, match="monotonic and unique"):
        build_minervini_signals_v1(
            panel.sort_index(ascending=False),
            instrument_types=_types(),
        )

    incomplete = panel.drop(columns=[("LEADER", "volume")])
    with pytest.raises(ValueError, match="missing required OHLCV"):
        build_minervini_signals_v1(
            incomplete,
            instrument_types=_types(),
        )


def test_config_is_frozen():
    config = MinerviniCoreConfigV1()

    with pytest.raises(Exception):
        config.minimum_price = 1.0
