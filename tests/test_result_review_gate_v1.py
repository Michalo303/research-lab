from __future__ import annotations

import copy

import pandas as pd
import pytest

import research_lab.execution as execution


def _adapter_request() -> dict[str, object]:
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


def _valid_review_request() -> dict[str, object]:
    adapter = execution.build_isolated_real_data_adapter_contract(copy.deepcopy(_adapter_request()))
    strategy_result = execution.build_swing_trend_filtered_pullback_strategy_contract(_strategy_request(adapter))
    bridge = execution.build_strategy_execution_bridge_request(_bridge_request(strategy_result))
    return {
        "version": "result_review_gate_request_v1",
        "adapter_result": adapter,
        "strategy_contract_result": strategy_result,
        "bridge_result": bridge,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_result_review_gate(copy.deepcopy(request))


def test_review_artifact_is_deterministic_for_same_input():
    first = _run(_valid_review_request())
    second = _run(_valid_review_request())

    assert first == second
    assert first["final_review_status"] == "REVIEW_REQUIRED"


def test_valid_isolated_path_produces_review_required_not_promoted():
    result = _run(_valid_review_request())

    assert result["final_review_status"] == "REVIEW_REQUIRED"
    assert result["promotion_performed"] is False
    assert result["registry_write_performed"] is False
    assert result["pass_reason"] == "validated_isolated_path_requires_human_review"
    assert result["failure_reason"] is None


def test_invalid_bridge_result_produces_failed_validation():
    request = _valid_review_request()
    request["bridge_result"]["version"] = "invalid"

    result = _run(request)

    assert result["final_review_status"] == "FAILED_VALIDATION"
    assert result["pass_reason"] is None
    assert "bridge_result.version" in result["failure_reason"]


def test_stable_schema_always_includes_nullable_metric_fields_when_unavailable():
    result = _run(_valid_review_request())

    assert "risk_metrics" in result
    assert "drawdown" in result
    assert "trade_count" in result
    assert "exposure_summary" in result
    assert result["risk_metrics"] is None
    assert result["drawdown"] is None
    assert result["trade_count"] is None
    assert result["exposure_summary"] is None
    assert result["risk_metrics_available"] is False
    assert result["drawdown_available"] is False
    assert result["trade_count_available"] is False
    assert result["exposure_summary_available"] is False


def test_safety_flags_prove_review_only_non_runtime_path():
    result = _run(_valid_review_request())

    assert result["promotion_performed"] is False
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_gate_run"] is False
    assert result["provider_calls_used"] == 0
    assert result["hermes_state_touched"] is False
    assert result["hetzner_state_touched"] is False


def test_artifact_consumes_adapter_strategy_and_bridge_outputs():
    request = _valid_review_request()

    result = _run(request)

    assert result["symbol"] == "SYNTH_QQQ"
    assert result["source_type"] == "isolated_execution_review"
    assert result["adapter_result"]["version"] == "isolated_real_data_adapter_contract_result_v1"
    assert result["strategy_contract_result"]["version"] == "swing_trend_filtered_pullback_strategy_contract_result_v1"
    assert result["bridge_result"]["version"] == "strategy_execution_capability_bridge_result_v1"


def test_optional_isolated_execution_result_populates_metrics_when_available():
    request = _valid_review_request()
    request["isolated_execution_result"] = {
        "version": "risk_overlay_isolated_execution_result_v1",
        "execution_status": "completed",
        "failure_reason": None,
        "metrics": {
            "initial_equity": 100_000.0,
            "final_equity": 104_000.0,
            "total_return": 0.04,
            "max_drawdown": -0.05,
            "trade_count": 2,
        },
        "final_state": {
            "current_equity": 104_000.0,
            "position_units": 0,
            "overlay_state": {"current_gross_exposure_multiplier": 1.0},
        },
        "provider_calls_used": 0,
        "broker_actions_used": 0,
        "registry_write_performed": False,
        "deployment_gate_run": False,
        "promotion_performed": False,
    }

    result = _run(request)

    assert result["final_review_status"] == "REVIEW_REQUIRED"
    assert result["risk_metrics"] == {
        "initial_equity": 100_000.0,
        "final_equity": 104_000.0,
        "total_return": 0.04,
    }
    assert result["drawdown"] == pytest.approx(-0.05)
    assert result["trade_count"] == 2
    assert result["exposure_summary"] == {
        "current_equity": 104_000.0,
        "position_units": 0,
        "current_gross_exposure_multiplier": 1.0,
    }
    assert result["risk_metrics_available"] is True
    assert result["drawdown_available"] is True
    assert result["trade_count_available"] is True
    assert result["exposure_summary_available"] is True
