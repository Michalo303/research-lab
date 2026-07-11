from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

import research_lab.execution as execution


MODULE_PATH = Path("research_lab/execution/qlib_isolated_evaluator_v1.py")


def _bars() -> list[dict[str, object]]:
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
    return bars


def _review_artifact() -> dict[str, object]:
    adapter = execution.build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": "qqq",
            "input_bars": [{**bar, "volume": 1_000_000} for bar in _bars()],
            "provenance": {"source": "unit_test"},
        }
    )
    strategy_result = execution.build_swing_trend_filtered_pullback_strategy_contract(
        {
            "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
            "symbol": adapter["symbol"],
            "synthetic_bars": adapter["synthetic_bars"],
            "strategy_parameters": {
                "fast_sma": 2,
                "slow_sma": 3,
                "rsi_entry": 80.0,
                "rsi_exit": 85.0,
                "atr_stop": 2.0,
                "max_exposure": 0.5,
            },
            "provenance": {"source": "unit_test"},
        }
    )
    bridge = execution.build_strategy_execution_bridge_request(
        {
            "version": "strategy_execution_capability_bridge_request_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": strategy_result["symbol"],
            "synthetic_bars": strategy_result["synthetic_bars"],
            "strategy_signal_plan": strategy_result["strategy_signal_plan"],
            "provenance": {"source": "unit_test"},
        }
    )
    return execution.build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": adapter,
            "strategy_contract_result": strategy_result,
            "bridge_result": bridge,
            "provenance": {"source": "unit_test"},
        }
    )


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.run_qlib_isolated_evaluator(copy.deepcopy(request))


def test_deterministic_unavailable_result_when_qlib_not_installed():
    if importlib.util.find_spec("qlib") is not None:
        pytest.skip("local environment already has qlib installed")
    request = {
        "version": "qlib_isolated_evaluator_request_v1",
        "input_type": "review_artifact",
        "review_artifact": _review_artifact(),
        "evaluation_mode": "availability_check",
        "provenance": {"source": "unit_test"},
    }

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["qlib_available"] is False
    assert first["evaluation_run"] is False
    assert first["metrics"] is None
    assert first["failure_reason"] == "qlib_unavailable"
    assert first["final_status"] == "UNAVAILABLE"


def test_deterministic_local_evaluation_result_when_local_stub_path_is_used():
    request = {
        "version": "qlib_isolated_evaluator_request_v1",
        "input_type": "normalized_bars",
        "symbol": "SYNTH_QQQ",
        "normalized_bars": _bars(),
        "evaluation_mode": "deterministic_local",
        "provenance": {"source": "unit_test"},
    }

    result = _run(request)

    assert result["evaluation_run"] is True
    assert result["final_status"] == "COMPLETED_LOCAL_STUB"
    assert result["metrics"] == {
        "bar_count": 23,
        "first_timestamp": "2026-01-01",
        "last_timestamp": "2026-02-02",
        "first_close": 100.0,
        "last_close": 106.0,
        "simple_return": 0.06,
    }


def test_no_provider_registry_broker_deployment_hermes_hetzner_or_promotion_actions():
    result = _run(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "normalized_bars",
            "symbol": "SYNTH_QQQ",
            "normalized_bars": _bars(),
            "evaluation_mode": "deterministic_local",
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["provider_calls_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_gate_run"] is False
    assert result["hermes_state_touched"] is False
    assert result["hetzner_state_touched"] is False
    assert result["promotion_performed"] is False
    assert result["production_runtime_supported"] is False


def test_stable_schema_always_includes_metrics_failure_reason_and_final_status():
    result = _run(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "normalized_bars",
            "symbol": "SYNTH_QQQ",
            "normalized_bars": _bars(),
            "evaluation_mode": "deterministic_local",
            "provenance": {"source": "unit_test"},
        }
    )

    assert "metrics" in result
    assert "failure_reason" in result
    assert "final_status" in result


def test_result_can_attach_to_review_gate_artifact_without_promotion():
    review_artifact = _review_artifact()
    result = _run(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "review_artifact",
            "review_artifact": review_artifact,
            "evaluation_mode": "deterministic_local",
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["input_source_type"] == "review_artifact"
    assert result["source_review_candidate_id"] == review_artifact["candidate_id"]
    assert result["promotion_performed"] is False


def test_malformed_input_fails_safely():
    request = {
        "version": "qlib_isolated_evaluator_request_v1",
        "input_type": "normalized_bars",
        "symbol": "SYNTH_QQQ",
        "normalized_bars": [{"timestamp": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5}],
        "evaluation_mode": "deterministic_local",
        "provenance": {"source": "unit_test"},
    }

    result = _run(request)

    assert result["evaluation_run"] is False
    assert result["final_status"] == "FAILED_VALIDATION"
    assert "normalized_bars" in result["failure_reason"]


def test_module_does_not_import_forbidden_modules():
    forbidden_roots = (
        "research_lab.provider",
        "research_lab.providers",
        "research_lab.broker",
        "research_lab.hermes",
        "research_lab.registry",
        "research_lab.deployment",
        "research_lab.orchestration.daily",
        "research_lab.backtest",
        "qlib",
        "socket",
        "subprocess",
        "requests",
        "aiohttp",
        "urllib",
        "http",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for forbidden in forbidden_roots:
        assert forbidden not in imports
