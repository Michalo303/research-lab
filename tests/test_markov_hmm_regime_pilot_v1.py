from __future__ import annotations

import ast
import copy
from pathlib import Path

import pandas as pd
import pytest

import research_lab.execution as execution


MODULE_PATH = Path("research_lab/execution/markov_hmm_regime_pilot_v1.py")


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
    return execution.run_markov_hmm_regime_pilot(copy.deepcopy(request))


def test_deterministic_output_for_same_input():
    request = {
        "version": "markov_hmm_regime_pilot_request_v1",
        "input_bars": _bars(),
        "parameters": {"lookback": 3},
        "provenance": {"source": "unit_test"},
    }

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["final_status"] == "COMPLETED"


def test_malformed_input_fails_safely():
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": [{"timestamp": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5}],
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["final_status"] == "FAILED_VALIDATION"
    assert result["failure_reason"] is not None


def test_no_provider_registry_broker_deployment_hermes_hetzner_or_promotion_actions():
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": _bars(),
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


def test_no_production_runtime_enablement():
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": _bars(),
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["production_runtime_supported"] is False


def test_stable_schema_on_pass_and_fail():
    passed = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": _bars(),
            "provenance": {"source": "unit_test"},
        }
    )
    failed = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": [{"timestamp": "2026-01-02", "open": 1.0}],
            "provenance": {"source": "unit_test"},
        }
    )

    for result in (passed, failed):
        assert "regime_pilot_version" in result
        assert "regime_model_type" in result
        assert "input_hash" in result
        assert "regime_labels" in result
        assert "regime_summary" in result
        assert "drawdown_timing_hint" in result
        assert "exposure_timing_hint" in result
        assert "final_status" in result
        assert "failure_reason" in result


def test_can_consume_result_review_gate_artifact():
    review_artifact = _review_artifact()
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "review_artifact": review_artifact,
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["source_review_candidate_id"] == review_artifact["candidate_id"]
    assert result["final_status"] == "COMPLETED"


def test_can_attach_output_back_to_review_only_pipeline_without_promotion():
    review_artifact = _review_artifact()
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "review_artifact": review_artifact,
            "provenance": {"source": "unit_test"},
        }
    )

    attachment = {
        "review_artifact_candidate_id": review_artifact["candidate_id"],
        "regime_pilot_output": result,
        "promotion_performed": False,
    }

    assert attachment["regime_pilot_output"]["final_status"] == "COMPLETED"
    assert attachment["promotion_performed"] is False


def test_regime_labels_are_deterministic_and_length_aligned_with_bars():
    bars = _bars()
    result = _run(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": bars,
            "parameters": {"lookback": 2},
            "provenance": {"source": "unit_test"},
        }
    )

    assert len(result["regime_labels"]) == len(bars)
    assert [item["timestamp"] for item in result["regime_labels"]] == [item["timestamp"] for item in bars]
    assert set(item["regime_label"] for item in result["regime_labels"]) <= {"bull", "bear", "sideways"}


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
        "hmmlearn",
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
