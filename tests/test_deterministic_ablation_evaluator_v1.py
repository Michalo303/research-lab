from __future__ import annotations

import copy

import pytest

from research_lab.execution.deterministic_ablation_evaluator_v1 import (
    evaluate_deterministic_ablations,
)


def _strategy_contract() -> dict[str, object]:
    return {
        "version": "swing_trend_filtered_pullback_strategy_contract_result_v1",
        "strategy_builder": "swing_trend_filtered_pullback",
        "symbol": "SYNTH_ABC",
        "production_runtime_supported": False,
    }


def _baseline_variant() -> dict[str, object]:
    return {
        "strategy_id": "STFP_BASE",
        "evaluation_artifact": {
            "total_return": 0.04,
            "max_drawdown": -0.08,
            "final_review_status": "REVIEW_REQUIRED",
        },
    }


def _request() -> dict[str, object]:
    return {
        "version": "deterministic_ablation_evaluator_request_v1",
        "strategy_contract": _strategy_contract(),
        "baseline_variant": _baseline_variant(),
        "ablated_variants": [
            {
                "variant_id": "ABL-LOAD",
                "strategy_id": "STFP_BASE",
                "removed_rule": {"rule_id": "trend_filter", "rule_role": "alpha"},
                "evaluation_artifact": {
                    "total_return": 0.01,
                    "max_drawdown": -0.12,
                    "final_review_status": "REVIEW_REQUIRED",
                },
            },
            {
                "variant_id": "ABL-REDUNDANT",
                "strategy_id": "STFP_BASE",
                "removed_rule": {"rule_id": "pullback_guard", "rule_role": "alpha"},
                "evaluation_artifact": {
                    "total_return": 0.039,
                    "max_drawdown": -0.081,
                    "final_review_status": "REVIEW_REQUIRED",
                },
            },
            {
                "variant_id": "ABL-DECORATIVE",
                "strategy_id": "STFP_BASE",
                "removed_rule": {"rule_id": "note_tag", "rule_role": "alpha"},
                "evaluation_artifact": {
                    "total_return": 0.0401,
                    "max_drawdown": -0.0801,
                    "final_review_status": "REVIEW_REQUIRED",
                },
            },
            {
                "variant_id": "ABL-HARMFUL",
                "strategy_id": "STFP_BASE",
                "removed_rule": {"rule_id": "legacy_toggle", "rule_role": "alpha"},
                "evaluation_artifact": {
                    "total_return": 0.06,
                    "max_drawdown": -0.06,
                    "final_review_status": "REVIEW_REQUIRED",
                },
            },
            {
                "variant_id": "ABL-RISK",
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
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return evaluate_deterministic_ablations(copy.deepcopy(request))


def test_classifies_bounded_ablations_deterministically():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    classifications = {item["variant_id"]: item["classification"] for item in first["ablation_results"]}
    assert classifications == {
        "ABL-LOAD": "LOAD_BEARING",
        "ABL-REDUNDANT": "USEFUL_BUT_REDUNDANT",
        "ABL-DECORATIVE": "DECORATIVE",
        "ABL-HARMFUL": "HARMFUL",
        "ABL-RISK": "REQUIRED_FOR_RISK_SAFETY",
    }
    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["promotion_performed"] is False
    assert first["production_runtime_supported"] is False


def test_rejects_incomplete_or_malformed_variant_sets():
    request = _request()
    request["ablated_variants"][0]["strategy_id"] = "MISMATCH"

    with pytest.raises(ValueError, match="strategy_id"):
        _run(request)


def test_risk_safety_rule_is_not_removed_solely_for_better_return():
    request = _request()
    request["ablated_variants"] = [
        {
            "variant_id": "ABL-RISK-UP",
            "strategy_id": "STFP_BASE",
            "removed_rule": {"rule_id": "protective_stop", "rule_role": "risk_safety"},
            "evaluation_artifact": {
                "total_return": 0.07,
                "max_drawdown": -0.08,
                "final_review_status": "REVIEW_REQUIRED",
            },
        }
    ]

    result = _run(request)

    assert result["ablation_results"][0]["classification"] == "REQUIRED_FOR_RISK_SAFETY"
