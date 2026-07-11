from __future__ import annotations

import copy

import pandas as pd
import pytest

import research_lab.execution as execution
from research_lab.execution.strategy_robustness_review_contract_v1 import (
    build_strategy_robustness_review_contract,
)


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
    return bars


def _strategy_contract_result() -> dict[str, object]:
    adapter = execution.build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": "abc",
            "input_bars": _bars(),
            "provenance": {"source": "unit_test"},
        }
    )
    return execution.build_swing_trend_filtered_pullback_strategy_contract(
        {
            "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
            "symbol": adapter["symbol"],
            "synthetic_bars": adapter["synthetic_bars"],
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
    )


def _baseline_review_artifact() -> dict[str, object]:
    strategy_contract = _strategy_contract_result()
    bridge = execution.build_strategy_execution_bridge_request(
        {
            "version": "strategy_execution_capability_bridge_request_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": strategy_contract["symbol"],
            "synthetic_bars": strategy_contract["synthetic_bars"],
            "strategy_signal_plan": strategy_contract["strategy_signal_plan"],
            "provenance": {"source": "unit_test"},
        }
    )
    isolated_execution_result = {
        "version": "risk_overlay_isolated_execution_result_v1",
        "execution_status": "completed",
        "failure_reason": None,
        "metrics": {
            "initial_equity": 100_000.0,
            "final_equity": 104_000.0,
            "total_return": 0.04,
            "max_drawdown": -0.08,
            "trade_count": 3,
        },
        "final_state": {
            "current_equity": 104_000.0,
            "position_units": 0,
            "overlay_state": {"current_gross_exposure_multiplier": 1.0},
        },
        "input_sha256": "isolated-execution-input",
    }
    return execution.build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": {
                "version": "isolated_real_data_adapter_contract_result_v1",
                "symbol": strategy_contract["symbol"],
                "production_runtime_supported": False,
                "output_payload_sha256": "adapter-output",
                "safe_flags": {"provider_calls_used": 0},
            },
            "strategy_contract_result": strategy_contract,
            "bridge_result": bridge,
            "isolated_execution_result": isolated_execution_result,
            "provenance": {"source": "unit_test"},
        }
    )


def _request() -> dict[str, object]:
    return {
        "version": "strategy_robustness_review_contract_request_v1",
        "strategy_contract": _strategy_contract_result(),
        "baseline_review_artifact": _baseline_review_artifact(),
        "parameter_schema": {
            "parameters": [
                {"name": "fast_sma", "type": "int", "baseline": 3, "tested_values": [2, 3, 4]},
                {"name": "slow_sma", "type": "int", "baseline": 5, "tested_values": [5, 6, 7]},
                {"name": "rsi_entry", "type": "float", "baseline": 80.0, "tested_values": [75.0, 80.0, 82.0]},
            ]
        },
        "evaluation_window_metadata": {
            "walk_forward_method": "true_rolling_oos",
            "window_count": 4,
            "pass_rate": 0.75,
            "effective_sample_size": 120,
        },
        "experiment_trial_metadata": {
            "trial_count": 8,
            "selection_mode": "bounded_grid",
            "selection_bias_controls": {
                "deflated_sharpe_applied": True,
                "pbo_checked": True,
            },
        },
        "validated_knihomol_evidence": {
            "notes": [
                {
                    "note_id": "KNIH-001",
                    "status": "validated",
                    "topic": "walk_forward_fail",
                    "summary": "Prefer strong walk-forward robustness and sample discipline.",
                    "supports": ["walk_forward", "selection_bias"],
                }
            ]
        },
        "robustness_policy": {
            "min_walk_forward_windows": 3,
            "min_walk_forward_pass_rate": 0.67,
            "max_drawdown": -0.20,
            "max_trial_count": 12,
            "max_parameter_count": 4,
        },
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_strategy_robustness_review_contract(copy.deepcopy(request))


def test_pass_result_is_deterministic_and_review_only():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["robustness_status"] == "PASS"
    assert first["blocking_reasons"] == []
    assert first["knowledge_note_ids_used"] == ["KNIH-001"]
    assert first["provider_calls_used"] == 0
    assert first["promotion_performed"] is False
    assert first["production_runtime_supported"] is False


def test_pass_with_simplification_when_parameter_count_exceeds_budget():
    request = _request()
    request["parameter_schema"]["parameters"].append(
        {"name": "atr_stop", "type": "float", "baseline": 2.0, "tested_values": [1.5, 2.0, 2.5]}
    )
    request["parameter_schema"]["parameters"].append(
        {"name": "max_exposure", "type": "float", "baseline": 0.5, "tested_values": [0.25, 0.5, 0.75]}
    )

    result = _run(request)

    assert result["robustness_status"] == "PASS_WITH_SIMPLIFICATION"
    assert "reduce_parameter_surface_area" in result["required_parameter_checks"]
    assert result["complexity_budget"]["within_budget"] is False


def test_revise_when_walk_forward_evidence_is_insufficient():
    request = _request()
    request["evaluation_window_metadata"]["window_count"] = 2
    request["evaluation_window_metadata"]["pass_rate"] = 0.5

    result = _run(request)

    assert result["robustness_status"] == "REVISE"
    assert "walk_forward_evidence_below_policy" in result["blocking_reasons"]
    assert "increase_walk_forward_windows" in result["required_walk_forward_checks"]


def test_reject_overfit_when_trial_count_and_evidence_show_selection_bias():
    request = _request()
    request["experiment_trial_metadata"]["trial_count"] = 25
    request["experiment_trial_metadata"]["selection_bias_controls"]["pbo_checked"] = False
    request["validated_knihomol_evidence"]["notes"].append(
        {
            "note_id": "KNIH-OVERFIT-1",
            "status": "validated",
            "topic": "selection_bias",
            "summary": "High trial counts without bias controls often indicate overfit selection.",
            "supports": ["selection_bias", "overfit"],
        }
    )

    result = _run(request)

    assert result["robustness_status"] == "REJECT_OVERFIT"
    assert "overfit_risk_detected" in result["blocking_reasons"]
    assert result["knowledge_note_ids_used"] == ["KNIH-001", "KNIH-OVERFIT-1"]


def test_unknown_fields_fail_closed():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)
