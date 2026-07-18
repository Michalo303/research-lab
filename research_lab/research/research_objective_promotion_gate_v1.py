from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any


POLICY_REQUEST_VERSION = "research_objective_policy_request_v1"
POLICY_RESULT_VERSION = "research_objective_policy_result_v1"
EVALUATION_REQUEST_VERSION = "research_objective_gate_evaluation_request_v1"
EVALUATION_RESULT_VERSION = "research_objective_gate_evaluation_result_v1"
CONTRACT_VERSION = "research_objective_promotion_gate_v1"

_HARD_VETOES = {
    "LOOK_AHEAD",
    "SURVIVORSHIP_BIAS",
    "POINT_IN_TIME_VIOLATION",
    "SEALED_OOS_CONTAMINATION",
    "LOSING_SEALED_OOS",
    "MAX_DRAWDOWN_ABOVE_POLICY",
    "PBO_ABOVE_POLICY",
    "DSR_BELOW_POLICY",
    "EDGE_DESTROYED_BY_2X_COSTS",
    "NARROW_PARAMETER_SPIKE",
    "SINGLE_INSTRUMENT_DOMINANCE",
    "ISOLATED_PERIOD_DOMINANCE",
    "INSUFFICIENT_TRADE_COUNT",
    "INVALID_DATA_LINEAGE",
    "UNBOUNDED_EXPERIMENT_COUNT",
}
_SCOPES = {
    "PRIMARY_PORTFOLIO": "TARGET_PORTFOLIO_GATE_PASS",
    "MINIMUM_VIABLE_PORTFOLIO": "MINIMUM_VIABLE_PORTFOLIO_GATE_PASS",
    "STANDALONE_STRATEGY": "STANDALONE_STRATEGY_GATE_PASS",
    "PORTFOLIO_CONTRIBUTION": "PORTFOLIO_CONTRIBUTION_GATE_PASS",
}
_REVIEW_REASONS = {"INSUFFICIENT_CONTEXT_EVIDENCE", "HUMAN_POLICY_INTERPRETATION_REQUIRED"}
_SAFETY = {
    "provider_calls_used": 0,
    "provider_credentials_accessed": False,
    "broker_calls_used": 0,
    "Fio_actions_performed": False,
    "IBKR_actions_performed": False,
    "paper_trading_performed": False,
    "live_trading_performed": False,
    "executable_orders_generated": False,
    "deployment_performed": False,
    "registry_write_performed": False,
    "production_runtime_supported": False,
}


def build_research_objective_policy_v1(request: dict[str, object]) -> dict[str, object]:
    value = _validate_policy_request(request)
    result: dict[str, object] = {
        "version": POLICY_RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "policy_id": value["policy_id"],
        "primary_frozen_portfolio_target": {
            "net_cagr_min": 0.15, "max_drawdown_max": 0.12, "sharpe_min": 1.20,
            "sortino_min": 1.70, "calmar_min": 1.20, "walk_forward_efficiency_min": 0.60,
            "positive_walk_forward_windows_min": 0.70, "deflated_sharpe_confidence_min": 0.95,
            "probability_backtest_overfit_max": 0.20,
        },
        "minimum_viable_research_portfolio": {
            "net_cagr_min": 0.10, "max_drawdown_max": 0.15, "sharpe_min": 0.90,
            "calmar_min": 0.70, "walk_forward_efficiency_min": 0.50,
            "positive_walk_forward_windows_min": 0.60, "deflated_sharpe_confidence_min": 0.90,
            "probability_backtest_overfit_max": 0.25,
        },
        "standalone_strategy_continuation_gate": {
            "net_cagr_min": 0.06, "max_drawdown_max": 0.20, "sharpe_min": 0.70,
            "calmar_min": 0.40, "positive_walk_forward_windows_min": 0.60,
            "oos_to_is_sharpe_ratio_min": 0.50, "deflated_sharpe_confidence_min": 0.90,
            "probability_backtest_overfit_max": 0.25, "double_cost_result_min": 0.00,
        },
        "portfolio_contribution_alternatives": {
            "marginal_net_cagr_improvement_min": 0.01,
            "marginal_sharpe_improvement_min": 0.10,
            "max_drawdown_reduction_min": 0.02,
            "explicitly_measured_weak_regime_improvement_required": True,
        },
        "portfolio_contribution_vetoes": {
            "marginal_sharpe_deterioration_max": 0.05,
            "marginal_drawdown_increase_max": 0.02,
            "concentration_limit_violation": "FAIL",
            "liquidity_limit_violation": "FAIL",
            "turnover_without_benefit": "FAIL",
        },
        "mandatory_hard_vetoes": sorted(_HARD_VETOES),
        "dominance_defaults": copy.deepcopy(value["dominance_defaults"]),
        "input_sha256": _sha(value),
        "provenance": copy.deepcopy(value["provenance"]),
        "safety_fields": copy.deepcopy(_SAFETY),
    }
    result["canonical_policy_sha256"] = _sha(result)
    return copy.deepcopy(result)


def evaluate_research_objective_promotion_gate_v1(request: dict[str, object]) -> dict[str, object]:
    value = _validate_evaluation_request(request)
    policy = value["policy"]
    failed = list(value["hard_vetoes"])
    if not failed:
        failed.extend(_threshold_failures(value["gate_scope"], policy, value["metrics"]))
    status = "FAIL" if failed else "REVIEW_REQUIRED" if value["review_required_reasons"] else _SCOPES[value["gate_scope"]]
    result: dict[str, object] = {
        "version": EVALUATION_RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "evaluation_id": value["evaluation_id"],
        "gate_scope": value["gate_scope"],
        "status": status,
        "policy_sha256": policy["canonical_policy_sha256"],
        "failed_requirements": sorted(set(failed)),
        "metrics": copy.deepcopy(value["metrics"]),
        "hard_vetoes": list(value["hard_vetoes"]),
        "review_required_reasons": list(value["review_required_reasons"]),
        "input_sha256": _sha(value),
        "provenance": copy.deepcopy(value["provenance"]),
        "safety_fields": copy.deepcopy(_SAFETY),
    }
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _threshold_failures(scope: str, policy: dict[str, Any], metrics: dict[str, float | bool]) -> list[str]:
    if scope == "PORTFOLIO_CONTRIBUTION":
        alternatives = policy["portfolio_contribution_alternatives"]
        vetoes = policy["portfolio_contribution_vetoes"]
        failures: list[str] = []
        if not (
            metrics["marginal_net_cagr_improvement"] >= alternatives["marginal_net_cagr_improvement_min"]
            or metrics["marginal_sharpe_improvement"] >= alternatives["marginal_sharpe_improvement_min"]
            or metrics["max_drawdown_reduction"] >= alternatives["max_drawdown_reduction_min"]
            or metrics["weak_regime_improvement"] is True
        ):
            failures.append("NO_MATERIAL_PORTFOLIO_CONTRIBUTION")
        if metrics["marginal_sharpe_improvement"] < -vetoes["marginal_sharpe_deterioration_max"]:
            failures.append("MARGINAL_SHARPE_DETERIORATION")
        if metrics["marginal_drawdown_increase"] > vetoes["marginal_drawdown_increase_max"]:
            failures.append("MARGINAL_DRAWDOWN_INCREASE")
        for name, requirement in (("concentration_limit_violation", "CONCENTRATION_LIMIT_VIOLATION"), ("liquidity_limit_violation", "LIQUIDITY_LIMIT_VIOLATION"), ("turnover_without_benefit", "TURNOVER_WITHOUT_BENEFIT")):
            if metrics[name] is True:
                failures.append(requirement)
        return failures
    target_key = {
        "PRIMARY_PORTFOLIO": "primary_frozen_portfolio_target",
        "MINIMUM_VIABLE_PORTFOLIO": "minimum_viable_research_portfolio",
        "STANDALONE_STRATEGY": "standalone_strategy_continuation_gate",
    }[scope]
    requirements = policy[target_key]
    veto_names = {
        "max_drawdown_max": "MAX_DRAWDOWN_ABOVE_POLICY",
        "deflated_sharpe_confidence_min": "DSR_BELOW_POLICY",
        "probability_backtest_overfit_max": "PBO_ABOVE_POLICY",
        "double_cost_result_min": "EDGE_DESTROYED_BY_2X_COSTS",
    }
    failures = [
        veto_names.get(name, name.upper())
        for name, threshold in requirements.items()
        if not _meets(name, metrics[name.removesuffix("_min").removesuffix("_max")], threshold)
    ]
    if metrics["single_year_profit_share"] > policy["dominance_defaults"]["single_year_profit_share_max"]:
        failures.append("ISOLATED_PERIOD_DOMINANCE")
    if metrics["single_instrument_profit_share"] > policy["dominance_defaults"]["single_instrument_profit_share_max"]:
        failures.append("SINGLE_INSTRUMENT_DOMINANCE")
    return failures


def _meets(name: str, actual: float | bool, threshold: float | bool) -> bool:
    if name.endswith("_max"):
        return bool(actual <= threshold)
    return bool(actual >= threshold)


def _validate_policy_request(request: dict[str, object]) -> dict[str, Any]:
    value = _mapping(request, "request")
    _unknown(value, {"version", "policy_id", "dominance_defaults", "provenance"}, "request")
    if _text(value, "version") != POLICY_REQUEST_VERSION:
        raise ValueError(f"version must be {POLICY_REQUEST_VERSION}.")
    return {"version": POLICY_REQUEST_VERSION, "policy_id": _text(value, "policy_id"), "dominance_defaults": _dominance_defaults(value.get("dominance_defaults")), "provenance": _provenance(value.get("provenance"))}


def _validate_evaluation_request(request: dict[str, object]) -> dict[str, Any]:
    value = _mapping(request, "request")
    _unknown(value, {"version", "evaluation_id", "gate_scope", "policy", "metrics", "hard_vetoes", "review_required_reasons", "provenance"}, "request")
    if _text(value, "version") != EVALUATION_REQUEST_VERSION:
        raise ValueError(f"version must be {EVALUATION_REQUEST_VERSION}.")
    scope = _text(value, "gate_scope")
    if scope not in _SCOPES:
        raise ValueError("gate_scope is not supported.")
    return {"version": EVALUATION_REQUEST_VERSION, "evaluation_id": _text(value, "evaluation_id"), "gate_scope": scope, "policy": _policy(value.get("policy")), "metrics": _metrics(value.get("metrics"), scope), "hard_vetoes": _hard_vetoes(value.get("hard_vetoes")), "review_required_reasons": _review_reasons(value.get("review_required_reasons", [])), "provenance": _provenance(value.get("provenance"))}


def _policy(raw: Any) -> dict[str, Any]:
    value = _mapping(raw, "policy")
    expected = build_research_objective_policy_v1({"version": POLICY_REQUEST_VERSION, "policy_id": _text(value, "policy_id"), "dominance_defaults": value.get("dominance_defaults"), "provenance": _provenance(value.get("provenance"))})
    supplied = copy.deepcopy(value)
    declared = supplied.get("canonical_policy_sha256")
    if declared != _sha({key: item for key, item in supplied.items() if key != "canonical_policy_sha256"}):
        raise ValueError("policy.canonical_policy_sha256 does not match policy content.")
    if supplied != expected:
        raise ValueError("policy must be the fixed canonical policy; runtime threshold mutation is prohibited.")
    return copy.deepcopy(supplied)


def _metrics(raw: Any, scope: str) -> dict[str, float | bool]:
    value = _mapping(raw, "metrics")
    numeric = {"net_cagr", "max_drawdown", "sharpe", "sortino", "calmar", "walk_forward_efficiency", "positive_walk_forward_windows", "deflated_sharpe_confidence", "probability_backtest_overfit", "oos_to_is_sharpe_ratio", "double_cost_result", "single_year_profit_share", "single_instrument_profit_share"}
    contribution_numeric = {"marginal_net_cagr_improvement", "marginal_sharpe_improvement", "max_drawdown_reduction", "marginal_drawdown_increase"}
    contribution_bool = {"weak_regime_improvement", "concentration_limit_violation", "liquidity_limit_violation", "turnover_without_benefit"}
    required = numeric | (contribution_numeric | contribution_bool if scope == "PORTFOLIO_CONTRIBUTION" else set())
    optional = {"weak_regime_evidence"} if scope == "PORTFOLIO_CONTRIBUTION" else set()
    _unknown(value, required | optional, "metrics")
    if not required <= set(value) or set(value) - required - optional:
        raise ValueError("metrics fields must exactly match the selected gate scope.")
    result: dict[str, float | bool] = {}
    for name in numeric | contribution_numeric:
        if name in value:
            result[name] = _number(value, name)
    for name in {"max_drawdown", "walk_forward_efficiency", "positive_walk_forward_windows", "deflated_sharpe_confidence", "probability_backtest_overfit", "single_year_profit_share", "single_instrument_profit_share"}:
        if not 0.0 <= result[name] <= 1.0:
            raise ValueError(f"metrics.{name} must be a non-negative proportion no greater than one.")
    for name in contribution_bool:
        if name in value:
            if not isinstance(value[name], bool):
                raise ValueError(f"metrics.{name} must be boolean.")
            result[name] = value[name]
    if scope == "PORTFOLIO_CONTRIBUTION" and result["weak_regime_improvement"] is True:
        result["weak_regime_evidence"] = _weak_regime_evidence(value.get("weak_regime_evidence"))
    elif "weak_regime_evidence" in value:
        raise ValueError("metrics.weak_regime_evidence is allowed only with weak_regime_improvement true.")
    return result


def _hard_vetoes(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("hard_vetoes must be a list.")
    values = [_text({"value": item}, "value") for item in raw]
    if len(values) != len(set(values)) or any(item not in _HARD_VETOES for item in values):
        raise ValueError("hard_vetoes must contain unique supported values.")
    return sorted(values)


def _review_reasons(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("review_required_reasons must be a list.")
    values = [_text({"value": item}, "value") for item in raw]
    if len(values) != len(set(values)) or any(item not in _REVIEW_REASONS for item in values):
        raise ValueError("review_required_reasons must contain unique supported values.")
    return sorted(values)


def _dominance_defaults(raw: Any) -> dict[str, float]:
    value = {"single_year_profit_share_max": 0.40, "single_instrument_profit_share_max": 0.20} if raw is None else _mapping(raw, "dominance_defaults")
    _unknown(value, {"single_year_profit_share_max", "single_instrument_profit_share_max"}, "dominance_defaults")
    if set(value) != {"single_year_profit_share_max", "single_instrument_profit_share_max"}:
        raise ValueError("dominance_defaults fields are required.")
    result = {name: _number(value, name) for name in value}
    if any(not 0.0 < threshold <= 1.0 for threshold in result.values()):
        raise ValueError("dominance_defaults values must be proportions greater than zero and no greater than one.")
    return result


def _weak_regime_evidence(raw: Any) -> dict[str, str | float]:
    value = _mapping(raw, "weak_regime_evidence")
    _unknown(value, {"regime_id", "baseline_metric", "candidate_metric", "measured_improvement", "evidence_sha256"}, "weak_regime_evidence")
    if set(value) != {"regime_id", "baseline_metric", "candidate_metric", "measured_improvement", "evidence_sha256"}:
        raise ValueError("weak_regime_evidence fields are required.")
    evidence_sha256 = _text(value, "evidence_sha256")
    if len(evidence_sha256) != 64 or any(char not in "0123456789abcdef" for char in evidence_sha256):
        raise ValueError("weak_regime_evidence.evidence_sha256 must be lowercase SHA-256 hex.")
    baseline = _number(value, "baseline_metric")
    candidate = _number(value, "candidate_metric")
    improvement = _number(value, "measured_improvement")
    if improvement <= 0.0 or not math.isclose(candidate - baseline, improvement, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("weak_regime_evidence must contain a positive measured improvement equal to candidate minus baseline.")
    return {"regime_id": _text(value, "regime_id"), "baseline_metric": baseline, "candidate_metric": candidate, "measured_improvement": improvement, "evidence_sha256": evidence_sha256}


def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object.")
    return copy.deepcopy(raw)


def _unknown(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _text(value: dict[str, Any], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be non-empty text.")
    return raw.strip()


def _number(value: dict[str, Any], name: str) -> float:
    raw = value.get(name)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ValueError(f"metrics.{name} must be finite numeric.")
    return float(raw)


def _provenance(raw: Any) -> dict[str, str | int | float | bool | None]:
    value = {} if raw is None else _mapping(raw, "provenance")
    result: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or item is not None and not isinstance(item, (str, int, float, bool)) or (isinstance(item, float) and not math.isfinite(item)):
            raise ValueError("provenance must contain non-empty keys and JSON scalar values.")
        result[key.strip()] = item
    return result


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
