from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "deterministic_ablation_evaluator_request_v1"
RESULT_VERSION = "deterministic_ablation_evaluator_result_v1"
EVALUATOR_VERSION = "deterministic_ablation_evaluator_v1"
CLASSIFICATION_LOAD_BEARING = "LOAD_BEARING"
CLASSIFICATION_USEFUL_BUT_REDUNDANT = "USEFUL_BUT_REDUNDANT"
CLASSIFICATION_DECORATIVE = "DECORATIVE"
CLASSIFICATION_HARMFUL = "HARMFUL"
CLASSIFICATION_REQUIRED_FOR_RISK_SAFETY = "REQUIRED_FOR_RISK_SAFETY"


def evaluate_deterministic_ablations(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    baseline = validated["baseline_variant"]["evaluation_artifact"]
    results = [
        _classify_variant(variant=variant, baseline=baseline, policy=validated["ablation_policy"])
        for variant in validated["ablated_variants"]
    ]
    result = {
        "version": RESULT_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "ablation_results": results,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _classify_variant(*, variant: dict[str, Any], baseline: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    removed_rule = variant["removed_rule"]
    artifact = variant["evaluation_artifact"]
    total_return_delta = artifact["total_return"] - baseline["total_return"]
    drawdown_delta = artifact["max_drawdown"] - baseline["max_drawdown"]
    if removed_rule["rule_role"] == "risk_safety":
        classification = CLASSIFICATION_REQUIRED_FOR_RISK_SAFETY
    elif total_return_delta > policy["return_tolerance"] and drawdown_delta >= -policy["drawdown_tolerance"]:
        classification = CLASSIFICATION_HARMFUL
    elif abs(total_return_delta) <= (policy["return_tolerance"] / 2.0) and abs(drawdown_delta) <= (policy["drawdown_tolerance"] / 2.0):
        classification = CLASSIFICATION_DECORATIVE
    elif artifact["final_review_status"] != baseline["final_review_status"] or total_return_delta < -(policy["return_tolerance"] * 4):
        classification = CLASSIFICATION_LOAD_BEARING
    else:
        classification = CLASSIFICATION_USEFUL_BUT_REDUNDANT
    return {
        "variant_id": variant["variant_id"],
        "strategy_id": variant["strategy_id"],
        "removed_rule": removed_rule,
        "classification": classification,
        "total_return_delta": round(total_return_delta, 10),
        "max_drawdown_delta": round(drawdown_delta, 10),
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "strategy_contract", "baseline_variant", "ablated_variants", "ablation_policy", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    strategy_contract = _required_mapping(payload.get("strategy_contract"), name="strategy_contract")
    if str(strategy_contract.get("version") or "") != "swing_trend_filtered_pullback_strategy_contract_result_v1":
        raise ValueError("strategy_contract.version must be swing_trend_filtered_pullback_strategy_contract_result_v1.")
    baseline_variant = _validate_baseline_variant(payload.get("baseline_variant"))
    ablated_variants = _validate_ablated_variants(payload.get("ablated_variants"), baseline_variant["strategy_id"])
    return {
        "version": version,
        "strategy_contract": strategy_contract,
        "baseline_variant": baseline_variant,
        "ablated_variants": ablated_variants,
        "ablation_policy": _validate_ablation_policy(payload.get("ablation_policy")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_baseline_variant(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="baseline_variant")
    _reject_unknown_fields(payload, allowed={"strategy_id", "evaluation_artifact"}, name="baseline_variant")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "evaluation_artifact": _validate_evaluation_artifact(payload.get("evaluation_artifact")),
    }


def _validate_ablated_variants(value: Any, expected_strategy_id: str) -> list[dict[str, Any]]:
    variants = _required_list(value, name="ablated_variants")
    normalized: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    seen_rule_ids: set[str] = set()
    for item in variants:
        payload = _required_mapping(item, name="ablated_variant")
        _reject_unknown_fields(payload, allowed={"variant_id", "strategy_id", "removed_rule", "evaluation_artifact"}, name="ablated_variant")
        variant_id = _required_text(payload, "variant_id")
        if variant_id in seen_variant_ids:
            raise ValueError("variant_id values must be unique.")
        seen_variant_ids.add(variant_id)
        strategy_id = _required_text(payload, "strategy_id")
        if strategy_id != expected_strategy_id:
            raise ValueError("ablated_variants strategy_id must match baseline strategy_id.")
        removed_rule = _required_mapping(payload.get("removed_rule"), name="removed_rule")
        _reject_unknown_fields(removed_rule, allowed={"rule_id", "rule_role"}, name="removed_rule")
        rule_id = _required_text(removed_rule, "rule_id")
        if rule_id in seen_rule_ids:
            raise ValueError("removed_rule.rule_id values must be unique.")
        seen_rule_ids.add(rule_id)
        rule_role = _required_text(removed_rule, "rule_role")
        if rule_role not in {"alpha", "risk_safety"}:
            raise ValueError("removed_rule.rule_role must be alpha or risk_safety.")
        normalized.append(
            {
                "variant_id": variant_id,
                "strategy_id": strategy_id,
                "removed_rule": {"rule_id": rule_id, "rule_role": rule_role},
                "evaluation_artifact": _validate_evaluation_artifact(payload.get("evaluation_artifact")),
            }
        )
    return normalized


def _validate_evaluation_artifact(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="evaluation_artifact")
    _reject_unknown_fields(payload, allowed={"total_return", "max_drawdown", "final_review_status"}, name="evaluation_artifact")
    return {
        "total_return": _required_finite_number(payload, "total_return"),
        "max_drawdown": _required_finite_number(payload, "max_drawdown"),
        "final_review_status": _required_text(payload, "final_review_status"),
    }


def _validate_ablation_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="ablation_policy")
    _reject_unknown_fields(payload, allowed={"return_tolerance", "drawdown_tolerance"}, name="ablation_policy")
    return {
        "return_tolerance": _required_non_negative_number(payload, "return_tolerance"),
        "drawdown_tolerance": _required_non_negative_number(payload, "drawdown_tolerance"),
    }


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _required_non_negative_number(payload: dict[str, Any], field: str) -> float:
    number = _required_finite_number(payload, field)
    if number < 0:
        raise ValueError(f"{field} must be non-negative.")
    return number


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
