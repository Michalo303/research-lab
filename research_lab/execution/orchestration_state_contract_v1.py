from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


REQUEST_VERSION = "orchestration_state_contract_request_v1"
STATE_CONTRACT_VERSION = "orchestration_state_contract_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATES = {
    "CREATED",
    "BASELINE_REVIEW_REQUIRED",
    "ROBUSTNESS_REVIEW_REQUIRED",
    "REVISION_REQUIRED",
    "RETEST_REQUIRED",
    "HUMAN_APPROVAL_REQUIRED",
    "ACCEPTED_REVIEW_ONLY",
    "REJECTED",
    "EXHAUSTED",
    "FAILED_VALIDATION",
}
_ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"CREATED"},
    "CREATED": {"BASELINE_REVIEW_REQUIRED", "FAILED_VALIDATION"},
    "BASELINE_REVIEW_REQUIRED": {"ROBUSTNESS_REVIEW_REQUIRED", "REJECTED", "FAILED_VALIDATION"},
    "ROBUSTNESS_REVIEW_REQUIRED": {
        "REVISION_REQUIRED",
        "RETEST_REQUIRED",
        "HUMAN_APPROVAL_REQUIRED",
        "REJECTED",
        "EXHAUSTED",
        "FAILED_VALIDATION",
    },
    "REVISION_REQUIRED": {"RETEST_REQUIRED", "REJECTED", "EXHAUSTED", "FAILED_VALIDATION"},
    "RETEST_REQUIRED": {"ROBUSTNESS_REVIEW_REQUIRED", "REJECTED", "EXHAUSTED", "FAILED_VALIDATION"},
    "HUMAN_APPROVAL_REQUIRED": {"ACCEPTED_REVIEW_ONLY", "REJECTED", "EXHAUSTED", "FAILED_VALIDATION"},
    "ACCEPTED_REVIEW_ONLY": set(),
    "REJECTED": set(),
    "EXHAUSTED": set(),
    "FAILED_VALIDATION": set(),
}


def build_orchestration_state_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    previous_state = validated["previous_state"]
    history = list(previous_state["transition_history"]) if previous_state is not None else []
    transition_record = {
        "sequence": len(history) + 1,
        "from_state": None if previous_state is None else previous_state["current_state"],
        "to_state": validated["target_state"],
        "reason": validated["reason"],
        "input_sha256": input_sha256,
        "artifact_hashes": validated["artifact_hashes"],
        "provenance": validated["provenance"],
    }
    transition_record["output_sha256"] = _canonical_sha256(transition_record)
    history.append(transition_record)

    result: dict[str, Any] = {
        "state_contract_version": STATE_CONTRACT_VERSION,
        "experiment_id": validated["experiment_manifest"]["experiment_id"],
        "experiment_manifest_output_sha256": validated["experiment_manifest"]["output_payload_sha256"],
        "current_state": validated["target_state"],
        "artifact_hashes": validated["artifact_hashes"],
        "transition_history": history,
        "execution_authority_granted": False,
        "persistence_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "production_runtime_supported": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "experiment_manifest", "previous_state", "target_state", "reason", "artifact_hashes", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    experiment_manifest = _validate_experiment_manifest(payload.get("experiment_manifest"))
    previous_state = _validate_previous_state(payload.get("previous_state"))
    target_state = _required_text(payload, "target_state")
    if target_state not in _ALLOWED_STATES:
        raise ValueError("target_state is invalid.")
    prior_state_name = None if previous_state is None else previous_state["current_state"]
    if target_state not in _ALLOWED_TRANSITIONS[prior_state_name]:
        raise ValueError(f"transition from {prior_state_name or 'INITIAL'} to {target_state} is not allowed.")
    reason = _required_text(payload, "reason")
    artifact_hashes = _validate_artifact_hashes(payload.get("artifact_hashes"))

    if previous_state is not None:
        if previous_state["experiment_id"] != experiment_manifest["experiment_id"]:
            raise ValueError("previous_state.experiment_id must match experiment_manifest.experiment_id.")
        if previous_state["experiment_manifest_output_sha256"] != experiment_manifest["output_payload_sha256"]:
            raise ValueError("previous_state manifest identity must match experiment_manifest.output_payload_sha256.")
        for key, prior_hash in previous_state["artifact_hashes"].items():
            current_hash = artifact_hashes.get(key)
            if current_hash != prior_hash:
                raise ValueError("artifact_hashes must preserve all prior artifact hashes.")

    return {
        "version": version,
        "experiment_manifest": experiment_manifest,
        "previous_state": previous_state,
        "target_state": target_state,
        "reason": reason,
        "artifact_hashes": artifact_hashes,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_experiment_manifest(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_manifest")
    required = {
        "manifest_version",
        "experiment_id",
        "output_payload_sha256",
        "production_runtime_supported",
        "execution_authority_granted",
        "persistence_performed",
        "provider_calls_used",
        "registry_write_performed",
        "broker_actions_used",
        "deployment_gate_run",
        "promotion_performed",
        "hermes_state_touched",
        "hetzner_state_touched",
    }
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ValueError(f"experiment_manifest is missing required field(s): {', '.join(missing)}")
    if _required_text(payload, "manifest_version") != "experiment_manifest_contract_v1":
        raise ValueError("experiment_manifest.manifest_version must be experiment_manifest_contract_v1.")
    _require_sha256(payload, "output_payload_sha256", context="experiment_manifest")
    _required_text(payload, "experiment_id")
    _validate_safety_flags(payload, name="experiment_manifest")
    return payload


def _validate_previous_state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _required_mapping(value, name="previous_state")
    _reject_unknown_fields(
        payload,
        allowed={
            "state_contract_version",
            "experiment_id",
            "experiment_manifest_output_sha256",
            "current_state",
            "artifact_hashes",
            "transition_history",
            "execution_authority_granted",
            "persistence_performed",
            "provider_calls_used",
            "registry_write_performed",
            "broker_actions_used",
            "deployment_gate_run",
            "promotion_performed",
            "hermes_state_touched",
            "hetzner_state_touched",
            "production_runtime_supported",
            "input_sha256",
            "provenance",
            "output_payload_sha256",
        },
        name="previous_state",
    )
    if _required_text(payload, "state_contract_version") != STATE_CONTRACT_VERSION:
        raise ValueError("previous_state.state_contract_version must be orchestration_state_contract_v1.")
    current_state = _required_text(payload, "current_state")
    if current_state not in _ALLOWED_STATES:
        raise ValueError("previous_state.current_state is invalid.")
    _require_sha256(payload, "experiment_manifest_output_sha256", context="previous_state")
    _require_sha256(payload, "input_sha256", context="previous_state")
    _require_sha256(payload, "output_payload_sha256", context="previous_state")
    _validate_safety_flags(payload, name="previous_state")
    artifact_hashes = _validate_artifact_hashes(payload.get("artifact_hashes"))
    transition_history = _validate_transition_history(payload.get("transition_history"))
    return {
        "state_contract_version": STATE_CONTRACT_VERSION,
        "experiment_id": _required_text(payload, "experiment_id"),
        "experiment_manifest_output_sha256": payload["experiment_manifest_output_sha256"],
        "current_state": current_state,
        "artifact_hashes": artifact_hashes,
        "transition_history": transition_history,
        "input_sha256": payload["input_sha256"],
        "output_payload_sha256": payload["output_payload_sha256"],
    }


def _validate_transition_history(value: Any) -> list[dict[str, Any]]:
    items = _required_non_empty_list(value, name="transition_history")
    normalized: list[dict[str, Any]] = []
    expected_sequence = 1
    for item in items:
        payload = _required_mapping(item, name="transition_record")
        _reject_unknown_fields(
            payload,
            allowed={"sequence", "from_state", "to_state", "reason", "input_sha256", "artifact_hashes", "provenance", "output_sha256"},
            name="transition_record",
        )
        sequence = _required_positive_int(payload, "sequence")
        if sequence != expected_sequence:
            raise ValueError("transition_history sequence must be contiguous.")
        expected_sequence += 1
        from_state = payload.get("from_state")
        if from_state is not None and from_state not in _ALLOWED_STATES:
            raise ValueError("transition_record.from_state is invalid.")
        to_state = _required_text(payload, "to_state")
        if to_state not in _ALLOWED_STATES:
            raise ValueError("transition_record.to_state is invalid.")
        _require_sha256(payload, "input_sha256", context="transition_record")
        _require_sha256(payload, "output_sha256", context="transition_record")
        normalized.append(
            {
                "sequence": sequence,
                "from_state": from_state,
                "to_state": to_state,
                "reason": _required_text(payload, "reason"),
                "input_sha256": payload["input_sha256"],
                "artifact_hashes": _validate_artifact_hashes(payload.get("artifact_hashes")),
                "provenance": _validate_provenance(payload.get("provenance")),
                "output_sha256": payload["output_sha256"],
            }
        )
    return normalized


def _validate_artifact_hashes(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="artifact_hashes")
    normalized: dict[str, str] = {}
    for key, raw in sorted(payload.items()):
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("artifact_hashes keys must be non-empty text.")
        if not isinstance(raw, str) or not _SHA256_RE.fullmatch(raw):
            raise ValueError(f"artifact_hashes.{key_name} must be a lowercase sha256 hex digest.")
        normalized[key_name] = raw
    if not normalized:
        raise ValueError("artifact_hashes must not be empty.")
    return normalized


def _validate_safety_flags(payload: dict[str, Any], *, name: str) -> None:
    if payload.get("execution_authority_granted") is not False:
        raise ValueError(f"{name}.execution_authority_granted must be false.")
    if payload.get("persistence_performed") is not False:
        raise ValueError(f"{name}.persistence_performed must be false.")
    if int(payload.get("provider_calls_used") or 0) != 0:
        raise ValueError(f"{name}.provider_calls_used must be 0.")
    if payload.get("registry_write_performed") is not False:
        raise ValueError(f"{name}.registry_write_performed must be false.")
    if int(payload.get("broker_actions_used") or 0) != 0:
        raise ValueError(f"{name}.broker_actions_used must be 0.")
    if payload.get("deployment_gate_run") is not False:
        raise ValueError(f"{name}.deployment_gate_run must be false.")
    if payload.get("promotion_performed") is not False:
        raise ValueError(f"{name}.promotion_performed must be false.")
    if payload.get("hermes_state_touched") is not False:
        raise ValueError(f"{name}.hermes_state_touched must be false.")
    if payload.get("hetzner_state_touched") is not False:
        raise ValueError(f"{name}.hetzner_state_touched must be false.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError(f"{name}.production_runtime_supported must be false.")


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


def _require_sha256(payload: dict[str, Any], field: str, *, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{context}.{field} must be a lowercase sha256 hex digest.")
    return value


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_non_empty_list(value: Any, *, name: str) -> list[Any]:
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
