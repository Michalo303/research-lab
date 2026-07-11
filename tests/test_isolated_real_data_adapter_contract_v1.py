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
    for index, (ts, close, span) in enumerate(zip(dates, closes, ranges, strict=True), start=1):
        bars.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
                "volume": 1_000_000 + index,
            }
        )
    return {
        "version": "isolated_real_data_adapter_contract_request_v1",
        "symbol": "qqq",
        "input_bars": bars,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_isolated_real_data_adapter_contract(copy.deepcopy(request))


def _strategy_request(adapter_result: dict[str, object]) -> dict[str, object]:
    return {
        "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
        "symbol": adapter_result["symbol"],
        "synthetic_bars": adapter_result["synthetic_bars"],
        "strategy_parameters": {
            "fast_sma": 3,
            "slow_sma": 5,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
        "provenance": {"source": "adapter_strategy_test"},
    }


def _bridge_request(strategy_result: dict[str, object]) -> dict[str, object]:
    return {
        "version": "strategy_execution_capability_bridge_request_v1",
        "strategy_builder": "swing_trend_filtered_pullback",
        "symbol": strategy_result["symbol"],
        "synthetic_bars": strategy_result["synthetic_bars"],
        "strategy_signal_plan": strategy_result["strategy_signal_plan"],
        "provenance": {"source": "adapter_bridge_test"},
    }


def test_deterministic_normalization_of_ohlcv_like_bars_into_synthetic_bars():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["symbol"] == "SYNTH_QQQ"
    assert first["source_symbol"] == "QQQ"
    assert set(first["synthetic_bars"][0]) == {"timestamp", "open", "high", "low", "close"}


def test_volume_is_dropped_for_bridge_compatible_output():
    result = _run(_request())

    assert all("volume" not in bar for bar in result["synthetic_bars"])
    assert result["synthetic_bars"][0]["close"] == pytest.approx(100.0)


@pytest.mark.parametrize("field", ["timestamp", "open", "high", "low", "close"])
def test_missing_required_fields_are_rejected(field: str):
    request = _request()
    del request["input_bars"][0][field]

    with pytest.raises(ValueError, match=field):
        _run(request)


def test_unordered_timestamps_are_rejected():
    request = _request()
    request["input_bars"][1]["timestamp"] = request["input_bars"][0]["timestamp"]

    with pytest.raises(ValueError, match="strictly increasing"):
        _run(request)


def test_invalid_ohlc_consistency_is_rejected():
    request = _request()
    request["input_bars"][0]["high"] = request["input_bars"][0]["close"] - 1.0

    with pytest.raises(ValueError, match="high must be greater than or equal"):
        _run(request)


def test_provider_runtime_and_deployment_fields_are_rejected():
    for field in ("provider", "runtime", "deployment", "broker", "registry", "hermes", "hetzner"):
        request = _request()
        request[field] = True
        with pytest.raises(ValueError, match="request contains unknown field"):
            _run(request)


def test_adapter_output_feeds_swing_contract_helper():
    adapter = _run(_request())

    strategy_result = execution.build_swing_trend_filtered_pullback_strategy_contract(_strategy_request(adapter))

    assert strategy_result["symbol"] == "SYNTH_QQQ"
    assert strategy_result["strategy_signal_plan"][0]["signal_type"] == "entry"
    assert strategy_result["strategy_signal_plan"][-1]["signal_type"] == "exit"


def test_strategy_output_from_adapter_feeds_existing_bridge_path():
    adapter = _run(_request())
    strategy_result = execution.build_swing_trend_filtered_pullback_strategy_contract(_strategy_request(adapter))

    bridge = build_strategy_execution_bridge_request(_bridge_request(strategy_result))

    assert bridge["strategy_builder"] == "swing_trend_filtered_pullback"
    assert bridge["strategy_events"][0]["event_type"] == "entry"
    assert bridge["strategy_events"][-1]["event_type"] == "exit"


def test_safety_flags_prove_no_external_or_runtime_actions():
    result = _run(_request())

    assert result["production_runtime_supported"] is False
    assert result["supported_for_risk_overlay_execution"] is False
    assert result["real_data_used"] is True
    assert result["synthetic_data_used"] is False
    assert result["safe_flags"] == {
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "backtest_run": False,
    }
    assert result["provenance"]["adapter_input_mode"] == "local_pre_supplied_bars"
    assert result["provenance"]["provider_fetch_performed"] is False
