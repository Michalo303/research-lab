from __future__ import annotations

import copy

import pandas as pd
import pytest

import research_lab.execution as execution
from research_lab.execution.strategy_execution_capability_bridge_v1 import (
    build_strategy_execution_bridge_request,
)


def _request() -> dict[str, object]:
    closes = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
        113.0,
        114.0,
        111.0,
        110.0,
        109.0,
        111.0,
        112.0,
        111.0,
        112.0,
        106.0,
    ]
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    bars = []
    ranges = [2.0] * len(closes)
    ranges[20] = 6.0
    ranges[21] = 8.0
    ranges[22] = 10.0
    for ts, close, span in zip(dates, closes, ranges, strict=True):
        bars.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
            }
        )
    return {
        "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
        "symbol": "SYNTH_QQQ",
        "synthetic_bars": bars,
        "strategy_parameters": {
            "fast_sma": 3,
            "slow_sma": 5,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_swing_trend_filtered_pullback_strategy_contract(copy.deepcopy(request))


def _bridge_request(contract_result: dict[str, object]) -> dict[str, object]:
    return {
        "version": "strategy_execution_capability_bridge_request_v1",
        "strategy_builder": "swing_trend_filtered_pullback",
        "symbol": contract_result["symbol"],
        "synthetic_bars": contract_result["synthetic_bars"],
        "strategy_signal_plan": contract_result["strategy_signal_plan"],
        "provenance": {"source": "contract_bridge_test"},
    }


def test_helper_emits_entry_exit_and_rebalance_contract_signals():
    result = _run(_request())

    assert result["strategy_builder"] == "swing_trend_filtered_pullback"
    assert result["production_runtime_supported"] is False
    assert result["supported_for_risk_overlay_execution"] is False
    signal_types = [item["signal_type"] for item in result["strategy_signal_plan"]]
    assert signal_types[0] == "entry"
    assert signal_types[-1] == "exit"
    assert "rebalance" not in signal_types


def test_entry_contract_includes_protective_exit_and_valid_per_unit_loss():
    result = _run(_request())

    entry_contract = result["signal_contracts"][0]
    exit_contract = result["signal_contracts"][-1]

    assert entry_contract["signal_type"] == "entry"
    assert entry_contract["protective_exit"]["per_unit_loss_to_protective_exit"] > 0
    assert exit_contract["signal_type"] == "exit"
    assert exit_contract["protective_exit"] is None


def test_stop_refreshes_are_reported_without_emitting_rebalance_when_exposure_is_unchanged():
    result = _run(_request())

    assert result["active_contract_refreshes"]
    assert all(item["refresh_type"] == "protective_exit_update" for item in result["active_contract_refreshes"])
    assert all(item["target_exposure"] == pytest.approx(0.5) for item in result["active_contract_refreshes"])
    assert all(item["protective_exit"]["per_unit_loss_to_protective_exit"] > 0 for item in result["active_contract_refreshes"])


def test_output_can_feed_existing_bridge_path_without_rebalance_for_stop_only_refreshes():
    result = _run(_request())

    bridge = build_strategy_execution_bridge_request(_bridge_request(result))

    assert bridge["strategy_builder"] == "swing_trend_filtered_pullback"
    event_types = [item["event_type"] for item in bridge["strategy_events"]]
    assert event_types[0] == "entry"
    assert event_types[-1] == "exit"
    assert set(bridge["protective_exits_by_event_id"]) == {
        item["signal_id"] for item in result["strategy_signal_plan"] if item["signal_type"] != "exit"
    }


def test_short_synthetic_series_uses_bounded_indicator_lookback_for_review_only_e2e_composition():
    request = _request()
    request["synthetic_bars"] = [
        {"timestamp": "2026-01-01T21:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"timestamp": "2026-01-02T21:00:00Z", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0},
        {"timestamp": "2026-01-03T21:00:00Z", "open": 102.0, "high": 105.0, "low": 101.0, "close": 104.0},
        {"timestamp": "2026-01-06T21:00:00Z", "open": 104.0, "high": 105.0, "low": 102.0, "close": 103.0},
        {"timestamp": "2026-01-07T21:00:00Z", "open": 103.0, "high": 104.0, "low": 100.0, "close": 101.0},
        {"timestamp": "2026-01-08T21:00:00Z", "open": 101.0, "high": 102.0, "low": 98.0, "close": 99.0},
        {"timestamp": "2026-01-09T21:00:00Z", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
        {"timestamp": "2026-01-10T21:00:00Z", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
    ]
    request["strategy_parameters"]["fast_sma"] = 2
    request["strategy_parameters"]["slow_sma"] = 3
    request["strategy_parameters"]["max_exposure"] = 1.0

    result = _run(request)

    assert result["strategy_signal_plan"]
    assert result["strategy_signal_plan"][0]["signal_type"] == "entry"
    assert result["production_runtime_supported"] is False


def test_missing_protective_exit_fails_before_executor_boundary():
    result = _run(_request())
    request = _bridge_request(result)
    del request["strategy_signal_plan"][0]["protective_exit"]

    with pytest.raises(ValueError, match="protective_exit is required for entry and rebalance signals"):
        build_strategy_execution_bridge_request(request)


def test_malformed_protective_exit_fails_before_executor_boundary():
    result = _run(_request())
    request = _bridge_request(result)
    request["strategy_signal_plan"][0]["protective_exit"]["unexpected"] = True

    with pytest.raises(ValueError, match="protective_exit contains unknown field"):
        build_strategy_execution_bridge_request(request)
