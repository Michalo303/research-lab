from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


REQUEST_VERSION = "human_approval_gate_request_v1"
GATE_VERSION = "human_approval_gate_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ACTIONS = {"REQUEST_APPROVAL", "APPROVE", "REJECT"}
_STATUSES = {
    "APPROVAL_REQUIRED",
    "APPROVED_FOR_NEXT_REVIEW_STAGE",
    "REJECTED_BY_HUMAN",
    "EXPIRED",
    "FAILED_VALIDATION",
}
_PASSING_DECISIONS = {"PASS", "PASS_WITH_SIMPLIFICATION"}


def build_human_approval_gate(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    bound_artifact_hashes = {
        "experiment_manifest_output_sha256": validated["experiment_manifest"]["output_payload_sha256"],
        "robustness_decision_output_sha256": validated["robustness_decision_result"]["output_payload_sha256"],
        "orchestration_state_output_sha256": validated["orchestration_state"]["output_payload_sha256"],
    }
    invalidation_reasons: list[str] = []
    status = _status_from_action(validated["approval_action"])
    if validated["orchestration_state"]["experiment_manifest_output_sha256"] != validated["experiment_manifest"]["output_payload_sha256"]:
        invalidation_reasons.append("upstream_artifact_mutated")
    if (
        validated["orchestration_state"]["artifact_hashes"]["robustness_decision_output_sha256"]
        != validated["robustness_decision_result"]["output_payload_sha256"]
    ):
        invalidation_reasons.append("upstream_artifact_mutated")

    prior_approval = validated["prior_approval"]
    if prior_approval is not None:
        if prior_approval["bound_artifact_hashes"] != bound_artifact_hashes:
            invalidation_reasons.append("upstream_artifact_mutated")
        if prior_approval["selected_variant_id"] != validated["selected_variant_id"]:
            invalidation_reasons.append("approval_binding_mutated")
        if prior_approval["reviewer_identity"] != validated["reviewer_identity"]:
            invalidation_reasons.append("approval_binding_mutated")
        if prior_approval["approval_timestamp"] != validated["approval_timestamp"]:
            invalidation_reasons.append("approval_binding_mutated")
        if prior_approval["expiry_policy"] != validated["expiry_policy"]:
            invalidation_reasons.append("approval_binding_mutated")
        if invalidation_reasons:
            status = "FAILED_VALIDATION"
        elif prior_approval["approval_status"] == "REJECTED_BY_HUMAN":
            status = "REJECTED_BY_HUMAN"
        elif prior_approval["approval_status"] == "EXPIRED":
            status = "EXPIRED"

    if not invalidation_reasons and _is_expired(validated["expiry_policy"]):
        invalidation_reasons.append("approval_expired")
        status = "EXPIRED"

    result: dict[str, Any] = {
        "approval_gate_version": GATE_VERSION,
        "approval_status": status,
        "experiment_id": validated["experiment_manifest"]["experiment_id"],
        "strategy_identity": validated["experiment_manifest"]["strategy_identity"],
        "bound_artifact_hashes": bound_artifact_hashes,
        "selected_variant_id": validated["selected_variant_id"],
        "reviewer_identity": validated["reviewer_identity"],
        "approval_timestamp": validated["approval_timestamp"],
        "expiry_policy": validated["expiry_policy"],
        "invalidation_reasons": invalidation_reasons,
        "execution_authority_granted": False,
        "persistence_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "automatic_approval_performed": False,
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
        allowed={
            "version",
            "experiment_manifest",
            "robustness_decision_result",
            "orchestration_state",
            "selected_variant_id",
            "approval_action",
            "reviewer_identity",
            "approval_timestamp",
            "expiry_policy",
            "prior_approval",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    experiment_manifest = _validate_experiment_manifest(payload.get("experiment_manifest"))
    robustness_decision_result = _validate_robustness_decision_result(payload.get("robustness_decision_result"))
    orchestration_state = _validate_orchestration_state(payload.get("orchestration_state"))
    prior_approval = _validate_prior_approval(payload.get("prior_approval"))
    selected_variant_id = _required_text(payload, "selected_variant_id")
    if selected_variant_id != robustness_decision_result["selected_variant_id"]:
        raise ValueError("selected_variant_id must match robustness_decision_result.selected_variant_id.")
    if prior_approval is None and orchestration_state["experiment_manifest_output_sha256"] != experiment_manifest["output_payload_sha256"]:
        raise ValueError("orchestration_state must bind to experiment_manifest.output_payload_sha256.")
    if prior_approval is None and orchestration_state["artifact_hashes"]["robustness_decision_output_sha256"] != robustness_decision_result["output_payload_sha256"]:
        raise ValueError("orchestration_state must bind to robustness_decision_result.output_payload_sha256.")
    approval_action = _required_text(payload, "approval_action")
    if approval_action not in _ACTIONS:
        raise ValueError("approval_action is invalid.")
    reviewer_identity = _validate_reviewer_identity(payload.get("reviewer_identity"))
    approval_timestamp = _required_timestamp(payload.get("approval_timestamp"), name="approval_timestamp")
    expiry_policy = _validate_expiry_policy(payload.get("expiry_policy"))
    return {
        "version": version,
        "experiment_manifest": experiment_manifest,
        "robustness_decision_result": robustness_decision_result,
        "orchestration_state": orchestration_state,
        "selected_variant_id": selected_variant_id,
        "approval_action": approval_action,
        "reviewer_identity": reviewer_identity,
        "approval_timestamp": approval_timestamp,
        "expiry_policy": expiry_policy,
        "prior_approval": prior_approval,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_experiment_manifest(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_manifest")
    if _required_text(payload, "manifest_version") != "experiment_manifest_contract_v1":
        raise ValueError("experiment_manifest.manifest_version must be experiment_manifest_contract_v1.")
    _require_sha256(payload, "output_payload_sha256", context="experiment_manifest")
    _validate_safety_flags(payload, name="experiment_manifest")
    return {
        "experiment_id": _required_text(payload, "experiment_id"),
        "strategy_identity": _validate_strategy_identity(payload.get("strategy_identity")),
        "output_payload_sha256": payload["output_payload_sha256"],
    }


def _validate_robustness_decision_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_decision_result")
    if _required_text(payload, "version") != "robustness_decision_gate_result_v1":
        raise ValueError("robustness_decision_result.version must be robustness_decision_gate_result_v1.")
    decision_status = _required_text(payload, "decision_status")
    if decision_status not in _PASSING_DECISIONS:
        raise ValueError("robustness_decision_result.decision_status must require human approval.")
    _require_sha256(payload, "output_payload_sha256", context="robustness_decision_result")
    _validate_review_only_flags(payload, name="robustness_decision_result")
    return {
        "selected_variant_id": _required_text(payload, "selected_variant_id"),
        "output_payload_sha256": payload["output_payload_sha256"],
        "decision_status": decision_status,
    }


def _validate_orchestration_state(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="orchestration_state")
    if _required_text(payload, "state_contract_version") != "orchestration_state_contract_v1":
        raise ValueError("orchestration_state.state_contract_version must be orchestration_state_contract_v1.")
    if _required_text(payload, "current_state") != "HUMAN_APPROVAL_REQUIRED":
        raise ValueError("orchestration_state.current_state must be HUMAN_APPROVAL_REQUIRED.")
    _require_sha256(payload, "output_payload_sha256", context="orchestration_state")
    _require_sha256(payload, "experiment_manifest_output_sha256", context="orchestration_state")
    artifact_hashes = _validate_artifact_hashes(payload.get("artifact_hashes"))
    if "robustness_decision_output_sha256" not in artifact_hashes:
        raise ValueError("orchestration_state.artifact_hashes.robustness_decision_output_sha256 is required.")
    _validate_safety_flags(payload, name="orchestration_state")
    return {
        "output_payload_sha256": payload["output_payload_sha256"],
        "experiment_manifest_output_sha256": payload["experiment_manifest_output_sha256"],
        "artifact_hashes": artifact_hashes,
    }


def _validate_reviewer_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="reviewer_identity")
    _reject_unknown_fields(payload, allowed={"reviewer_id", "reviewer_role"}, name="reviewer_identity")
    return {
        "reviewer_id": _required_text(payload, "reviewer_id"),
        "reviewer_role": _required_text(payload, "reviewer_role"),
    }


def _validate_expiry_policy(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="expiry_policy")
    _reject_unknown_fields(payload, allowed={"expiry_timestamp", "validation_timestamp"}, name="expiry_policy")
    return {
        "expiry_timestamp": _required_timestamp(payload.get("expiry_timestamp"), name="expiry_policy.expiry_timestamp"),
        "validation_timestamp": _required_timestamp(payload.get("validation_timestamp"), name="expiry_policy.validation_timestamp"),
    }


def _validate_prior_approval(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _required_mapping(value, name="prior_approval")
    _reject_unknown_fields(
        payload,
        allowed={
            "approval_gate_version",
            "approval_status",
            "experiment_id",
            "strategy_identity",
            "bound_artifact_hashes",
            "selected_variant_id",
            "reviewer_identity",
            "approval_timestamp",
            "expiry_policy",
            "invalidation_reasons",
            "execution_authority_granted",
            "persistence_performed",
            "provider_calls_used",
            "registry_write_performed",
            "broker_actions_used",
            "deployment_gate_run",
            "promotion_performed",
            "hermes_state_touched",
            "hetzner_state_touched",
            "automatic_approval_performed",
            "production_runtime_supported",
            "input_sha256",
            "provenance",
            "output_payload_sha256",
        },
        name="prior_approval",
    )
    if _required_text(payload, "approval_gate_version") != GATE_VERSION:
        raise ValueError("prior_approval.approval_gate_version must be human_approval_gate_v1.")
    approval_status = _required_text(payload, "approval_status")
    if approval_status not in _STATUSES:
        raise ValueError("prior_approval.approval_status is invalid.")
    _validate_safety_flags(payload, name="prior_approval")
    if payload.get("automatic_approval_performed") is not False:
        raise ValueError("prior_approval.automatic_approval_performed must be false.")
    _require_sha256(payload, "input_sha256", context="prior_approval")
    _require_sha256(payload, "output_payload_sha256", context="prior_approval")
    invalidation_reasons = payload.get("invalidation_reasons")
    if not isinstance(invalidation_reasons, list):
        raise ValueError("prior_approval.invalidation_reasons must be a list.")
    return {
        "approval_status": approval_status,
        "bound_artifact_hashes": _validate_artifact_hashes(payload.get("bound_artifact_hashes")),
        "selected_variant_id": _required_text(payload, "selected_variant_id"),
        "reviewer_identity": _validate_reviewer_identity(payload.get("reviewer_identity")),
        "approval_timestamp": _required_timestamp(payload.get("approval_timestamp"), name="prior_approval.approval_timestamp"),
        "expiry_policy": _validate_expiry_policy(payload.get("expiry_policy")),
    }


def _validate_artifact_hashes(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="bound_artifact_hashes")
    normalized: dict[str, str] = {}
    for key, raw in sorted(payload.items()):
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("bound_artifact_hashes keys must be non-empty text.")
        if not isinstance(raw, str) or not _SHA256_RE.fullmatch(raw):
            raise ValueError(f"bound_artifact_hashes.{key_name} must be a lowercase sha256 hex digest.")
        normalized[key_name] = raw
    if not normalized:
        raise ValueError("bound_artifact_hashes must not be empty.")
    return normalized


def _validate_strategy_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="strategy_identity")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": _required_text(payload, "strategy_builder"),
        "strategy_version": _required_text(payload, "strategy_version"),
    }


def _status_from_action(action: str) -> str:
    if action == "REQUEST_APPROVAL":
        return "APPROVAL_REQUIRED"
    if action == "APPROVE":
        return "APPROVED_FOR_NEXT_REVIEW_STAGE"
    return "REJECTED_BY_HUMAN"


def _is_expired(expiry_policy: dict[str, str]) -> bool:
    return expiry_policy["validation_timestamp"] > expiry_policy["expiry_timestamp"]


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


def _validate_review_only_flags(payload: dict[str, Any], *, name: str) -> None:
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


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(payload: dict[str, Any], field: str, *, context: str) -> None:
    value = payload.get(field)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{context}.{field} must be a lowercase sha256 hex digest.")


def _required_timestamp(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError(f"{name} must be an RFC3339 UTC timestamp.")
    return value


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


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
