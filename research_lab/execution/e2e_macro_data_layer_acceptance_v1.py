from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from research_lab.execution.ecb_sdmx_readonly_adapter_v1 import (
    build_ecb_sdmx_readonly_adapter,
)
from research_lab.execution.fred_alfred_readonly_adapter_v1 import (
    build_fred_alfred_readonly_adapter,
)
from research_lab.execution.immutable_macro_snapshot_contract_v1 import (
    build_immutable_macro_snapshot_contract,
)


REQUEST_VERSION = "e2e_macro_data_layer_acceptance_request_v1"
RESULT_VERSION = "e2e_macro_data_layer_acceptance_result_v1"
ACCEPTANCE_VERSION = "e2e_macro_data_layer_acceptance_v1"


def run_e2e_macro_data_layer_acceptance(
    request: dict[str, object],
    *,
    fred_http_get: Callable[..., dict[str, Any]],
    ecb_http_get: Callable[..., dict[str, Any]],
) -> dict[str, object]:
    validated = _validate_request(request)
    fred_result = build_fred_alfred_readonly_adapter(validated["fred_request"], http_get=fred_http_get)
    ecb_result = build_ecb_sdmx_readonly_adapter(validated["ecb_request"], http_get=ecb_http_get)
    snapshot_result = build_immutable_macro_snapshot_contract(
        {
            "version": "immutable_macro_snapshot_contract_request_v1",
            "snapshot_id": validated["snapshot_id"],
            "snapshot_date": validated["snapshot_date"],
            "series_adapter_results": [fred_result, ecb_result],
            "provenance": validated["provenance"],
        }
    )
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "acceptance_version": ACCEPTANCE_VERSION,
        "status": "ACCEPTED",
        "adapter_statuses": {
            "fred": str(fred_result["status"]),
            "ecb": str(ecb_result["status"]),
        },
        "snapshot_status": "SUCCESS",
        "snapshot_series_count": int(snapshot_result["series_count"]),
        "snapshot_output_sha256": str(snapshot_result["output_payload_sha256"]),
        "live_network_used": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
        "input_sha256": _canonical_sha256(validated),
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "snapshot_id", "snapshot_date", "fred_request", "ecb_request", "provenance"},
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return {
        "version": payload["version"],
        "snapshot_id": _required_text(payload, "snapshot_id"),
        "snapshot_date": _required_iso_date(payload, "snapshot_date"),
        "fred_request": _required_mapping(payload.get("fred_request"), name="fred_request"),
        "ecb_request": _required_mapping(payload.get("ecb_request"), name="ecb_request"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


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
