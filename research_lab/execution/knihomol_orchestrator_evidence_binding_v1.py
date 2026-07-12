from __future__ import annotations

from typing import Any

from research_lab.execution.experiment_manifest_contract_v1 import (
    _canonical_sha256,
    _reject_unknown_fields,
    _required_mapping,
    _required_text,
    _required_unique_text_list,
    _validate_provenance,
)


REQUEST_VERSION = "knihomol_orchestrator_evidence_binding_request_v1"
RESULT_VERSION = "knihomol_orchestrator_evidence_binding_result_v1"
BRIDGE_VERSION = "knihomol_orchestrator_evidence_binding_v1"
MAPPING_POLICY_VERSION = "knihomol_orchestrator_evidence_mapping_policy_v1"
SOURCE_RESULT_VERSION = "knihomol_readonly_evidence_adapter_result_v1"
SOURCE_ADAPTER_VERSION = "knihomol_readonly_evidence_adapter_v1"
_SUPPORTS_BY_BLOCKER = {
    "drawdown_fail": ["drawdown"],
    "walk_forward_fail": ["walk_forward"],
}


def build_knihomol_orchestrator_evidence_binding(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    adapter_result = validated["adapter_result"]
    adapter_notes = _validate_adapter_notes(adapter_result.get("notes"))
    recomputed_content_sha256 = _canonical_sha256(adapter_notes)
    if adapter_result["content_sha256"] != recomputed_content_sha256:
        raise ValueError("adapter_result.content_sha256 does not match adapter_result.notes.")
    if validated["expected_adapter_content_sha256"] != recomputed_content_sha256:
        raise ValueError("expected_adapter_content_sha256 does not match adapter_result.notes.")

    adapter_requested_ids = _required_unique_text_list(
        adapter_result.get("requested_note_ids"),
        name="adapter_result.requested_note_ids",
    )
    expected_note_ids = validated["expected_note_ids"]
    if sorted(adapter_requested_ids) != expected_note_ids:
        raise ValueError("expected_note_ids must exactly match adapter_result.requested_note_ids after normalization.")

    note_ids = sorted(note["note_id"] for note in adapter_notes)
    if note_ids != expected_note_ids:
        raise ValueError("expected_note_ids must exactly match adapter_result.notes note_id values.")

    shallow_notes = [_map_note_to_validated_evidence(note) for note in sorted(adapter_notes, key=lambda item: item["note_id"])]
    lineage = [
        {
            "note_id": note["note_id"],
            "blocker": note["blocker"],
            "book_id": note["book_id"],
            "source_sha256": note["source_sha256"],
            "source_passage_id": note["source_passage_id"],
        }
        for note in sorted(adapter_notes, key=lambda item: item["note_id"])
    ]
    mapping_table = [
        {
            "blocker": blocker,
            "topic": blocker,
            "supports": list(supports),
            "summary_source_field": "implementation_hint",
        }
        for blocker, supports in sorted(_SUPPORTS_BY_BLOCKER.items())
    ]
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "mapping_policy_version": validated["mapping_policy_version"],
        "validated_knihomol_evidence": {"notes": shallow_notes},
        "source_adapter_content_sha256": recomputed_content_sha256,
        "source_note_ids": note_ids,
        "source_note_lineage": lineage,
        "mapping_table": mapping_table,
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result["input_sha256"] = _canonical_sha256(validated)
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "adapter_result",
            "expected_note_ids",
            "expected_adapter_content_sha256",
            "mapping_policy_version",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    adapter_result = _validate_adapter_result(payload.get("adapter_result"))
    mapping_policy_version = _required_text(payload, "mapping_policy_version")
    if mapping_policy_version != MAPPING_POLICY_VERSION:
        raise ValueError(f"mapping_policy_version must be {MAPPING_POLICY_VERSION}.")
    expected_note_ids = sorted(
        _required_unique_text_list(payload.get("expected_note_ids"), name="expected_note_ids")
    )
    expected_adapter_content_sha256 = _required_sha256(
        payload.get("expected_adapter_content_sha256"),
        name="expected_adapter_content_sha256",
    )
    return {
        "version": version,
        "adapter_result": adapter_result,
        "expected_note_ids": expected_note_ids,
        "expected_adapter_content_sha256": expected_adapter_content_sha256,
        "mapping_policy_version": mapping_policy_version,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_adapter_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="adapter_result")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "adapter_version",
            "status",
            "evidence_purpose",
            "requested_note_ids",
            "notes",
            "content_sha256",
            "source_hashes",
            "corpus_files_unchanged",
            "writes_performed",
            "promotion_performed",
            "provider_calls_used",
            "network_used",
            "production_runtime_supported",
            "provenance",
        },
        name="adapter_result",
    )
    if _required_text(payload, "version") != SOURCE_RESULT_VERSION:
        raise ValueError(f"adapter_result.version must be {SOURCE_RESULT_VERSION}.")
    if _required_text(payload, "adapter_version") != SOURCE_ADAPTER_VERSION:
        raise ValueError(f"adapter_result.adapter_version must be {SOURCE_ADAPTER_VERSION}.")
    if _required_text(payload, "status") != "SUCCESS":
        raise ValueError("adapter_result.status must be SUCCESS.")
    if payload.get("corpus_files_unchanged") is not True:
        raise ValueError("adapter_result.corpus_files_unchanged must be true.")
    if payload.get("writes_performed") is not False:
        raise ValueError("adapter_result.writes_performed must be false.")
    if payload.get("promotion_performed") is not False:
        raise ValueError("adapter_result.promotion_performed must be false.")
    if payload.get("provider_calls_used") != 0:
        raise ValueError("adapter_result.provider_calls_used must be 0.")
    if payload.get("network_used") is not False:
        raise ValueError("adapter_result.network_used must be false.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError("adapter_result.production_runtime_supported must be false.")
    _required_sha256(payload.get("content_sha256"), name="adapter_result.content_sha256")
    _validate_provenance(payload.get("provenance"))
    return dict(payload)


def _validate_adapter_notes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("adapter_result.notes must be a non-empty list.")
    normalized: list[dict[str, Any]] = []
    seen_note_ids: set[str] = set()
    for item in value:
        note = _required_mapping(item, name="adapter_result.note")
        _reject_unknown_fields(
            note,
            allowed={
                "note_id",
                "blocker",
                "book_id",
                "source_title",
                "source_sha256",
                "source_passage_id",
                "source_location",
                "testable_rules",
                "compatible_builders",
                "implementation_hint",
                "priority_score",
            },
            name="adapter_result.note",
        )
        note_id = _required_text(note, "note_id")
        if note_id in seen_note_ids:
            raise ValueError("adapter_result.notes note_id values must be unique.")
        seen_note_ids.add(note_id)
        blocker = _required_text(note, "blocker")
        if blocker not in _SUPPORTS_BY_BLOCKER:
            raise ValueError(f"adapter_result.note.blocker is unsupported in V1: {blocker}")
        implementation_hint = _required_text(note, "implementation_hint")
        normalized.append(
            {
                "note_id": note_id,
                "blocker": blocker,
                "book_id": _required_text(note, "book_id"),
                "source_title": _required_text(note, "source_title"),
                "source_sha256": _required_sha256(note.get("source_sha256"), name="adapter_result.note.source_sha256"),
                "source_passage_id": _required_text(note, "source_passage_id"),
                "source_location": _required_text(note, "source_location"),
                "testable_rules": _required_text_list(note.get("testable_rules"), name="adapter_result.note.testable_rules"),
                "compatible_builders": _required_text_list(
                    note.get("compatible_builders"),
                    name="adapter_result.note.compatible_builders",
                ),
                "implementation_hint": implementation_hint,
                "priority_score": _required_finite_number(note, "priority_score"),
            }
        )
    return normalized


def _map_note_to_validated_evidence(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "note_id": note["note_id"],
        "status": "validated",
        "topic": note["blocker"],
        "summary": note["implementation_hint"],
        "supports": list(_SUPPORTS_BY_BLOCKER[note["blocker"]]),
    }


def _required_text_list(value: Any, *, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must contain non-empty text values.")
        normalized.append(item.strip())
    return normalized


def _required_sha256(value: Any, *, name: str) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{name} must be a lowercase sha256 hex digest.")
    return text


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite.")
    return number
