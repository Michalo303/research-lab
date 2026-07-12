from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.bounded_revise_retest_loop_v1 import (
    run_bounded_revise_retest_loop,
)
from research_lab.execution.e2e_review_pipeline_acceptance_v1 import (
    run_e2e_review_pipeline_acceptance,
)
from research_lab.execution.experiment_manifest_contract_v1 import (
    build_experiment_manifest_contract,
)
from research_lab.execution.human_approval_gate_v1 import (
    build_human_approval_gate,
)
from research_lab.execution.orchestration_state_contract_v1 import (
    build_orchestration_state_contract,
)
from research_lab.execution.research_failure_memory_contract_v1 import (
    build_research_failure_memory_contract,
)


REQUEST_VERSION = "e2e_research_orchestrator_acceptance_request_v1"
RESULT_VERSION = "e2e_research_orchestrator_acceptance_result_v1"
ORCHESTRATOR_VERSION = "e2e_research_orchestrator_acceptance_v1"
_AUTO_PARENT = "STATE_FROM_PREVIOUS_STEP"
_AUTO_BASELINE_STEP_ID = "__auto_baseline_review_required"
_AUTO_ROBUSTNESS_STEP_ID = "__auto_robustness_review_required"
_AUTO_POST_ROBUSTNESS_STEP_ID = "__auto_post_robustness_transition"


def run_e2e_research_orchestrator_acceptance(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)

    experiment_manifest = build_experiment_manifest_contract(
        {
            **validated["experiment_manifest_request"],
            "provenance": validated["provenance"],
        }
    )
    initial_state = build_orchestration_state_contract(
        {
            "version": validated["initial_orchestration_state_request"]["version"],
            "experiment_manifest": experiment_manifest,
            "previous_state": None,
            "target_state": validated["initial_orchestration_state_request"]["target_state"],
            "reason": validated["initial_orchestration_state_request"]["reason"],
            "artifact_hashes": {
                "experiment_manifest_output_sha256": experiment_manifest["output_payload_sha256"],
            },
            "provenance": validated["provenance"],
        }
    )
    robustness_pipeline_result = run_e2e_review_pipeline_acceptance(
        {
            **validated["robustness_pipeline_request"],
            "provenance": validated["provenance"],
        }
    )
    _validate_pipeline_alignment(
        validated=validated,
        experiment_manifest=experiment_manifest,
        robustness_pipeline_result=robustness_pipeline_result,
    )

    loop_result = run_bounded_revise_retest_loop(
        {
            "version": validated["revise_retest_request"]["version"],
            "experiment_manifest": experiment_manifest,
            "initial_state": initial_state,
            "steps": _compose_loop_steps(
                experiment_manifest=experiment_manifest,
                robustness_pipeline_result=robustness_pipeline_result,
                user_steps=validated["revise_retest_request"]["steps"],
            ),
            "provenance": validated["provenance"],
        }
    )
    if loop_result["final_state"] == "ACCEPTED_REVIEW_ONLY":
        raise ValueError("revise_retest_request must not bypass human approval with ACCEPTED_REVIEW_ONLY.")

    failure_memory_result: dict[str, Any] | None = None
    if loop_result["final_state"] in {"REJECTED", "EXHAUSTED", "FAILED_VALIDATION"}:
        failure_memory_result = build_research_failure_memory_contract(
            {
                "version": validated["failure_memory_request"]["version"],
                "experiment_manifest": experiment_manifest,
                "prior_memory": validated["failure_memory_request"]["prior_memory"],
                "failure_observation": _validated_failure_observation(
                    failure_observation=validated["failure_memory_request"]["failure_observation"],
                    experiment_manifest=experiment_manifest,
                    robustness_pipeline_result=robustness_pipeline_result,
                ),
                "provenance": validated["provenance"],
            }
        )

    human_approval_result: dict[str, Any] | None = None
    final_orchestration_state = loop_result["final_state_artifact"]
    final_status = loop_result["final_state"]

    if loop_result["final_state"] == "HUMAN_APPROVAL_REQUIRED":
        human_approval_result = build_human_approval_gate(
            {
                "version": validated["human_approval_request"]["version"],
                "experiment_manifest": experiment_manifest,
                "robustness_decision_result": robustness_pipeline_result["robustness_decision_result"],
                "orchestration_state": loop_result["final_state_artifact"],
                "selected_variant_id": validated["human_approval_request"]["selected_variant_id"],
                "approval_action": validated["human_approval_request"]["approval_action"],
                "reviewer_identity": validated["human_approval_request"]["reviewer_identity"],
                "approval_timestamp": validated["human_approval_request"]["approval_timestamp"],
                "expiry_policy": validated["human_approval_request"]["expiry_policy"],
                "prior_approval": validated["human_approval_request"]["prior_approval"],
                "provenance": validated["provenance"],
            }
        )
        approval_status = human_approval_result["approval_status"]
        prior_approval = validated["human_approval_request"]["prior_approval"]
        if prior_approval is not None and prior_approval.get("approval_status") == "REJECTED_BY_HUMAN":
            approval_status = "REJECTED_BY_HUMAN"
        if approval_status == "APPROVAL_REQUIRED":
            final_status = "HUMAN_APPROVAL_REQUIRED"
        else:
            target_state, reason = _approval_transition(approval_status)
            final_orchestration_state = build_orchestration_state_contract(
                {
                    "version": "orchestration_state_contract_request_v1",
                    "experiment_manifest": experiment_manifest,
                    "previous_state": loop_result["final_state_artifact"],
                    "target_state": target_state,
                    "reason": reason,
                    "artifact_hashes": {
                        **loop_result["final_state_artifact"]["artifact_hashes"],
                        "human_approval_output_sha256": human_approval_result["output_payload_sha256"],
                    },
                    "provenance": validated["provenance"],
                }
            )
            final_status = final_orchestration_state["current_state"]

    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "orchestrator_version": ORCHESTRATOR_VERSION,
        "experiment_manifest": experiment_manifest,
        "initial_orchestration_state": initial_state,
        "robustness_pipeline_result": robustness_pipeline_result,
        "loop_result": loop_result,
        "failure_memory_result": failure_memory_result,
        "human_approval_result": human_approval_result,
        "final_orchestration_state": final_orchestration_state,
        "final_status": final_status,
        "selected_variant_id": robustness_pipeline_result["robustness_decision_result"]["selected_variant_id"],
        "lineage": {
            "experiment_id": experiment_manifest["experiment_id"],
            "strategy_id": experiment_manifest["strategy_identity"]["strategy_id"],
            "strategy_version": experiment_manifest["strategy_identity"]["strategy_version"],
            "experiment_manifest_output_sha256": experiment_manifest["output_payload_sha256"],
            "robustness_decision_output_sha256": robustness_pipeline_result["robustness_decision_result"]["output_payload_sha256"],
            "orchestration_state_output_sha256": final_orchestration_state["output_payload_sha256"],
            "selected_variant_id": robustness_pipeline_result["robustness_decision_result"]["selected_variant_id"],
        },
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "generated_code_executed": False,
        "external_data_used": False,
        "automatic_strategy_application_performed": False,
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
            "experiment_manifest_request",
            "initial_orchestration_state_request",
            "robustness_pipeline_request",
            "revise_retest_request",
            "failure_memory_request",
            "human_approval_request",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    experiment_manifest_request = _validate_experiment_manifest_request(payload.get("experiment_manifest_request"))
    robustness_pipeline_request = _validate_robustness_pipeline_request(
        payload.get("robustness_pipeline_request"),
        expected_strategy_identity=experiment_manifest_request["strategy_identity"],
    )
    human_approval_request = _validate_human_approval_request(payload.get("human_approval_request"))
    if human_approval_request["selected_variant_id"] and human_approval_request["selected_variant_id"] != robustness_pipeline_request["expected_selected_variant_id"]:
        raise ValueError("human_approval_request.selected_variant_id must match robustness_pipeline_request.expected_selected_variant_id.")
    return {
        "version": version,
        "experiment_manifest_request": experiment_manifest_request["request"],
        "initial_orchestration_state_request": _validate_initial_orchestration_state_request(payload.get("initial_orchestration_state_request")),
        "robustness_pipeline_request": robustness_pipeline_request["request"],
        "revise_retest_request": _validate_revise_retest_request(payload.get("revise_retest_request")),
        "failure_memory_request": _validate_failure_memory_request(payload.get("failure_memory_request")),
        "human_approval_request": human_approval_request,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_experiment_manifest_request(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_manifest_request")
    strategy_identity = _required_mapping(payload.get("strategy_identity"), name="experiment_manifest_request.strategy_identity")
    return {
        "request": payload,
        "strategy_identity": {
            "strategy_id": _required_text(strategy_identity, "strategy_id"),
            "strategy_builder": _required_text(strategy_identity, "strategy_builder"),
            "strategy_version": _required_text(strategy_identity, "strategy_version"),
        },
    }


def _validate_initial_orchestration_state_request(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="initial_orchestration_state_request")
    _reject_unknown_fields(payload, allowed={"version", "target_state", "reason"}, name="initial_orchestration_state_request")
    if _required_text(payload, "version") != "orchestration_state_contract_request_v1":
        raise ValueError("initial_orchestration_state_request.version must be orchestration_state_contract_request_v1.")
    target_state = _required_text(payload, "target_state")
    if target_state != "CREATED":
        raise ValueError("initial_orchestration_state_request.target_state must be CREATED.")
    return {
        "version": payload["version"],
        "target_state": target_state,
        "reason": _required_text(payload, "reason"),
    }


def _validate_robustness_pipeline_request(value: Any, *, expected_strategy_identity: dict[str, str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_pipeline_request")
    strategy_identity = _required_mapping(payload.get("strategy_identity"), name="robustness_pipeline_request.strategy_identity")
    strategy_id = _required_text(strategy_identity, "strategy_id")
    strategy_builder = _required_text(strategy_identity, "strategy_builder")
    if strategy_id != expected_strategy_identity["strategy_id"]:
        raise ValueError("robustness_pipeline_request.strategy_identity.strategy_id must match experiment_manifest_request.strategy_identity.strategy_id.")
    if strategy_builder != expected_strategy_identity["strategy_builder"]:
        raise ValueError("robustness_pipeline_request.strategy_identity.strategy_builder must match experiment_manifest_request.strategy_identity.strategy_builder.")
    return {
        "request": payload,
        "expected_selected_variant_id": _required_text(strategy_identity, "baseline_variant_id"),
    }


def _validate_revise_retest_request(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="revise_retest_request")
    _reject_unknown_fields(payload, allowed={"version", "steps"}, name="revise_retest_request")
    if _required_text(payload, "version") != "bounded_revise_retest_loop_request_v1":
        raise ValueError("revise_retest_request.version must be bounded_revise_retest_loop_request_v1.")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise ValueError("revise_retest_request.steps must be a list.")
    return {
        "version": payload["version"],
        "steps": list(steps),
    }


def _validate_failure_memory_request(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="failure_memory_request")
    _reject_unknown_fields(payload, allowed={"version", "prior_memory", "failure_observation"}, name="failure_memory_request")
    if _required_text(payload, "version") != "research_failure_memory_contract_request_v1":
        raise ValueError("failure_memory_request.version must be research_failure_memory_contract_request_v1.")
    return {
        "version": payload["version"],
        "prior_memory": payload.get("prior_memory"),
        "failure_observation": _required_mapping(payload.get("failure_observation"), name="failure_memory_request.failure_observation"),
    }


def _validate_human_approval_request(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="human_approval_request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "selected_variant_id", "approval_action", "reviewer_identity", "approval_timestamp", "expiry_policy", "prior_approval"},
        name="human_approval_request",
    )
    if _required_text(payload, "version") != "human_approval_gate_request_v1":
        raise ValueError("human_approval_request.version must be human_approval_gate_request_v1.")
    return {
        "version": payload["version"],
        "selected_variant_id": _required_text(payload, "selected_variant_id"),
        "approval_action": _required_text(payload, "approval_action"),
        "reviewer_identity": _required_mapping(payload.get("reviewer_identity"), name="human_approval_request.reviewer_identity"),
        "approval_timestamp": payload.get("approval_timestamp"),
        "expiry_policy": _required_mapping(payload.get("expiry_policy"), name="human_approval_request.expiry_policy"),
        "prior_approval": payload.get("prior_approval"),
    }


def _validate_pipeline_alignment(
    *,
    validated: dict[str, Any],
    experiment_manifest: dict[str, Any],
    robustness_pipeline_result: dict[str, Any],
) -> None:
    strategy_identity = experiment_manifest["strategy_identity"]
    pipeline_strategy_identity = robustness_pipeline_result["robustness_decision_result"]["strategy_identity"]
    if pipeline_strategy_identity["strategy_id"] != strategy_identity["strategy_id"]:
        raise ValueError("robustness decision strategy_id must match experiment manifest strategy_id.")
    if pipeline_strategy_identity["strategy_builder"] != strategy_identity["strategy_builder"]:
        raise ValueError("robustness decision strategy_builder must match experiment manifest strategy_builder.")
    if validated["human_approval_request"]["selected_variant_id"] != robustness_pipeline_result["robustness_decision_result"]["selected_variant_id"]:
        raise ValueError("human_approval_request.selected_variant_id must match robustness_decision_result.selected_variant_id.")


def _compose_loop_steps(
    *,
    experiment_manifest: dict[str, Any],
    robustness_pipeline_result: dict[str, Any],
    user_steps: list[object],
) -> list[dict[str, Any]]:
    manifest_hash = experiment_manifest["output_payload_sha256"]
    robustness_hash = robustness_pipeline_result["robustness_decision_result"]["output_payload_sha256"]
    decision_status = robustness_pipeline_result["robustness_decision_result"]["decision_status"]
    derived_target, derived_reason = _derived_transition(decision_status)
    auto_steps: list[dict[str, Any]] = [
        {
            "step_id": _AUTO_BASELINE_STEP_ID,
            "target_state": "BASELINE_REVIEW_REQUIRED",
            "reason": "baseline_review_required",
            "parent_output_sha256": _AUTO_PARENT,
            "proposal_fingerprint": None,
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest_hash,
            },
        },
        {
            "step_id": _AUTO_ROBUSTNESS_STEP_ID,
            "target_state": "ROBUSTNESS_REVIEW_REQUIRED",
            "reason": "robustness_review_required",
            "parent_output_sha256": _AUTO_PARENT,
            "proposal_fingerprint": None,
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest_hash,
                "robustness_decision_output_sha256": robustness_hash,
            },
        },
    ]
    normalized_user_steps = [dict(_required_mapping(step, name="revise_retest_step")) for step in user_steps]
    if normalized_user_steps:
        return auto_steps + normalized_user_steps
    auto_steps.append(
        {
            "step_id": _AUTO_POST_ROBUSTNESS_STEP_ID,
            "target_state": derived_target,
            "reason": derived_reason,
            "parent_output_sha256": _AUTO_PARENT,
            "proposal_fingerprint": None,
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest_hash,
                "robustness_decision_output_sha256": robustness_hash,
            },
        }
    )
    return auto_steps


def _validated_failure_observation(
    *,
    failure_observation: dict[str, Any],
    experiment_manifest: dict[str, Any],
    robustness_pipeline_result: dict[str, Any],
) -> dict[str, Any]:
    lineage_hashes = _required_mapping(failure_observation.get("lineage_hashes"), name="failure_observation.lineage_hashes")
    manifest_hash = lineage_hashes.get("experiment_manifest_output_sha256")
    robustness_hash = lineage_hashes.get("robustness_decision_output_sha256")
    if manifest_hash != experiment_manifest["output_payload_sha256"]:
        raise ValueError("failure_memory_request.failure_observation.lineage_hashes.experiment_manifest_output_sha256 must match experiment manifest hash.")
    if robustness_hash != robustness_pipeline_result["robustness_decision_result"]["output_payload_sha256"]:
        raise ValueError("failure_memory_request.failure_observation.lineage_hashes.robustness_decision_output_sha256 must match robustness decision hash.")
    return failure_observation


def _derived_transition(decision_status: str) -> tuple[str, str]:
    if decision_status in {"PASS", "PASS_WITH_SIMPLIFICATION"}:
        return "HUMAN_APPROVAL_REQUIRED", "robustness_review_passed"
    if decision_status == "REVISE":
        return "REVISION_REQUIRED", "robustness_revision_required"
    if decision_status in {"REJECT_OVERFIT", "REJECT_RISK"}:
        return "REJECTED", "robustness_review_rejected"
    raise ValueError("robustness decision status is unsupported for orchestration.")


def _approval_transition(approval_status: str) -> tuple[str, str]:
    if approval_status == "APPROVED_FOR_NEXT_REVIEW_STAGE":
        return "ACCEPTED_REVIEW_ONLY", "human_approval_granted"
    if approval_status == "REJECTED_BY_HUMAN":
        return "REJECTED", "human_approval_rejected"
    if approval_status == "EXPIRED":
        return "FAILED_VALIDATION", "human_approval_expired"
    if approval_status == "FAILED_VALIDATION":
        return "FAILED_VALIDATION", "human_approval_invalid"
    raise ValueError("approval status is unsupported for orchestration transition.")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
