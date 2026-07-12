from __future__ import annotations

import re
from typing import Any

from research_lab.execution.e2e_research_orchestrator_acceptance_v1 import (
    _validate_request as _validate_orchestrator_request,
)
from research_lab.execution.experiment_manifest_contract_v1 import (
    _canonical_sha256,
    _reject_unknown_fields,
    _required_mapping,
    _required_text,
    _required_unique_text_list,
    _validate_dataset_identity,
    _validate_provenance,
    _validate_strategy_identity,
    build_experiment_manifest_contract,
)


REQUEST_VERSION = "orchestrator_run_bundle_contract_request_v1"
CONTRACT_VERSION = "orchestrator_run_bundle_contract_v1"
MANIFEST_VERSION = "orchestrator_run_bundle_manifest_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_orchestrator_run_bundle_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    canonical_request_sha256 = _canonical_sha256(validated["normalized_request"])
    bundle_manifest = {
        "bundle_manifest_version": MANIFEST_VERSION,
        "run_id": validated["run_id"],
        "canonical_request_sha256": canonical_request_sha256,
        "request_source_metadata": validated["request_source_metadata"],
        "source_artifact_hashes": validated["supplied_input_artifact_hashes"],
        "expected_identities": validated["expected_identities"],
        "execution_authority_granted": False,
        "persistence_authority_granted": False,
        "filesystem_access_performed": False,
        "clock_reads_used": 0,
        "random_identifiers_used": 0,
        "provider_calls_used": 0,
        "external_data_used": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result: dict[str, Any] = {
        "bundle_contract_version": CONTRACT_VERSION,
        "run_id": validated["run_id"],
        "normalized_request": validated["normalized_request"],
        "canonical_request_sha256": canonical_request_sha256,
        "request_source_metadata": validated["request_source_metadata"],
        "source_artifact_hashes": validated["supplied_input_artifact_hashes"],
        "expected_identities": validated["expected_identities"],
        "bundle_manifest": bundle_manifest,
        "bundle_manifest_sha256": _canonical_sha256(bundle_manifest),
        "execution_authority_granted": False,
        "persistence_authority_granted": False,
        "filesystem_access_performed": False,
        "clock_reads_used": 0,
        "random_identifiers_used": 0,
        "provider_calls_used": 0,
        "external_data_used": False,
        "production_runtime_supported": False,
        "input_sha256": _canonical_sha256(validated),
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
            "run_id",
            "orchestrator_request",
            "request_source_metadata",
            "supplied_input_artifact_hashes",
            "expected_experiment_id",
            "expected_strategy_identity",
            "expected_dataset_identity",
            "expected_knihomol_evidence_ids",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    run_id = _required_text(payload, "run_id")
    orchestrator_request = _validate_orchestrator_request(
        _required_mapping(payload.get("orchestrator_request"), name="orchestrator_request")
    )
    manifest = build_experiment_manifest_contract(
        {
            **orchestrator_request["experiment_manifest_request"],
            "provenance": orchestrator_request["provenance"],
        }
    )
    request_source_metadata = _validate_request_source_metadata(payload.get("request_source_metadata"))
    supplied_input_artifact_hashes = _validate_supplied_input_artifact_hashes(payload.get("supplied_input_artifact_hashes"))
    expected_experiment_id = _required_text(payload, "expected_experiment_id")
    expected_strategy_identity = _validate_strategy_identity(payload.get("expected_strategy_identity"))
    expected_dataset_identity = _validate_dataset_identity(payload.get("expected_dataset_identity"))
    expected_knihomol_evidence_ids = _required_unique_text_list(
        payload.get("expected_knihomol_evidence_ids"),
        name="expected_knihomol_evidence_ids",
    )
    _validate_identity_consistency(
        expected_experiment_id=expected_experiment_id,
        expected_strategy_identity=expected_strategy_identity,
        expected_dataset_identity=expected_dataset_identity,
        expected_knihomol_evidence_ids=expected_knihomol_evidence_ids,
        orchestrator_request=orchestrator_request,
        manifest=manifest,
    )
    return {
        "version": version,
        "run_id": run_id,
        "normalized_request": orchestrator_request,
        "request_source_metadata": request_source_metadata,
        "supplied_input_artifact_hashes": supplied_input_artifact_hashes,
        "expected_identities": {
            "experiment_id": expected_experiment_id,
            "strategy_identity": expected_strategy_identity,
            "dataset_identity": expected_dataset_identity,
            "knihomol_evidence_ids": expected_knihomol_evidence_ids,
        },
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_request_source_metadata(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="request_source_metadata")
    _reject_unknown_fields(
        payload,
        allowed={"source_type", "source_path", "source_sha256"},
        name="request_source_metadata",
    )
    source_sha256 = _required_text(payload, "source_sha256")
    if not _SHA256_RE.fullmatch(source_sha256):
        raise ValueError("request_source_metadata.source_sha256 must be a lowercase sha256 hex digest.")
    return {
        "source_type": _required_text(payload, "source_type"),
        "source_path": _required_text(payload, "source_path"),
        "source_sha256": source_sha256,
    }


def _validate_supplied_input_artifact_hashes(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="supplied_input_artifact_hashes")
    if not payload:
        raise ValueError("supplied_input_artifact_hashes must not be empty.")
    normalized: dict[str, str] = {}
    for field, raw in payload.items():
        if not isinstance(field, str) or not field.strip():
            raise ValueError("supplied_input_artifact_hashes keys must be non-empty text.")
        digest = raw.strip() if isinstance(raw, str) else ""
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"supplied_input_artifact_hashes.{field} must be a lowercase sha256 hex digest.")
        normalized[field] = digest
    return normalized


def _validate_identity_consistency(
    *,
    expected_experiment_id: str,
    expected_strategy_identity: dict[str, str],
    expected_dataset_identity: dict[str, Any],
    expected_knihomol_evidence_ids: list[str],
    orchestrator_request: dict[str, Any],
    manifest: dict[str, object],
) -> None:
    manifest_request = orchestrator_request["experiment_manifest_request"]
    if expected_experiment_id != manifest_request["experiment_id"]:
        raise ValueError("expected_experiment_id must match orchestrator_request.experiment_manifest_request.experiment_id.")
    if expected_strategy_identity != manifest_request["strategy_identity"]:
        raise ValueError("expected_strategy_identity must match orchestrator_request.experiment_manifest_request.strategy_identity.")
    if expected_dataset_identity != manifest_request["dataset_identity"]:
        raise ValueError("expected_dataset_identity must match orchestrator_request.experiment_manifest_request.dataset_identity.")
    if manifest["experiment_id"] != expected_experiment_id:
        raise ValueError("expected_experiment_id must match the normalized experiment manifest.")

    robustness_strategy_identity = orchestrator_request["robustness_pipeline_request"]["strategy_identity"]
    if robustness_strategy_identity["strategy_id"] != expected_strategy_identity["strategy_id"]:
        raise ValueError("robustness_pipeline_request.strategy_identity.strategy_id must match expected_strategy_identity.strategy_id.")
    if robustness_strategy_identity["strategy_builder"] != expected_strategy_identity["strategy_builder"]:
        raise ValueError("robustness_pipeline_request.strategy_identity.strategy_builder must match expected_strategy_identity.strategy_builder.")

    manifest_knowledge_note_ids = list(manifest_request["knowledge_note_ids"])
    evidence_ids = _extract_knihomol_evidence_ids(orchestrator_request["robustness_pipeline_request"])
    if expected_knihomol_evidence_ids != manifest_knowledge_note_ids:
        raise ValueError("expected_knihomol_evidence_ids must exactly match experiment_manifest_request.knowledge_note_ids.")
    if expected_knihomol_evidence_ids != evidence_ids:
        raise ValueError("expected_knihomol_evidence_ids must exactly match robustness_pipeline_request validated Knihomol evidence IDs.")


def _extract_knihomol_evidence_ids(robustness_pipeline_request: dict[str, Any]) -> list[str]:
    try:
        notes = robustness_pipeline_request["robustness_review_inputs"]["validated_knihomol_evidence"]["notes"]
    except KeyError as exc:
        raise ValueError("robustness_pipeline_request.robustness_review_inputs.validated_knihomol_evidence.notes is required.") from exc
    if not isinstance(notes, list) or not notes:
        raise ValueError("robustness_pipeline_request.robustness_review_inputs.validated_knihomol_evidence.notes must be non-empty.")
    evidence_ids: list[str] = []
    seen_ids: set[str] = set()
    for item in notes:
        payload = _required_mapping(item, name="validated_knihomol_evidence_note")
        note_id = _required_text(payload, "note_id")
        if note_id in seen_ids:
            raise ValueError("validated Knihomol evidence note_id values must not contain duplicate entries.")
        seen_ids.add(note_id)
        evidence_ids.append(note_id)
    return evidence_ids
