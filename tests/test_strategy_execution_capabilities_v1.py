from __future__ import annotations

import pandas as pd
import pytest

from research_lab.execution.strategy_execution_capabilities_v1 import (
    get_strategy_execution_capability,
    supported_strategy_execution_builders,
    validate_strategy_execution_capability,
)
from research_lab.strategies.baselines import StrategySpec, build_weights


def _panel() -> pd.DataFrame:
    index = pd.bdate_range("2026-01-01", periods=6)
    close = pd.Series([100.0, 102.0, 101.0, 99.0, 98.0, 103.0], index=index)
    frame = pd.DataFrame(
        {
            ("QQQ", "open"): close,
            ("QQQ", "high"): close * 1.01,
            ("QQQ", "low"): close * 0.99,
            ("QQQ", "close"): close,
            ("QQQ", "volume"): 1_000.0,
        },
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame


def test_unknown_builder_fails_closed():
    capability = get_strategy_execution_capability("unknown_builder")

    assert capability["builder"] == "unknown_builder"
    assert capability["supported_for_risk_overlay_execution"] is False
    assert capability["unsupported_reason"] == "builder is not explicitly supported"


def test_current_supported_builders_are_all_explicitly_unsupported():
    table = supported_strategy_execution_builders()

    assert table
    assert all(item["supported_for_risk_overlay_execution"] is False for item in table)


def test_non_dict_capability_payload_is_rejected():
    with pytest.raises(ValueError, match="JSON object"):
        validate_strategy_execution_capability("not-a-dict")


def test_missing_required_capability_field_is_rejected():
    payload = get_strategy_execution_capability("unknown_builder")
    payload.pop("emits_entry_events")

    with pytest.raises(ValueError, match="emits_entry_events"):
        validate_strategy_execution_capability(payload)


def test_unknown_extra_capability_field_is_rejected():
    payload = get_strategy_execution_capability("unknown_builder")
    payload["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        validate_strategy_execution_capability(payload)


def test_non_boolean_capability_flag_is_rejected():
    payload = get_strategy_execution_capability("unknown_builder")
    payload["supports_position_caps"] = "false"

    with pytest.raises(ValueError, match="supports_position_caps"):
        validate_strategy_execution_capability(payload)


def test_blank_builder_identifier_is_rejected():
    with pytest.raises(ValueError, match="builder is required"):
        get_strategy_execution_capability("  ")


def test_swing_trend_filtered_pullback_remains_explicitly_unsupported():
    capability = get_strategy_execution_capability("swing_trend_filtered_pullback")

    assert capability["supported_for_risk_overlay_execution"] is False
    assert "synthetic-only contract helper exists" in capability["unsupported_reason"]
    assert capability["emits_entry_events"] is True
    assert capability["emits_exit_events"] is True
    assert capability["emits_rebalance_events"] is False
    assert capability["exposes_protective_exit"] is True
    assert capability["exposes_per_unit_loss_distance"] is True


def test_existing_strategy_output_remains_unchanged():
    spec = StrategySpec(
        family="SWING",
        asset_class="ETF",
        timeframe="1D",
        short_name="QUEUE_PULLBACK",
        hypothesis="test",
        parameters={
            "symbol": "QQQ",
            "fast_sma": 2,
            "slow_sma": 3,
            "rsi_entry": 40,
            "rsi_exit": 58,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
        rules="test",
        builder="swing_trend_filtered_pullback",
    )
    panel = _panel()

    before = build_weights(spec, panel)
    _ = get_strategy_execution_capability("swing_trend_filtered_pullback")
    after = build_weights(spec, panel)

    pd.testing.assert_frame_equal(before, after)
