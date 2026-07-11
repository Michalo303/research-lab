from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "strategy_robustness_review_contract_request_v1"
RESULT_VERSION = "strategy_robustness_review_contract_result_v1"
CONTRACT_VERSION = "strategy_robustness_review_contract_v1"
STATUS_PASS = "PASS"
STATUS_PASS_WITH_SIMPLIFICATION = "PASS_WITH_SIMPLIFICATION"
STATUS_REVISE = "REVISE"
STATUS_REJECT_OVERFIT = "REJECT_OVERFIT"


def build_strategy_robustness_review_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    parameter_count = len(validated["parameter_schema"]["parameters"])
    walk_forward_reasons, walk_forward_checks = _walk_forward_findings(
        validated["evaluation_window_metadata"],
        validated["robustness_policy"],
    )
    drawdown_reasons, drawdown_checks = _drawdown_findings(
        validated["baseline_review_artifact"],
        validated["robustness_policy"],
    )
    overfit_reasons, selection_bias_checks = _selection_bias_findings(
        validated["experiment_trial_metadata"],
        validated["validated_knihomol_evidence"],
        validated["robustness_policy"],
    )
    parameter_checks = []
    status = STATUS_PASS
    complexity_budget = {
        "allowed_parameter_count": int(validated["robustness_policy"]["max_parameter_count"]),
        "observed_parameter_count": parameter_count,
        "within_budget": parameter_count <= int(validated["robustness_policy"]["max_parameter_count"]),
    }
    if not complexity_budget["within_budget"]:
        parameter_checks.append("reduce_parameter_surface_area")
        status = STATUS_PASS_WITH_SIMPLIFICATION

    blocking_reasons = [*walk_forward_reasons, *drawdown_reasons, *overfit_reasons]
    if overfit_reasons:
        status = STATUS_REJECT_OVERFIT
    elif walk_forward_reasons or drawdown_reasons:
        status = STATUS_REVISE

    result = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "robustness_status": status,
        "required_ablations": [
            "remove_primary_entry_filter",
            "remove_primary_exit_condition",
        ],
        "required_parameter_checks": parameter_checks,
        "required_walk_forward_checks": walk_forward_checks,
        "required_selection_bias_checks": selection_bias_checks,
        "required_drawdown_checks": drawdown_checks,
        "complexity_budget": complexity_budget,
        "blocking_reasons": blocking_reasons,
        "knowledge_note_ids_used": _knowledge_note_ids(validated["validated_knihomol_evidence"]),
        "provider_calls_used": 0,
        "promotion_performed": False,
        "production_runtime_supported": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "strategy_contract",
            "baseline_review_artifact",
            "parameter_schema",
            "evaluation_window_metadata",
            "experiment_trial_metadata",
            "validated_knihomol_evidence",
            "robustness_policy",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    strategy_contract = _required_mapping(payload.get("strategy_contract"), name="strategy_contract")
    baseline_review_artifact = _required_mapping(payload.get("baseline_review_artifact"), name="baseline_review_artifact")
    parameter_schema = _validate_parameter_schema(payload.get("parameter_schema"))
    evaluation_window_metadata = _validate_evaluation_window_metadata(payload.get("evaluation_window_metadata"))
    experiment_trial_metadata = _validate_experiment_trial_metadata(payload.get("experiment_trial_metadata"))
    validated_knihomol_evidence = _validate_knihomol_evidence(payload.get("validated_knihomol_evidence"))
    robustness_policy = _validate_robustness_policy(payload.get("robustness_policy"))
    if str(strategy_contract.get("version") or "") != "swing_trend_filtered_pullback_strategy_contract_result_v1":
        raise ValueError("strategy_contract.version must be swing_trend_filtered_pullback_strategy_contract_result_v1.")
    if strategy_contract.get("production_runtime_supported") is not False:
        raise ValueError("strategy_contract.production_runtime_supported must be false.")
    if str(baseline_review_artifact.get("version") or "") != "result_review_gate_result_v1":
        raise ValueError("baseline_review_artifact.version must be result_review_gate_result_v1.")
    return {
        "version": version,
        "strategy_contract": strategy_contract,
        "baseline_review_artifact": baseline_review_artifact,
        "parameter_schema": parameter_schema,
        "evaluation_window_metadata": evaluation_window_metadata,
        "experiment_trial_metadata": experiment_trial_metadata,
        "validated_knihomol_evidence": validated_knihomol_evidence,
        "robustness_policy": robustness_policy,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_parameter_schema(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="parameter_schema")
    _reject_unknown_fields(payload, allowed={"parameters"}, name="parameter_schema")
    parameters = _required_list(payload.get("parameters"), name="parameter_schema.parameters")
    normalized: list[dict[str, Any]] = []
    for item in parameters:
        param = _required_mapping(item, name="parameter_schema parameter")
        _reject_unknown_fields(param, allowed={"name", "type", "baseline", "tested_values"}, name="parameter_schema parameter")
        tested_values = _required_list(param.get("tested_values"), name="tested_values")
        normalized.append(
            {
                "name": _required_text(param, "name"),
                "type": _required_text(param, "type"),
                "baseline": _json_scalar(param.get("baseline"), name="baseline"),
                "tested_values": [_json_scalar(entry, name="tested_value") for entry in tested_values],
            }
        )
    return {"parameters": normalized}


def _validate_evaluation_window_metadata(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="evaluation_window_metadata")
    _reject_unknown_fields(
        payload,
        allowed={"walk_forward_method", "window_count", "pass_rate", "effective_sample_size"},
        name="evaluation_window_metadata",
    )
    return {
        "walk_forward_method": _required_text(payload, "walk_forward_method"),
        "window_count": _required_positive_int(payload, "window_count"),
        "pass_rate": _required_unit_interval(payload, "pass_rate"),
        "effective_sample_size": _required_positive_int(payload, "effective_sample_size"),
    }


def _validate_experiment_trial_metadata(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_trial_metadata")
    _reject_unknown_fields(payload, allowed={"trial_count", "selection_mode", "selection_bias_controls"}, name="experiment_trial_metadata")
    controls = _required_mapping(payload.get("selection_bias_controls"), name="selection_bias_controls")
    _reject_unknown_fields(controls, allowed={"deflated_sharpe_applied", "pbo_checked"}, name="selection_bias_controls")
    return {
        "trial_count": _required_positive_int(payload, "trial_count"),
        "selection_mode": _required_text(payload, "selection_mode"),
        "selection_bias_controls": {
            "deflated_sharpe_applied": _required_bool(controls, "deflated_sharpe_applied"),
            "pbo_checked": _required_bool(controls, "pbo_checked"),
        },
    }


def _validate_knihomol_evidence(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="validated_knihomol_evidence")
    _reject_unknown_fields(payload, allowed={"notes"}, name="validated_knihomol_evidence")
    notes = _required_list(payload.get("notes"), name="validated_knihomol_evidence.notes")
    normalized: list[dict[str, Any]] = []
    for item in notes:
        note = _required_mapping(item, name="validated_knihomol_evidence note")
        _reject_unknown_fields(note, allowed={"note_id", "status", "topic", "summary", "supports"}, name="validated_knihomol_evidence note")
        supports = _required_list(note.get("supports"), name="supports")
        status = _required_text(note, "status")
        if status != "validated":
            raise ValueError("validated_knihomol_evidence notes must have status=validated.")
        normalized.append(
            {
                "note_id": _required_text(note, "note_id"),
                "status": status,
                "topic": _required_text(note, "topic"),
                "summary": _required_text(note, "summary"),
                "supports": sorted(_required_text({"value": item}, "value") for item in supports),
            }
        )
    return {"notes": sorted(normalized, key=lambda item: item["note_id"])}


def _validate_robustness_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_policy")
    _reject_unknown_fields(
        payload,
        allowed={"min_walk_forward_windows", "min_walk_forward_pass_rate", "max_drawdown", "max_trial_count", "max_parameter_count"},
        name="robustness_policy",
    )
    max_drawdown = _required_finite_number(payload, "max_drawdown")
    if max_drawdown >= 0:
        raise ValueError("max_drawdown must be negative.")
    return {
        "min_walk_forward_windows": _required_positive_int(payload, "min_walk_forward_windows"),
        "min_walk_forward_pass_rate": _required_unit_interval(payload, "min_walk_forward_pass_rate"),
        "max_drawdown": max_drawdown,
        "max_trial_count": _required_positive_int(payload, "max_trial_count"),
        "max_parameter_count": _required_positive_int(payload, "max_parameter_count"),
    }


def _walk_forward_findings(metadata: dict[str, Any], policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    checks: list[str] = []
    if metadata["walk_forward_method"] != "true_rolling_oos":
        reasons.append("walk_forward_method_not_true_rolling_oos")
    if metadata["window_count"] < policy["min_walk_forward_windows"] or metadata["pass_rate"] < policy["min_walk_forward_pass_rate"]:
        reasons.append("walk_forward_evidence_below_policy")
        checks.append("increase_walk_forward_windows")
    return reasons, checks


def _drawdown_findings(review_artifact: dict[str, Any], policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    checks: list[str] = []
    drawdown = review_artifact.get("drawdown")
    if not _is_number(drawdown):
        reasons.append("drawdown_evidence_missing")
        checks.append("supply_drawdown_evidence")
    elif float(drawdown) < float(policy["max_drawdown"]):
        reasons.append("drawdown_exceeds_policy")
        checks.append("reduce_drawdown_under_policy")
    return reasons, checks


def _selection_bias_findings(
    trial_metadata: dict[str, Any],
    evidence: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    checks: list[str] = []
    supports = {item for note in evidence["notes"] for item in note["supports"]}
    controls = trial_metadata["selection_bias_controls"]
    if trial_metadata["trial_count"] > policy["max_trial_count"]:
        checks.append("reduce_trial_count_or_strengthen_bias_controls")
        if (not controls["pbo_checked"]) or (not controls["deflated_sharpe_applied"]) or ("overfit" in supports) or ("selection_bias" in supports):
            reasons.append("overfit_risk_detected")
    return reasons, checks


def _knowledge_note_ids(evidence: dict[str, Any]) -> list[str]:
    return [item["note_id"] for item in evidence["notes"]]


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


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_unit_interval(payload: dict[str, Any], field: str) -> float:
    value = _required_finite_number(payload, field)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{field} must be within [0, 1].")
    return value


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


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


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
