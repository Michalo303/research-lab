from __future__ import annotations

import copy

import pytest

from research_lab.research.research_objective_promotion_gate_v1 import (
    build_research_objective_policy_v1,
    evaluate_research_objective_promotion_gate_v1,
)


def _policy_request() -> dict[str, object]:
    return {
        "version": "research_objective_policy_request_v1",
        "policy_id": "RESEARCH-OBJECTIVE-001",
        "provenance": {"source": "unit_test"},
    }


def _policy() -> dict[str, object]:
    return build_research_objective_policy_v1(copy.deepcopy(_policy_request()))


def _metrics() -> dict[str, object]:
    return {
        "net_cagr": 0.16,
        "max_drawdown": 0.10,
        "sharpe": 1.30,
        "sortino": 1.80,
        "calmar": 1.30,
        "walk_forward_efficiency": 0.70,
        "positive_walk_forward_windows": 0.80,
        "deflated_sharpe_confidence": 0.96,
        "probability_backtest_overfit": 0.10,
        "oos_to_is_sharpe_ratio": 0.70,
        "double_cost_result": 0.01,
        "single_year_profit_share": 0.30,
        "single_instrument_profit_share": 0.10,
    }


def _evaluation_request(scope: str = "PRIMARY_PORTFOLIO") -> dict[str, object]:
    return {
        "version": "research_objective_gate_evaluation_request_v1",
        "evaluation_id": "EVAL-001",
        "gate_scope": scope,
        "policy": _policy(),
        "metrics": _metrics(),
        "hard_vetoes": [],
        "provenance": {"source": "unit_test"},
    }


def test_builds_fixed_canonical_policy_with_explicit_safety_boundary():
    first = _policy()
    second = _policy()

    assert first == second
    assert first["primary_frozen_portfolio_target"]["net_cagr_min"] == 0.15
    assert first["primary_frozen_portfolio_target"]["max_drawdown_max"] == 0.12
    assert first["dominance_defaults"] == {
        "single_instrument_profit_share_max": 0.20,
        "single_year_profit_share_max": 0.40,
    }
    assert len(first["canonical_policy_sha256"]) == 64
    assert first["safety_fields"]["production_runtime_supported"] is False


def test_primary_portfolio_pass_requires_all_primary_metrics_and_no_hard_vetoes():
    result = evaluate_research_objective_promotion_gate_v1(_evaluation_request())

    assert result["status"] == "TARGET_PORTFOLIO_GATE_PASS"
    assert result["failed_requirements"] == []
    assert result["policy_sha256"] == _policy()["canonical_policy_sha256"]
    assert result["safety_fields"]["provider_calls_used"] == 0


def test_hard_veto_overrides_anotherwise_passing_metrics():
    request = _evaluation_request()
    request["hard_vetoes"] = ["SEALED_OOS_CONTAMINATION"]

    result = evaluate_research_objective_promotion_gate_v1(request)

    assert result["status"] == "FAIL"
    assert result["failed_requirements"] == ["SEALED_OOS_CONTAMINATION"]


def test_policy_metric_failures_use_the_mandatory_hard_veto_taxonomy():
    request = _evaluation_request("STANDALONE_STRATEGY")
    request["metrics"]["max_drawdown"] = 0.21
    request["metrics"]["deflated_sharpe_confidence"] = 0.89
    request["metrics"]["probability_backtest_overfit"] = 0.26
    request["metrics"]["double_cost_result"] = -0.01

    result = evaluate_research_objective_promotion_gate_v1(request)

    assert result["status"] == "FAIL"
    assert set(result["failed_requirements"]) >= {
        "MAX_DRAWDOWN_ABOVE_POLICY",
        "DSR_BELOW_POLICY",
        "PBO_ABOVE_POLICY",
        "EDGE_DESTROYED_BY_2X_COSTS",
    }


def test_evaluation_rejects_policy_threshold_mutation_and_unknown_fields():
    request = _evaluation_request()
    request["policy"]["primary_frozen_portfolio_target"]["net_cagr_min"] = 0.01
    with pytest.raises(ValueError, match="canonical_policy_sha256"):
        evaluate_research_objective_promotion_gate_v1(request)

    request = _evaluation_request()
    request["unexpected"] = True
    with pytest.raises(ValueError, match="unknown field"):
        evaluate_research_objective_promotion_gate_v1(request)


def test_drawdown_and_probability_metrics_are_non_negative_loss_magnitudes():
    request = _evaluation_request()
    request["metrics"]["max_drawdown"] = -0.30

    with pytest.raises(ValueError, match="non-negative"):
        evaluate_research_objective_promotion_gate_v1(request)


def test_policy_can_explicitly_configure_hash_covered_dominance_defaults():
    request = _policy_request()
    request["dominance_defaults"] = {
        "single_year_profit_share_max": 0.35,
        "single_instrument_profit_share_max": 0.15,
    }

    policy = build_research_objective_policy_v1(request)

    assert policy["dominance_defaults"]["single_year_profit_share_max"] == 0.35
    assert policy["dominance_defaults"]["single_instrument_profit_share_max"] == 0.15


def test_weak_regime_claim_requires_explicit_quantitative_evidence():
    request = _evaluation_request("PORTFOLIO_CONTRIBUTION")
    request["metrics"].update({
        "marginal_net_cagr_improvement": 0.0,
        "marginal_sharpe_improvement": 0.0,
        "max_drawdown_reduction": 0.0,
        "marginal_drawdown_increase": 0.0,
        "weak_regime_improvement": True,
        "concentration_limit_violation": False,
        "liquidity_limit_violation": False,
        "turnover_without_benefit": False,
    })
    request["review_required_reasons"] = []

    with pytest.raises(ValueError, match="weak_regime_evidence"):
        evaluate_research_objective_promotion_gate_v1(request)

    request["metrics"]["weak_regime_evidence"] = {
        "regime_id": "CRISIS",
        "baseline_metric": -0.10,
        "candidate_metric": -0.02,
        "measured_improvement": 0.08,
        "evidence_sha256": "a" * 64,
    }
    assert evaluate_research_objective_promotion_gate_v1(request)["status"] == "PORTFOLIO_CONTRIBUTION_GATE_PASS"


def test_explicit_review_boundary_is_deterministic_and_cannot_pass():
    request = _evaluation_request()
    request["review_required_reasons"] = ["INSUFFICIENT_CONTEXT_EVIDENCE"]

    result = evaluate_research_objective_promotion_gate_v1(request)

    assert result["status"] == "REVIEW_REQUIRED"


def test_provenance_rejects_non_json_scalars_before_hashing():
    request = _policy_request()
    request["provenance"] = {"bad": ("not", "json")}

    with pytest.raises(ValueError, match="JSON scalar"):
        build_research_objective_policy_v1(request)
