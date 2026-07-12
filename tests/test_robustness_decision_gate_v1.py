from __future__ import annotations

import copy

import pytest

from research_lab.execution.robustness_decision_gate_v1 import (
    build_robustness_decision_gate,
)


def _request() -> dict[str, object]:
    return {
        "version": "robustness_decision_gate_request_v1",
        "strategy_identity": {
            "strategy_id": "STFP_BASE",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": "SYNTH_ABC",
            "baseline_variant_id": "BASELINE",
        },
        "robustness_review_result": {
            "version": "strategy_robustness_review_contract_result_v1",
            "robustness_status": "PASS",
            "required_parameter_checks": [],
            "required_walk_forward_checks": [],
            "required_selection_bias_checks": [],
            "required_drawdown_checks": [],
            "complexity_budget": {
                "allowed_parameter_count": 5,
                "observed_parameter_count": 4,
                "within_budget": True,
            },
            "blocking_reasons": [],
            "knowledge_note_ids_used": ["KNIH-001"],
            "production_runtime_supported": False,
        },
        "ablation_result": {
            "version": "deterministic_ablation_evaluator_result_v1",
            "ablation_results": [
                {
                    "variant_id": "BASELINE",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "note_tag", "rule_role": "alpha"},
                    "classification": "DECORATIVE",
                    "total_return_delta": 0.0,
                    "max_drawdown_delta": 0.0,
                },
                {
                    "variant_id": "SIMPLER_SAFE",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "legacy_filter", "rule_role": "alpha"},
                    "classification": "DECORATIVE",
                    "total_return_delta": 0.0,
                    "max_drawdown_delta": 0.0,
                },
                {
                    "variant_id": "RISKY",
                    "strategy_id": "STFP_BASE",
                    "removed_rule": {"rule_id": "protective_stop", "rule_role": "risk_safety"},
                    "classification": "REQUIRED_FOR_RISK_SAFETY",
                    "total_return_delta": 0.02,
                    "max_drawdown_delta": -0.01,
                },
            ],
            "production_runtime_supported": False,
        },
        "parameter_stability_results": [
            {
                "version": "parameter_stability_evaluator_result_v1",
                "parameter_name": "fast_sma",
                "baseline_value": 3,
                "stability_classification": "BROAD_PLATEAU",
                "production_runtime_supported": False,
            },
            {
                "version": "parameter_stability_evaluator_result_v1",
                "parameter_name": "slow_sma",
                "baseline_value": 5,
                "stability_classification": "NARROW_PLATEAU",
                "production_runtime_supported": False,
            },
        ],
        "baseline_review_artifact": {
            "version": "result_review_gate_result_v1",
            "candidate_id": "RESULT_REVIEW_GATE_V1::abc123",
            "candidate_sha256": "abc123",
            "symbol": "SYNTH_ABC",
            "final_review_status": "REVIEW_REQUIRED",
        },
        "walk_forward_fold_evidence": {
            "strategy_id": "STFP_BASE",
            "fold_results": [
                {"fold_id": "WF-1", "passed": True, "failure_reasons": []},
                {"fold_id": "WF-2", "passed": True, "failure_reasons": []},
                {"fold_id": "WF-3", "passed": True, "failure_reasons": []},
            ],
        },
        "effective_sample_metadata": {
            "strategy_id": "STFP_BASE",
            "effective_sample_size": 160,
            "minimum_required": 100,
            "passed": True,
        },
        "trial_count_metadata": {
            "strategy_id": "STFP_BASE",
            "total_trials": 12,
            "complete_accounting": True,
            "bounded_search": True,
            "selection_mode": "bounded_grid",
        },
        "deflated_sharpe_result": {
            "strategy_id": "STFP_BASE",
            "available": True,
            "passed": True,
            "observed_value": 0.32,
            "minimum_required": 0.10,
        },
        "pbo_cscv_result": {
            "strategy_id": "STFP_BASE",
            "available": True,
            "passed": True,
            "observed_value": 0.08,
            "maximum_allowed": 0.20,
        },
        "drawdown_stress_result": {
            "strategy_id": "STFP_BASE",
            "available": True,
            "passed": True,
            "stressed_max_drawdown": -0.11,
            "maximum_allowed_drawdown": -0.20,
        },
        "complexity_variants": {
            "strategy_id": "STFP_BASE",
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
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_robustness_decision_gate(copy.deepcopy(request))


def test_pass_selects_baseline_when_it_is_already_the_simplest_passing_variant():
    request = _request()
    request["complexity_variants"]["variants"] = [request["complexity_variants"]["variants"][0]]
    request["ablation_result"]["ablation_results"] = [request["ablation_result"]["ablation_results"][0]]

    result = _run(request)

    assert result["decision_status"] == "PASS"
    assert result["selected_variant_id"] == "BASELINE"
    assert result["recommended_variant_id"] == "BASELINE"
    assert result["missing_evidence"] == []
    assert result["rejected_variants"] == []


def test_pass_with_simplification_selects_simplest_safe_variant():
    result = _run(_request())

    assert result["decision_status"] == "PASS_WITH_SIMPLIFICATION"
    assert result["selected_variant_id"] == "SIMPLER_SAFE"
    assert result["recommended_variant_id"] == "SIMPLER_SAFE"
    assert result["rejected_variants"] == [
        {
            "variant_id": "RISKY",
            "reason": "required_risk_safety_rule_removed",
        }
    ]


def test_rejects_overfit_when_parameter_isolated_spike_is_present():
    request = _request()
    request["parameter_stability_results"][0]["stability_classification"] = "ISOLATED_SPIKE"

    result = _run(request)

    assert result["decision_status"] == "REJECT_OVERFIT"
    assert "isolated_spike_detected:fast_sma" in result["blocking_reasons"]
    assert {
        "parameter_name": "fast_sma",
        "stability_classification": "ISOLATED_SPIKE",
    } in result["weak_parameters"]


def test_rejects_risk_when_drawdown_stress_fails():
    request = _request()
    request["drawdown_stress_result"]["passed"] = False

    result = _run(request)

    assert result["decision_status"] == "REJECT_RISK"
    assert "drawdown_stress_failed" in result["blocking_reasons"]


def test_missing_evidence_is_distinct_from_failed_evidence():
    request = _request()
    request["pbo_cscv_result"]["available"] = False
    request["pbo_cscv_result"]["passed"] = False

    result = _run(request)

    assert result["decision_status"] == "REVISE"
    assert result["missing_evidence"] == ["pbo_cscv_result"]
    assert result["blocking_reasons"] == ["missing_pbo_cscv_evidence"]


def test_incomplete_trial_accounting_blocks_as_overfit():
    request = _request()
    request["trial_count_metadata"]["complete_accounting"] = False

    result = _run(request)

    assert result["decision_status"] == "REJECT_OVERFIT"
    assert "incomplete_trial_accounting" in result["blocking_reasons"]


def test_required_risk_controls_are_preserved_even_when_simpler_variant_exists():
    result = _run(_request())

    assert result["selected_variant_id"] == "SIMPLER_SAFE"
    assert all(item["variant_id"] != "RISKY" for item in result["accepted_variants"])
    assert result["rejected_variants"] == [
        {
            "variant_id": "RISKY",
            "reason": "required_risk_safety_rule_removed",
        }
    ]


def test_rejects_identity_mismatch():
    request = _request()
    request["walk_forward_fold_evidence"]["strategy_id"] = "MISMATCH"

    with pytest.raises(ValueError, match="strategy_id"):
        _run(request)


def test_rejects_unknown_fields():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)


def test_rejects_malformed_nested_artifacts():
    request = _request()
    request["ablation_result"]["version"] = "wrong"

    with pytest.raises(ValueError, match="ablation_result.version"):
        _run(request)


def test_result_is_deterministic_and_review_only():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_gate_run"] is False
    assert first["hermes_state_touched"] is False
    assert first["hetzner_state_touched"] is False
    assert first["promotion_performed"] is False
    assert first["production_runtime_supported"] is False
