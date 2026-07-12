from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


REQUEST_VERSION = "research_failure_memory_contract_request_v1"
MEMORY_VERSION = "research_failure_memory_contract_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_FAILURE_CATEGORIES = {
    "rejected_variant",
    "failed_parameter_region",
    "isolated_spike",
    "fold_failure",
    "insufficient_sample_failure",
    "dsr_failure",
    "excessive_pbo",
    "incomplete_trial_accounting",
    "drawdown_stress_failure",
    "risk_control_removal_attempt",
    "complexity_budget_violation",
    "exhausted_revisions",
}


def build_research_failure_memory_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    prior_memory = validated["prior_memory"]
    records = list(prior_memory["failure_records"]) if prior_memory is not None else []
    observation = validated["failure_observation"]
    duplicate_identity_detected = any(item["observation_id"] == observation["observation_id"] for item in records)
    duplicate_failure_detected = any(item["failure_fingerprint"] == observation["failure_fingerprint"] for item in records)
    novel_failure_recorded = not duplicate_failure_detected
    if novel_failure_recorded:
        records.append(observation)

    result: dict[str, Any] = {
        "memory_contract_version": MEMORY_VERSION,
        "experiment_id": validated["experiment_manifest"]["experiment_id"],
        "strategy_identity": validated["experiment_manifest"]["strategy_identity"],
        "failure_records": records,
        "latest_failure_fingerprint": observation["failure_fingerprint"],
        "novel_failure_recorded": novel_failure_recorded,
        "duplicate_failure_detected": duplicate_failure_detected,
        "duplicate_identity_detected": duplicate_identity_detected,
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
    _reject_unknown_fields(payload, allowed={"version", "experiment_manifest", "prior_memory", "failure_observation", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    experiment_manifest = _validate_experiment_manifest(payload.get("experiment_manifest"))
    prior_memory = _validate_prior_memory(payload.get("prior_memory"))
    failure_observation = _validate_failure_observation(payload.get("failure_observation"))
    if prior_memory is not None:
        if prior_memory["experiment_id"] != experiment_manifest["experiment_id"]:
            raise ValueError("prior_memory.experiment_id must match experiment_manifest.experiment_id.")
        if prior_memory["strategy_identity"] != experiment_manifest["strategy_identity"]:
            raise ValueError("prior_memory.strategy_identity must match experiment_manifest.strategy_identity.")
        prior_manifest_hash = prior_memory["failure_records"][0]["lineage_hashes"]["experiment_manifest_output_sha256"] if prior_memory["failure_records"] else None
        if prior_manifest_hash is not None and prior_manifest_hash != failure_observation["lineage_hashes"]["experiment_manifest_output_sha256"]:
            raise ValueError("failure lineage must preserve experiment_manifest_output_sha256.")
    return {
        "version": version,
        "experiment_manifest": experiment_manifest,
        "prior_memory": prior_memory,
        "failure_observation": failure_observation,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_experiment_manifest(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="experiment_manifest")
    if _required_text(payload, "manifest_version") != "experiment_manifest_contract_v1":
        raise ValueError("experiment_manifest.manifest_version must be experiment_manifest_contract_v1.")
    _validate_safety_flags(payload, name="experiment_manifest")
    return {
        "experiment_id": _required_text(payload, "experiment_id"),
        "strategy_identity": _validate_strategy_identity(payload.get("strategy_identity")),
    }


def _validate_prior_memory(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _required_mapping(value, name="prior_memory")
    _reject_unknown_fields(
        payload,
        allowed={
            "memory_contract_version",
            "experiment_id",
            "strategy_identity",
            "failure_records",
            "latest_failure_fingerprint",
            "novel_failure_recorded",
            "duplicate_failure_detected",
            "duplicate_identity_detected",
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
        name="prior_memory",
    )
    if _required_text(payload, "memory_contract_version") != MEMORY_VERSION:
        raise ValueError("prior_memory.memory_contract_version must be research_failure_memory_contract_v1.")
    _validate_safety_flags(payload, name="prior_memory")
    return {
        "experiment_id": _required_text(payload, "experiment_id"),
        "strategy_identity": _validate_strategy_identity(payload.get("strategy_identity")),
        "failure_records": _validate_failure_records(payload.get("failure_records")),
    }


def _validate_failure_observation(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="failure_observation")
    _reject_unknown_fields(
        payload,
        allowed={"observation_id", "variant_id", "failure_category", "parameter_region", "lineage_hashes", "evidence_hashes", "notes", "failure_fingerprint"},
        name="failure_observation",
    )
    parameter_region = _validate_parameter_region(payload.get("parameter_region"))
    lineage_hashes = _validate_hash_mapping(payload.get("lineage_hashes"), name="lineage_hashes")
    evidence_hashes = _validate_hash_mapping(payload.get("evidence_hashes"), name="evidence_hashes")
    notes = _required_text_list(payload.get("notes"), name="notes")
    failure_category = _required_text(payload, "failure_category")
    if failure_category not in _ALLOWED_FAILURE_CATEGORIES:
        raise ValueError("failure_category is invalid.")
    semantic_fingerprint_payload = {
        "variant_id": _required_text(payload, "variant_id"),
        "failure_category": failure_category,
        "parameter_region": parameter_region,
        "lineage_hashes": lineage_hashes,
    }
    return {
        "observation_id": _required_text(payload, "observation_id"),
        "variant_id": semantic_fingerprint_payload["variant_id"],
        "failure_category": failure_category,
        "parameter_region": parameter_region,
        "lineage_hashes": lineage_hashes,
        "evidence_hashes": evidence_hashes,
        "notes": notes,
        "failure_fingerprint": _canonical_sha256(semantic_fingerprint_payload),
    }


def _validate_failure_records(value: Any) -> list[dict[str, Any]]:
    items = _required_non_empty_list(value, name="failure_records")
    return [_validate_failure_record(item) for item in items]


def _validate_failure_record(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="failure_record")
    _reject_unknown_fields(
        payload,
        allowed={"observation_id", "variant_id", "failure_category", "parameter_region", "lineage_hashes", "evidence_hashes", "notes", "failure_fingerprint"},
        name="failure_record",
    )
    normalized = _validate_failure_observation(payload)
    if payload.get("failure_fingerprint") != normalized["failure_fingerprint"]:
        raise ValueError("failure_record.failure_fingerprint must match the semantic failure fingerprint.")
    return normalized


def _validate_hash_mapping(value: Any, *, name: str) -> dict[str, str]:
    payload = _required_mapping(value, name=name)
    normalized: dict[str, str] = {}
    for key, raw in sorted(payload.items()):
        key_name = str(key).strip()
        if not key_name:
            raise ValueError(f"{name} keys must be non-empty text.")
        if not isinstance(raw, str) or not _SHA256_RE.fullmatch(raw):
            raise ValueError(f"{name}.{key_name} must be a lowercase sha256 hex digest.")
        normalized[key_name] = raw
    if not normalized:
        raise ValueError(f"{name} must not be empty.")
    return normalized


def _validate_parameter_region(value: Any) -> dict[str, str | int | float | None | bool]:
    payload = _required_mapping(value, name="parameter_region")
    if not payload:
        raise ValueError("parameter_region must not be empty.")
    normalized: dict[str, str | int | float | None | bool] = {}
    for key, raw in sorted(payload.items()):
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("parameter_region keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"parameter_region.{key_name}")
    return normalized


def _validate_strategy_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="strategy_identity")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": _required_text(payload, "strategy_builder"),
        "strategy_version": _required_text(payload, "strategy_version"),
    }


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


def _required_text_list(value: Any, *, name: str) -> list[str]:
    items = _required_non_empty_list(value, name=name)
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty text.")
        normalized.append(item.strip())
    return normalized


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
