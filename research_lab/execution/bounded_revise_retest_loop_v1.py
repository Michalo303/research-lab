from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from research_lab.execution.orchestration_state_contract_v1 import (
    build_orchestration_state_contract,
)


REQUEST_VERSION = "bounded_revise_retest_loop_request_v1"
LOOP_VERSION = "bounded_revise_retest_loop_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PARENT_PREVIOUS_STEP = "STATE_FROM_PREVIOUS_STEP"


def run_bounded_revise_retest_loop(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    manifest = validated["experiment_manifest"]
    current_state = validated["initial_state"]
    processed_steps: list[dict[str, Any]] = []
    seen_proposals: set[str] = set()
    iteration_count = 0
    revision_count = 0
    retry_count = 0
    repeated_proposal_detected = False

    for step in validated["steps"]:
        expected_parent = current_state["output_payload_sha256"] if step["parent_output_sha256"] == _PARENT_PREVIOUS_STEP else step["parent_output_sha256"]
        if expected_parent != current_state["output_payload_sha256"]:
            raise ValueError("step lineage does not match the current state output hash.")

        proposal_fingerprint = step["proposal_fingerprint"]
        if proposal_fingerprint is not None:
            if proposal_fingerprint in seen_proposals:
                repeated_proposal_detected = True
                current_state = build_orchestration_state_contract(
                    {
                        "version": "orchestration_state_contract_request_v1",
                        "experiment_manifest": manifest,
                        "previous_state": current_state,
                        "target_state": "REJECTED",
                        "reason": "repeated_proposal_detected",
                        "artifact_hashes": current_state["artifact_hashes"],
                        "provenance": validated["provenance"],
                    }
                )
                break
            seen_proposals.add(proposal_fingerprint)

        if iteration_count >= int(manifest["iteration_budget"]):
            current_state = build_orchestration_state_contract(
                {
                    "version": "orchestration_state_contract_request_v1",
                    "experiment_manifest": manifest,
                    "previous_state": current_state,
                    "target_state": "EXHAUSTED",
                    "reason": "iteration_budget_exhausted",
                    "artifact_hashes": current_state["artifact_hashes"],
                    "provenance": validated["provenance"],
                }
            )
            break

        if step["target_state"] == "REVISION_REQUIRED" and revision_count >= int(manifest["revision_budget"]):
            current_state = build_orchestration_state_contract(
                {
                    "version": "orchestration_state_contract_request_v1",
                    "experiment_manifest": manifest,
                    "previous_state": current_state,
                    "target_state": "EXHAUSTED",
                    "reason": "revision_budget_exhausted",
                    "artifact_hashes": current_state["artifact_hashes"],
                    "provenance": validated["provenance"],
                }
            )
            break

        if step["target_state"] == "RETEST_REQUIRED" and retry_count >= int(manifest["retry_budget"]):
            current_state = build_orchestration_state_contract(
                {
                    "version": "orchestration_state_contract_request_v1",
                    "experiment_manifest": manifest,
                    "previous_state": current_state,
                    "target_state": "EXHAUSTED",
                    "reason": "retry_budget_exhausted",
                    "artifact_hashes": current_state["artifact_hashes"],
                    "provenance": validated["provenance"],
                }
            )
            break

        next_artifact_hashes = dict(current_state["artifact_hashes"])
        next_artifact_hashes.update(step["artifact_hashes"])
        current_state = build_orchestration_state_contract(
            {
                "version": "orchestration_state_contract_request_v1",
                "experiment_manifest": manifest,
                "previous_state": current_state,
                "target_state": step["target_state"],
                "reason": step["reason"],
                "artifact_hashes": next_artifact_hashes,
                "provenance": validated["provenance"],
            }
        )
        iteration_count += 1
        if step["target_state"] == "REVISION_REQUIRED":
            revision_count += 1
        if step["target_state"] == "RETEST_REQUIRED":
            retry_count += 1
        processed_steps.append(
            {
                "step_id": step["step_id"],
                "target_state": step["target_state"],
                "proposal_fingerprint": proposal_fingerprint,
                "state_output_sha256": current_state["output_payload_sha256"],
            }
        )
        if current_state["current_state"] in {"ACCEPTED_REVIEW_ONLY", "REJECTED", "EXHAUSTED", "FAILED_VALIDATION"}:
            break

    result: dict[str, Any] = {
        "loop_contract_version": LOOP_VERSION,
        "experiment_id": manifest["experiment_id"],
        "final_state": current_state["current_state"],
        "final_state_artifact": current_state,
        "processed_steps": processed_steps,
        "iteration_count": iteration_count,
        "revision_count": revision_count,
        "retry_count": retry_count,
        "repeated_proposal_detected": repeated_proposal_detected,
        "execution_authority_granted": False,
        "persistence_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "generated_code_executed": False,
        "production_runtime_supported": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "experiment_manifest", "initial_state", "steps", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    manifest = _validate_experiment_manifest(payload.get("experiment_manifest"))
    initial_state = _validate_initial_state(payload.get("initial_state"))
    if initial_state["experiment_id"] != manifest["experiment_id"]:
        raise ValueError("initial_state.experiment_id must match experiment_manifest.experiment_id.")
    steps = _validate_steps(payload.get("steps"))
    return {
        "version": version,
        "experiment_manifest": manifest,
        "initial_state": initial_state,
        "steps": steps,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_experiment_manifest(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_manifest")
    if _required_text(payload, "manifest_version") != "experiment_manifest_contract_v1":
        raise ValueError("experiment_manifest.manifest_version must be experiment_manifest_contract_v1.")
    _require_sha256(payload, "output_payload_sha256", context="experiment_manifest")
    _required_text(payload, "experiment_id")
    _required_positive_int(payload, "iteration_budget")
    _required_non_negative_int(payload, "revision_budget")
    _required_non_negative_int(payload, "retry_budget")
    _validate_safety_flags(payload, name="experiment_manifest")
    return payload


def _validate_initial_state(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="initial_state")
    if _required_text(payload, "state_contract_version") != "orchestration_state_contract_v1":
        raise ValueError("initial_state.state_contract_version must be orchestration_state_contract_v1.")
    if _required_text(payload, "current_state") != "CREATED":
        raise ValueError("initial_state.current_state must be CREATED.")
    _require_sha256(payload, "output_payload_sha256", context="initial_state")
    _require_sha256(payload, "experiment_manifest_output_sha256", context="initial_state")
    artifact_hashes = _validate_artifact_hashes(payload.get("artifact_hashes"), allow_empty=False)
    _validate_safety_flags(payload, name="initial_state")
    return payload | {
        "experiment_id": _required_text(payload, "experiment_id"),
        "output_payload_sha256": payload["output_payload_sha256"],
        "experiment_manifest_output_sha256": payload["experiment_manifest_output_sha256"],
        "artifact_hashes": artifact_hashes,
        "state_contract_version": payload["state_contract_version"],
        "current_state": "CREATED",
    }


def _validate_steps(value: Any) -> list[dict[str, Any]]:
    items = _required_non_empty_list(value, name="steps")
    normalized: list[dict[str, Any]] = []
    seen_step_ids: set[str] = set()
    for item in items:
        payload = _required_mapping(item, name="step")
        _reject_unknown_fields(
            payload,
            allowed={"step_id", "target_state", "reason", "parent_output_sha256", "proposal_fingerprint", "artifact_hashes"},
            name="step",
        )
        step_id = _required_text(payload, "step_id")
        if step_id in seen_step_ids:
            raise ValueError("steps.step_id values must be unique.")
        seen_step_ids.add(step_id)
        target_state = _required_text(payload, "target_state")
        if target_state not in {
            "BASELINE_REVIEW_REQUIRED",
            "ROBUSTNESS_REVIEW_REQUIRED",
            "REVISION_REQUIRED",
            "RETEST_REQUIRED",
            "HUMAN_APPROVAL_REQUIRED",
            "ACCEPTED_REVIEW_ONLY",
            "REJECTED",
            "FAILED_VALIDATION",
        }:
            raise ValueError("steps.target_state is invalid.")
        parent_output_sha256 = payload.get("parent_output_sha256")
        if parent_output_sha256 != _PARENT_PREVIOUS_STEP:
            if not isinstance(parent_output_sha256, str) or not _SHA256_RE.fullmatch(parent_output_sha256):
                raise ValueError("steps.parent_output_sha256 must be a lowercase sha256 hex digest or STATE_FROM_PREVIOUS_STEP.")
        proposal_fingerprint = payload.get("proposal_fingerprint")
        if proposal_fingerprint is not None:
            if not isinstance(proposal_fingerprint, str) or not proposal_fingerprint.strip():
                raise ValueError("steps.proposal_fingerprint must be non-empty text when supplied.")
            proposal_fingerprint = proposal_fingerprint.strip()
        normalized.append(
            {
                "step_id": step_id,
                "target_state": target_state,
                "reason": _required_text(payload, "reason"),
                "parent_output_sha256": parent_output_sha256,
                "proposal_fingerprint": proposal_fingerprint,
                "artifact_hashes": _validate_artifact_hashes(payload.get("artifact_hashes"), allow_empty=True),
            }
        )
    return normalized


def _validate_artifact_hashes(value: Any, *, allow_empty: bool) -> dict[str, str]:
    payload = _required_mapping(value, name="artifact_hashes")
    normalized: dict[str, str] = {}
    for key, raw in sorted(payload.items()):
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("artifact_hashes keys must be non-empty text.")
        if not isinstance(raw, str) or not _SHA256_RE.fullmatch(raw):
            raise ValueError(f"artifact_hashes.{key_name} must be a lowercase sha256 hex digest.")
        normalized[key_name] = raw
    if not allow_empty and not normalized:
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


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
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
