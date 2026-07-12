from __future__ import annotations

import hashlib
import json
from typing import Any


REQUEST_VERSION = "immutable_macro_snapshot_contract_request_v1"
RESULT_VERSION = "immutable_macro_snapshot_contract_result_v1"
SNAPSHOT_VERSION = "immutable_macro_snapshot_contract_v1"
_ADAPTER_VERSIONS = {
    "fred_alfred_readonly_adapter_v1",
    "ecb_sdmx_readonly_adapter_v1",
}


def build_immutable_macro_snapshot_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    manifest = sorted(validated["series_manifest"], key=lambda item: item["identity"])
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_id": validated["snapshot_id"],
        "snapshot_date": validated["snapshot_date"],
        "series_count": len(manifest),
        "series_manifest": manifest,
        "safe_flags": {
            "provider_calls_used": 0,
            "network_used": False,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "hermes_state_touched": False,
            "production_runtime_supported": False,
        },
        "provenance": validated["provenance"],
        "input_sha256": _canonical_sha256(validated),
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "snapshot_id", "snapshot_date", "series_adapter_results", "provenance"}, name="request")
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    snapshot_date = _required_iso_date(payload, "snapshot_date")
    series_results = _required_list(payload.get("series_adapter_results"), name="series_adapter_results")
    series_manifest: list[dict[str, object]] = []
    seen_identities: set[str] = set()
    for item in series_results:
        manifest_item = _validate_adapter_result(item, snapshot_date=snapshot_date)
        identity = str(manifest_item["identity"])
        if identity in seen_identities:
            raise ValueError("duplicate series identity is not allowed.")
        seen_identities.add(identity)
        series_manifest.append(manifest_item)
    return {
        "version": payload["version"],
        "snapshot_id": _required_text(payload, "snapshot_id"),
        "snapshot_date": snapshot_date,
        "series_manifest": series_manifest,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_adapter_result(value: Any, *, snapshot_date: str) -> dict[str, object]:
    payload = _required_mapping(value, name="series_adapter_result")
    provider = _required_text(payload, "provider")
    adapter_version = _required_text(payload, "adapter_version")
    if adapter_version not in _ADAPTER_VERSIONS:
        raise ValueError("adapter_version is not supported.")
    allowed = {
        "version",
        "adapter_version",
        "status",
        "provider",
        "series_id",
        "flow_ref",
        "series_key",
        "response_sha256",
        "macro_series_contract",
        "network_used",
        "provider_calls_used",
        "registry_write_performed",
        "broker_actions_used",
        "deployment_performed",
        "production_runtime_supported",
        "provenance",
        "input_sha256",
        "output_payload_sha256",
    }
    _reject_unknown_fields(payload, allowed=allowed, name="series_adapter_result")
    if payload.get("status") != "SUCCESS":
        raise ValueError("series_adapter_result.status must be SUCCESS.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError("series_adapter_result.production_runtime_supported must be false.")
    if payload.get("registry_write_performed") is not False:
        raise ValueError("series_adapter_result.registry_write_performed must be false.")
    if not isinstance(payload.get("provider_calls_used"), int) or int(payload["provider_calls_used"]) < 0:
        raise ValueError("series_adapter_result.provider_calls_used must be a non-negative integer.")

    macro_series_contract = _validate_macro_series_contract(payload.get("macro_series_contract"))
    latest_available_date = str(macro_series_contract["point_in_time_summary"]["latest_available_date"])
    if snapshot_date < latest_available_date:
        raise ValueError("snapshot_date must be on or after every series latest available date.")

    if provider == "ECB_SDMX":
        identity = f"{provider}:{_required_text(payload, 'flow_ref')}:{_required_text(payload, 'series_key')}"
        source_ref = _required_text(payload, "series_key")
    else:
        identity = f"{provider}:{_required_text(payload, 'series_id')}"
        source_ref = _required_text(payload, "series_id")

    return {
        "identity": identity,
        "provider": provider,
        "source_ref": source_ref,
        "adapter_version": adapter_version,
        "response_sha256": _required_sha256(payload, "response_sha256"),
        "adapter_output_sha256": _required_sha256(payload, "output_payload_sha256"),
        "macro_series_output_sha256": str(macro_series_contract["output_payload_sha256"]),
        "observation_count": int(macro_series_contract["observation_count"]),
        "first_observation_date": str(macro_series_contract["first_observation_date"]),
        "last_observation_date": str(macro_series_contract["last_observation_date"]),
        "point_in_time_summary": macro_series_contract["point_in_time_summary"],
    }


def _validate_macro_series_contract(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="macro_series_contract")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "contract_version",
            "provider",
            "series_id",
            "frequency",
            "units",
            "observations",
            "observation_count",
            "first_observation_date",
            "last_observation_date",
            "point_in_time_summary",
            "safe_flags",
            "provenance",
            "input_sha256",
            "output_payload_sha256",
        },
        name="macro_series_contract",
    )
    if payload.get("version") != "macro_series_contract_result_v1":
        raise ValueError("macro_series_contract.version must be macro_series_contract_result_v1.")
    if payload.get("contract_version") != "macro_series_contract_v1":
        raise ValueError("macro_series_contract.contract_version must be macro_series_contract_v1.")
    safe_flags = _required_mapping(payload.get("safe_flags"), name="macro_series_contract.safe_flags")
    if safe_flags.get("production_runtime_supported") is not False:
        raise ValueError("macro_series_contract.safe_flags.production_runtime_supported must be false.")
    summary = _required_mapping(payload.get("point_in_time_summary"), name="macro_series_contract.point_in_time_summary")
    _required_iso_date(summary, "latest_available_date")
    _required_sha256(payload, "output_payload_sha256")
    return payload


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_provenance(value: Any) -> dict[str, str | int | float | bool | None]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


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


def _required_iso_date(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    parts = value.split("-")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"{field} must be an ISO date.")
    return value


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
