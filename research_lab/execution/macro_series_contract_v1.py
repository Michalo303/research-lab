from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any


REQUEST_VERSION = "macro_series_contract_request_v1"
RESULT_VERSION = "macro_series_contract_result_v1"
CONTRACT_VERSION = "macro_series_contract_v1"
_CLASSIFICATIONS = {
    "exact_release_timestamp",
    "release_date_only",
    "vintage_date_only",
}


def build_macro_series_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    observations = validated["observations"]
    classification_counts: dict[str, int] = {}
    observed_dates: set[str] = set()
    has_revisions = False
    for item in observations:
        classification = item["point_in_time"]["classification"]
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        observation_date = item["observation_date"]
        if observation_date in observed_dates:
            has_revisions = True
        observed_dates.add(observation_date)

    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "provider": validated["provider"],
        "series_id": validated["series_id"],
        "frequency": validated["frequency"],
        "units": validated["units"],
        "observations": observations,
        "observation_count": len(observations),
        "first_observation_date": observations[0]["observation_date"],
        "last_observation_date": observations[-1]["observation_date"],
        "point_in_time_summary": {
            "classification_counts": classification_counts,
            "has_revisions": has_revisions,
            "latest_available_date": max(item["point_in_time"]["available_date"] for item in observations),
        },
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
    _reject_unknown_fields(
        payload,
        allowed={"version", "provider", "series_id", "frequency", "units", "observations", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    observations = _validate_observations(payload.get("observations"))
    return {
        "version": version,
        "provider": _required_text(payload, "provider").upper(),
        "series_id": _required_text(payload, "series_id").upper(),
        "frequency": _required_text(payload, "frequency").lower(),
        "units": _required_text(payload, "units"),
        "observations": observations,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_observations(value: Any) -> list[dict[str, Any]]:
    observations = _required_list(value, name="observations")
    normalized: list[dict[str, Any]] = []
    previous_identity: tuple[str, str, str] | None = None
    seen_identities: set[tuple[str, str, str]] = set()
    for raw in observations:
        payload = _required_mapping(raw, name="observation")
        _reject_unknown_fields(payload, allowed={"observation_date", "value", "point_in_time"}, name="observation")
        observation_date = _required_iso_date(payload, "observation_date")
        point_in_time = _validate_point_in_time(payload.get("point_in_time"))
        value_number = _required_finite_number(payload, "value")
        identity = (
            observation_date,
            point_in_time["available_date"],
            point_in_time["available_timestamp_utc"] or "",
        )
        if identity in seen_identities:
            raise ValueError("duplicate observation identity is not allowed.")
        if previous_identity is not None and identity <= previous_identity:
            raise ValueError("observations must be strictly ordered by observation_date and point-in-time identity.")
        seen_identities.add(identity)
        previous_identity = identity
        normalized.append(
            {
                "observation_date": observation_date,
                "value": value_number,
                "point_in_time": point_in_time,
            }
        )
    return normalized


def _validate_point_in_time(value: Any) -> dict[str, str | None]:
    payload = _required_mapping(value, name="point_in_time")
    _reject_unknown_fields(
        payload,
        allowed={"classification", "available_date", "available_timestamp_utc"},
        name="point_in_time",
    )
    classification = _required_text(payload, "classification")
    if classification not in _CLASSIFICATIONS:
        raise ValueError("point_in_time.classification is not supported.")
    available_date = _required_iso_date(payload, "available_date")
    raw_timestamp = payload.get("available_timestamp_utc")
    if classification == "exact_release_timestamp":
        if raw_timestamp is None:
            raise ValueError("point_in_time.available_timestamp_utc is required for exact_release_timestamp.")
        timestamp = _required_utc_timestamp(raw_timestamp, name="available_timestamp_utc")
        if timestamp[:10] != available_date:
            raise ValueError("point_in_time.available_date must match the UTC date of available_timestamp_utc.")
        return {
            "classification": classification,
            "available_date": available_date,
            "available_timestamp_utc": timestamp,
        }
    if raw_timestamp is not None:
        raise ValueError(
            "point_in_time classification with date-only semantics must not include available_timestamp_utc."
        )
    return {
        "classification": classification,
        "available_date": available_date,
        "available_timestamp_utc": None,
    }


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


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    raw = _required_text(payload, field)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date.") from exc
    return parsed.isoformat()


def _required_utc_timestamp(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    text = value.strip()
    if not text.endswith("Z"):
        raise ValueError(f"{name} must end with Z.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 UTC timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include UTC timezone information.")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


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
