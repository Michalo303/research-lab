from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import urllib.request
from typing import Any, Callable

from research_lab.execution.macro_series_contract_v1 import (
    build_macro_series_contract,
)


REQUEST_VERSION = "ecb_sdmx_readonly_adapter_request_v1"
RESULT_VERSION = "ecb_sdmx_readonly_adapter_result_v1"
ADAPTER_VERSION = "ecb_sdmx_readonly_adapter_v1"
_CLASSIFICATIONS = {"release_date_only", "vintage_date_only"}


def build_ecb_sdmx_readonly_adapter(
    request: dict[str, object],
    *,
    http_get: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, object]:
    validated = _validate_request(request)
    getter = http_get or _http_get_bytes
    request_url = _build_request_url(validated)
    response = getter(
        request_url,
        timeout_seconds=validated["timeout_seconds"],
        max_response_bytes=validated["max_response_bytes"],
        headers={"User-Agent": "research-lab/0.1 macro-readonly"},
    )
    payload, response_sha256 = _validate_response(response, validated=validated)
    observations = _normalize_observations(payload, point_in_time=validated["point_in_time"], max_observations=validated["max_observations"])
    macro_series_contract = build_macro_series_contract(
        {
            "version": "macro_series_contract_request_v1",
            "provider": validated["provider"],
            "series_id": validated["series_key"],
            "frequency": validated["frequency"],
            "units": validated["units"],
            "observations": observations,
            "provenance": {
                **validated["provenance"],
                "adapter_version": ADAPTER_VERSION,
                "flow_ref": validated["flow_ref"],
            },
        }
    )
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "status": "SUCCESS",
        "provider": validated["provider"],
        "flow_ref": validated["flow_ref"],
        "series_key": validated["series_key"],
        "response_sha256": response_sha256,
        "macro_series_contract": macro_series_contract,
        "network_used": True,
        "provider_calls_used": 1,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
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
        allowed={
            "version",
            "provider",
            "flow_ref",
            "series_key",
            "frequency",
            "units",
            "approved_host",
            "point_in_time",
            "timeout_seconds",
            "max_response_bytes",
            "max_observations",
            "live_access",
            "provenance",
        },
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if _required_text(payload, "provider") != "ECB_SDMX":
        raise ValueError("provider must be ECB_SDMX.")
    if payload.get("live_access") is not True:
        raise ValueError("live_access must be true.")
    approved_host = _required_text(payload, "approved_host")
    if approved_host != "data-api.ecb.europa.eu":
        raise ValueError("approved_host must be data-api.ecb.europa.eu.")
    return {
        "version": payload["version"],
        "provider": payload["provider"],
        "flow_ref": _required_text(payload, "flow_ref"),
        "series_key": _required_text(payload, "series_key"),
        "frequency": _required_text(payload, "frequency").lower(),
        "units": _required_text(payload, "units"),
        "approved_host": approved_host,
        "point_in_time": _validate_point_in_time(payload.get("point_in_time")),
        "timeout_seconds": _required_positive_int(payload, "timeout_seconds"),
        "max_response_bytes": _required_positive_int(payload, "max_response_bytes"),
        "max_observations": _required_positive_int(payload, "max_observations"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_point_in_time(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="point_in_time")
    _reject_unknown_fields(payload, allowed={"classification", "available_date"}, name="point_in_time")
    classification = _required_text(payload, "classification")
    if classification not in _CLASSIFICATIONS:
        raise ValueError("point_in_time.classification is not supported.")
    return {
        "classification": classification,
        "available_date": _required_iso_date(payload, "available_date"),
    }


def _build_request_url(validated: dict[str, Any]) -> str:
    query = urllib.parse.urlencode({"format": "jsondata"})
    series_key = urllib.parse.quote(validated["series_key"], safe=".")
    flow_ref = urllib.parse.quote(validated["flow_ref"], safe="")
    return f"https://{validated['approved_host']}/service/data/{flow_ref}/{series_key}?{query}"


def _validate_response(response: dict[str, Any], *, validated: dict[str, Any]) -> tuple[dict[str, Any], str]:
    payload = _required_mapping(response, name="response")
    final_url = _required_text(payload, "final_url")
    parsed = urllib.parse.urlparse(final_url)
    if parsed.scheme != "https":
        raise ValueError("provider response must use HTTPS.")
    if parsed.hostname != validated["approved_host"]:
        raise ValueError("provider response must remain on the approved host.")
    if not parsed.path.startswith("/service/data/"):
        raise ValueError("provider response path is not allowed.")
    if _required_positive_int(payload, "status_code") != 200:
        raise ValueError("unexpected HTTP status.")
    body_bytes = payload.get("body_bytes")
    if not isinstance(body_bytes, (bytes, bytearray)) or not body_bytes:
        raise ValueError("body_bytes must be non-empty bytes.")
    if len(body_bytes) > validated["max_response_bytes"]:
        raise ValueError("response exceeds max_response_bytes.")
    response_sha256 = hashlib.sha256(bytes(body_bytes)).hexdigest()
    parsed_payload = _required_mapping(json.loads(bytes(body_bytes).decode("utf-8")), name="provider payload")
    return parsed_payload, response_sha256


def _normalize_observations(payload: dict[str, Any], *, point_in_time: dict[str, str], max_observations: int) -> list[dict[str, Any]]:
    _reject_unknown_fields(payload, allowed={"dataSets", "structure"}, name="provider payload")
    data_sets = _required_list(payload.get("dataSets"), name="provider payload.dataSets")
    structure = _required_mapping(payload.get("structure"), name="provider payload.structure")
    if len(data_sets) != 1:
        raise ValueError("provider payload must contain exactly one dataset.")
    dataset = _required_mapping(data_sets[0], name="dataset")
    _reject_unknown_fields(dataset, allowed={"series"}, name="dataset")
    series = _required_mapping(dataset.get("series"), name="dataset.series")
    if len(series) != 1:
        raise ValueError("dataset.series must contain exactly one series.")
    series_entry = _required_mapping(next(iter(series.values())), name="series entry")
    _reject_unknown_fields(series_entry, allowed={"observations"}, name="series entry")
    observation_map = _required_mapping(series_entry.get("observations"), name="series entry.observations")

    dimensions = _required_mapping(structure.get("dimensions"), name="structure.dimensions")
    observation_dims = _required_list(dimensions.get("observation"), name="structure.dimensions.observation")
    time_dim = _required_mapping(observation_dims[0], name="observation dimension")
    values = _required_list(time_dim.get("values"), name="observation dimension.values")

    normalized: list[dict[str, Any]] = []
    for raw_index, raw_value in observation_map.items():
        if not isinstance(raw_index, str) or not raw_index.isdigit():
            raise ValueError("observation keys must be digit strings.")
        index = int(raw_index)
        if index >= len(values):
            raise ValueError("observation index exceeds dimension values.")
        dimension_value = _required_mapping(values[index], name="dimension value")
        observation_date = _required_iso_date(dimension_value, "id")
        if not isinstance(raw_value, list) or not raw_value:
            raise ValueError("observation value must be a non-empty array.")
        numeric = raw_value[0]
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)):
            raise ValueError("observation value must be numeric.")
        numeric_value = float(numeric)
        if not math.isfinite(numeric_value):
            raise ValueError("observation value must be finite.")
        normalized.append(
            {
                "observation_date": observation_date,
                "value": numeric_value,
                "point_in_time": dict(point_in_time),
            }
        )
    normalized.sort(key=lambda item: item["observation_date"])
    if len(normalized) > max_observations:
        raise ValueError("provider payload exceeds max_observations.")
    return normalized


def _http_get_bytes(
    url: str,
    *,
    timeout_seconds: int,
    max_response_bytes: int,
    headers: dict[str, str],
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            raise ValueError("response exceeds max_response_bytes.")
        return {
            "status_code": getattr(response, "status", None) or response.getcode(),
            "final_url": response.geturl(),
            "body_bytes": body,
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


def _required_list(value: Any, *, name: str) -> list[Any]:
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
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
