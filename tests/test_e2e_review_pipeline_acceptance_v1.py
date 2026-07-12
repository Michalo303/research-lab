from __future__ import annotations

import copy

import pandas as pd
import pytest

from research_lab.execution.e2e_review_pipeline_acceptance_v1 import (
    run_e2e_review_pipeline_acceptance,
)
from research_lab.execution.result_review_gate_v1 import (
    build_result_review_gate,
)


def _input_bars() -> list[dict[str, object]]:
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
    bars: list[dict[str, object]] = []
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


def _request() -> dict[str, object]:
    return {
        "version": "e2e_review_pipeline_acceptance_request_v1",
        "strategy_identity": {
            "strategy_id": "STFP_BASE",
            "strategy_builder": "swing_trend_filtered_pullback",
            "baseline_variant_id": "BASELINE",
        },
        "symbol": "abc",
        "input_bars": _input_bars(),
        "strategy_parameters": {
            "fast_sma": 3,
            "slow_sma": 5,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
        "executor_config": {
            "runtime_contract_version": "risk_execution_contract_v1",
            "initial_equity": 100_000.0,
            "fixed_fractional_config": {"selected_risk_per_trade_pct": 1.0},
            "strategy_position_cap": 100_000.0,
            "portfolio_exposure_cap": 100_000.0,
            "circuit_breaker_thresholds": [{"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75}],
            "reentry_rule": {"type": "equity_recovery", "recovery_from_peak_pct": 1.0, "cooldown_days": 1},
            "fractional_units_allowed": False,
            "output_mode": "full_result",
        },
        "strategy_rule_definitions": [
            {"rule_id": "protective_stop", "rule_role": "risk_safety", "description": "Protective stop must stay active."},
            {"rule_id": "legacy_filter", "rule_role": "alpha", "description": "Legacy alpha filter."},
            {"rule_id": "note_tag", "rule_role": "alpha", "description": "Decorative rule tag."},
        ],
        "robustness_review_inputs": {
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
                "effective_sample_size": 160,
            },
            "experiment_trial_metadata": {
                "trial_count": 12,
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
                        "topic": "selection_bias",
                        "summary": "Prefer simpler robust variants once all safety controls hold.",
                        "supports": ["selection_bias", "risk_controls"],
                    }
                ]
            },
            "robustness_policy": {
                "min_walk_forward_windows": 3,
                "min_walk_forward_pass_rate": 0.67,
                "max_drawdown": -0.20,
                "max_trial_count": 16,
                "max_parameter_count": 4,
            },
        },
        "ablation_inputs": {
            "baseline_variant": {
                "strategy_id": "STFP_BASE",
                "evaluation_artifact": {
                    "total_return": 0.04,
                    "max_drawdown": -0.08,
                    "final_review_status": "REVIEW_REQUIRED",
                },
            },
            "ablated_variants": [
                {
                    "variant_id": "BASELINE",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "note_tag", "rule_role": "alpha"},
                    "evaluation_artifact": {
                        "total_return": 0.0401,
                        "max_drawdown": -0.0801,
                        "final_review_status": "REVIEW_REQUIRED",
                    },
                },
                {
                    "variant_id": "SIMPLER_SAFE",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "legacy_filter", "rule_role": "alpha"},
                    "evaluation_artifact": {
                        "total_return": 0.039,
                        "max_drawdown": -0.081,
                        "final_review_status": "REVIEW_REQUIRED",
                    },
                },
                {
                    "variant_id": "RISKY",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "protective_stop", "rule_role": "risk_safety"},
                    "evaluation_artifact": {
                        "total_return": 0.05,
                        "max_drawdown": -0.11,
                        "final_review_status": "REVIEW_REQUIRED",
                    },
                },
            ],
            "ablation_policy": {
                "return_tolerance": 0.002,
                "drawdown_tolerance": 0.002,
            },
        },
        "parameter_stability_inputs": [
            {
                "version": "parameter_stability_evaluator_request_v1",
                "parameter_name": "fast_sma",
                "baseline_value": 3,
                "one_dimensional_results": [
                    {"value": 1, "score": 0.20},
                    {"value": 2, "score": 0.39},
                    {"value": 3, "score": 0.40},
                    {"value": 4, "score": 0.39},
                    {"value": 5, "score": 0.21},
                ],
                "pair_interactions": [],
                "stability_policy": {
                    "plateau_tolerance": 0.02,
                    "edge_buffer": 1,
                    "spike_penalty_threshold": 0.08,
                },
            },
            {
                "version": "parameter_stability_evaluator_request_v1",
                "parameter_name": "slow_sma",
                "baseline_value": 5,
                "one_dimensional_results": [
                    {"value": 3, "score": 0.10},
                    {"value": 4, "score": 0.20},
                    {"value": 5, "score": 0.40},
                    {"value": 6, "score": 0.39},
                    {"value": 7, "score": 0.15},
                ],
                "pair_interactions": [],
                "stability_policy": {
                    "plateau_tolerance": 0.02,
                    "edge_buffer": 1,
                    "spike_penalty_threshold": 0.08,
                },
            },
        ],
        "robustness_decision_inputs": {
            "walk_forward_fold_evidence": {
                "fold_results": [
                    {"fold_id": "WF-1", "passed": True, "failure_reasons": []},
                    {"fold_id": "WF-2", "passed": True, "failure_reasons": []},
                    {"fold_id": "WF-3", "passed": True, "failure_reasons": []},
                ],
            },
            "effective_sample_metadata": {
                "effective_sample_size": 160,
                "minimum_required": 100,
                "passed": True,
            },
            "trial_count_metadata": {
                "total_trials": 12,
                "complete_accounting": True,
                "bounded_search": True,
                "selection_mode": "bounded_grid",
            },
            "deflated_sharpe_result": {
                "available": True,
                "passed": True,
                "observed_value": 0.32,
                "minimum_required": 0.10,
            },
            "pbo_cscv_result": {
                "available": True,
                "passed": True,
                "observed_value": 0.08,
                "maximum_allowed": 0.20,
            },
            "drawdown_stress_result": {
                "available": True,
                "passed": True,
                "stressed_max_drawdown": -0.11,
                "maximum_allowed_drawdown": -0.20,
            },
            "complexity_variants": {
                "variants": [
                    {
                        "variant_id": "BASELINE",
                        "parameter_count": 4,
                        "complexity_score": 4.0,
                        "required_risk_controls_preserved": True,
                    },
                    {
                        "variant_id": "SIMPLER_SAFE",
                        "parameter_count": 3,
                        "complexity_score": 3.0,
                        "required_risk_controls_preserved": True,
                    },
                    {
                        "variant_id": "RISKY",
                        "parameter_count": 2,
                        "complexity_score": 2.0,
                        "required_risk_controls_preserved": False,
                    },
                ],
            },
            "decision_policy": {
                "missing_effective_sample_action": "REVISE",
                "failed_effective_sample_action": "REVISE",
                "missing_dsr_action": "REVISE",
                "failed_dsr_action": "REJECT_OVERFIT",
                "missing_pbo_action": "REVISE",
                "failed_pbo_action": "REJECT_OVERFIT",
                "missing_drawdown_stress_action": "REVISE",
                "failed_drawdown_stress_action": "REJECT_RISK",
            },
        },
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_e2e_review_pipeline_acceptance(copy.deepcopy(request))


def test_full_local_review_pipeline_is_deterministic_and_review_only():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["version"] == "e2e_review_pipeline_acceptance_result_v1"
    assert first["adapter_result"]["symbol"] == "SYNTH_ABC"
    assert first["strategy_contract_result"]["production_runtime_supported"] is False
    assert first["strategy_contract_result"]["supported_for_risk_overlay_execution"] is False
    assert first["bridge_result"]["version"] == "strategy_execution_capability_bridge_result_v1"
    assert first["bridge_executor_result"]["synthetic_data_used"] is True
    assert first["bridge_executor_result"]["real_data_used"] is False
    assert first["review_artifact"]["final_review_status"] == "REVIEW_REQUIRED"
    assert first["review_artifact"]["promotion_performed"] is False
    assert first["robustness_review_result"]["robustness_status"] == "PASS"
    assert first["ablation_result"]["version"] == "deterministic_ablation_evaluator_result_v1"
    assert len(first["parameter_stability_results"]) == 2
    assert first["robustness_decision_result"]["decision_status"] == "PASS_WITH_SIMPLIFICATION"
    assert first["qlib_evaluation"]["final_status"] == "COMPLETED_LOCAL_STUB"
    assert first["regime_pilot_result"]["final_status"] == "COMPLETED"
    assert first["rd_agent_proposal"]["review_status"] == "REVIEW_REQUIRED"
    assert first["rd_agent_proposal"]["proposal_run"] is True
    assert first["rd_agent_proposal"]["reviewed_robustness_context"]["decision_status"] == "PASS_WITH_SIMPLIFICATION"
    assert first["rd_agent_proposal"]["reviewed_robustness_context"]["required_risk_safety_rules"] == [
        {"rule_id": "protective_stop", "rule_role": "risk_safety", "description": "Protective stop must stay active."}
    ]
    assert all(isinstance(item, str) for item in first["rd_agent_proposal"]["candidate_hypotheses"])

    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_gate_run"] is False
    assert first["hermes_state_touched"] is False
    assert first["hetzner_state_touched"] is False
    assert first["promotion_performed"] is False
    assert first["generated_code_executed"] is False
    assert first["external_data_used"] is False
    assert first["production_runtime_supported"] is False
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_failure_at_review_boundary_is_safe_and_non_promoting():
    pipeline = _run(_request())

    failed_review = build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": pipeline["adapter_result"],
            "strategy_contract_result": pipeline["strategy_contract_result"],
            "bridge_result": {**pipeline["bridge_result"], "version": "invalid"},
            "isolated_execution_result": pipeline["isolated_execution_result"],
            "provenance": {"source": "unit_test"},
        }
    )

    assert failed_review["final_review_status"] == "FAILED_VALIDATION"
    assert "bridge_result.version" in failed_review["failure_reason"]
    assert failed_review["promotion_performed"] is False
    assert failed_review["registry_write_performed"] is False
    assert failed_review["broker_actions_used"] == 0
    assert failed_review["deployment_gate_run"] is False
    assert failed_review["provider_calls_used"] == 0
    assert failed_review["hermes_state_touched"] is False
    assert failed_review["hetzner_state_touched"] is False


def test_overfit_rejection_blocks_rd_agent_proposal_authority_and_preserves_context():
    request = _request()
    request["robustness_decision_inputs"]["deflated_sharpe_result"]["passed"] = False

    result = _run(request)

    assert result["robustness_decision_result"]["decision_status"] == "REJECT_OVERFIT"
    assert result["rd_agent_proposal"]["proposal_run"] is False
    assert result["rd_agent_proposal"]["review_status"] == "REJECTED"
    assert result["rd_agent_proposal"]["reviewed_robustness_context"]["deflated_sharpe_findings"]["passed"] is False


def test_failed_pbo_is_preserved_in_downstream_review_context():
    request = _request()
    request["robustness_decision_inputs"]["pbo_cscv_result"]["passed"] = False

    result = _run(request)

    assert result["robustness_decision_result"]["decision_status"] == "REJECT_OVERFIT"
    assert result["rd_agent_proposal"]["reviewed_robustness_context"]["pbo_cscv_findings"]["passed"] is False


def test_malformed_parameter_stability_input_fails_closed():
    request = _request()
    request["parameter_stability_inputs"][0]["one_dimensional_results"][0].pop("score")

    with pytest.raises(ValueError, match="score"):
        _run(request)


def test_isolated_spike_blocks_acceptance():
    request = _request()
    request["parameter_stability_inputs"][0]["one_dimensional_results"] = [
        {"value": 1, "score": 0.10},
        {"value": 2, "score": 0.11},
        {"value": 3, "score": 0.40},
        {"value": 4, "score": 0.12},
        {"value": 5, "score": 0.10},
    ]

    result = _run(request)

    assert result["robustness_decision_result"]["decision_status"] == "REJECT_OVERFIT"
    assert result["rd_agent_proposal"]["reviewed_robustness_context"]["isolated_spike_findings"] == [
        "isolated_spike_detected:fast_sma"
    ]


def test_required_for_risk_safety_rule_cannot_be_removed():
    result = _run(_request())

    assert result["robustness_decision_result"]["rejected_variants"] == [
        {"variant_id": "RISKY", "reason": "required_risk_safety_rule_removed"}
    ]
    assert result["rd_agent_proposal"]["reviewed_robustness_context"]["required_risk_safety_rules"][0]["rule_id"] == "protective_stop"


def test_failed_stressed_drawdown_produces_risk_rejection():
    request = _request()
    request["robustness_decision_inputs"]["drawdown_stress_result"]["passed"] = False

    result = _run(request)

    assert result["robustness_decision_result"]["decision_status"] == "REJECT_RISK"
    assert result["rd_agent_proposal"]["proposal_run"] is False


def test_incomplete_trial_accounting_is_blocking():
    request = _request()
    request["robustness_decision_inputs"]["trial_count_metadata"]["complete_accounting"] = False

    result = _run(request)

    assert result["robustness_decision_result"]["decision_status"] == "REJECT_OVERFIT"
    assert "incomplete_trial_accounting" in result["rd_agent_proposal"]["reviewed_robustness_context"]["trial_accounting_findings"]["blocking_reasons"]


def test_mismatched_strategy_identity_fails_closed():
    request = _request()
    request["strategy_identity"]["strategy_id"] = "MISMATCH"

    with pytest.raises(ValueError, match="strategy_id"):
        _run(request)


def test_malformed_nested_robustness_inputs_fail_closed():
    request = _request()
    request["ablation_inputs"]["ablation_policy"]["return_tolerance"] = -1.0

    with pytest.raises(ValueError, match="return_tolerance"):
        _run(request)
