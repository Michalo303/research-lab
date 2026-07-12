from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "parameter_stability_evaluator_request_v1"
RESULT_VERSION = "parameter_stability_evaluator_result_v1"
EVALUATOR_VERSION = "parameter_stability_evaluator_v1"


def evaluate_parameter_stability(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    ordered = validated["one_dimensional_results"]
    scores = [item["score"] for item in ordered]
    values = [item["value"] for item in ordered]
    max_score = max(scores)
    max_index = scores.index(max_score)
    baseline_index = values.index(validated["baseline_value"])
    pair_deltas = [item["score_delta"] for item in validated["pair_interactions"]]

    if pair_deltas and min(pair_deltas) < 0 < max(pair_deltas):
        classification = "UNSTABLE"
    elif baseline_index <= validated["stability_policy"]["edge_buffer"] - 1 or baseline_index >= len(values) - validated["stability_policy"]["edge_buffer"]:
        classification = "EDGE_OF_RANGE"
    elif _is_monotonic(scores):
        classification = "MONOTONIC_NO_OPTIMUM"
    elif _is_isolated_spike(scores, max_index, validated["stability_policy"]["spike_penalty_threshold"]):
        classification = "ISOLATED_SPIKE"
    else:
        plateau_count = sum(1 for score in scores if abs(max_score - score) <= validated["stability_policy"]["plateau_tolerance"])
        classification = "BROAD_PLATEAU" if plateau_count >= 3 else "NARROW_PLATEAU"

    result = {
        "version": RESULT_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "parameter_name": validated["parameter_name"],
        "baseline_value": validated["baseline_value"],
        "stability_classification": classification,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
        "input_sha256": _canonical_sha256(validated),
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "parameter_name", "baseline_value", "one_dimensional_results", "pair_interactions", "stability_policy", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    one_dimensional_results = _validate_one_dimensional_results(payload.get("one_dimensional_results"))
    values = [item["value"] for item in one_dimensional_results]
    baseline_value = _json_scalar(payload.get("baseline_value"), name="baseline_value")
    if baseline_value not in values:
        raise ValueError("baseline_value must exist in one_dimensional_results.")
    return {
        "version": version,
        "parameter_name": _required_text(payload, "parameter_name"),
        "baseline_value": baseline_value,
        "one_dimensional_results": one_dimensional_results,
        "pair_interactions": _validate_pair_interactions(payload.get("pair_interactions")),
        "stability_policy": _validate_stability_policy(payload.get("stability_policy")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_one_dimensional_results(value: Any) -> list[dict[str, Any]]:
    items = _required_list(value, name="one_dimensional_results")
    normalized: list[dict[str, Any]] = []
    seen_values: set[Any] = set()
    for item in items:
        payload = _required_mapping(item, name="one_dimensional_results item")
        _reject_unknown_fields(payload, allowed={"value", "score"}, name="one_dimensional_results item")
        metric_value = _json_scalar(payload.get("value"), name="value")
        if metric_value in seen_values:
            raise ValueError("one_dimensional_results values must be unique.")
        seen_values.add(metric_value)
        normalized.append({"value": metric_value, "score": _required_finite_number(payload, "score")})
    normalized.sort(key=lambda item: item["value"])
    return normalized


def _validate_pair_interactions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("pair_interactions must be a list.")
    items = list(value)
    normalized: list[dict[str, Any]] = []
    for item in items:
        payload = _required_mapping(item, name="pair_interactions item")
        _reject_unknown_fields(payload, allowed={"other_parameter", "other_value", "score_delta"}, name="pair_interactions item")
        normalized.append(
            {
                "other_parameter": _required_text(payload, "other_parameter"),
                "other_value": _json_scalar(payload.get("other_value"), name="other_value"),
                "score_delta": _required_finite_number(payload, "score_delta"),
            }
        )
    return normalized


def _validate_stability_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="stability_policy")
    _reject_unknown_fields(payload, allowed={"plateau_tolerance", "edge_buffer", "spike_penalty_threshold"}, name="stability_policy")
    return {
        "plateau_tolerance": _required_non_negative_number(payload, "plateau_tolerance"),
        "edge_buffer": _required_positive_int(payload, "edge_buffer"),
        "spike_penalty_threshold": _required_non_negative_number(payload, "spike_penalty_threshold"),
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


def _is_monotonic(values: list[float]) -> bool:
    increasing = all(left <= right for left, right in zip(values, values[1:]))
    decreasing = all(left >= right for left, right in zip(values, values[1:]))
    return increasing or decreasing


def _is_isolated_spike(values: list[float], max_index: int, threshold: float) -> bool:
    if max_index == 0 or max_index == len(values) - 1:
        return False
    return (values[max_index] - values[max_index - 1] >= threshold) and (values[max_index] - values[max_index + 1] >= threshold)


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


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
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
