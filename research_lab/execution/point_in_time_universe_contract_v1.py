from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


REQUEST_VERSION = "point_in_time_universe_contract_request_v1"
RESULT_VERSION = "point_in_time_universe_contract_result_v1"
CONTRACT_VERSION = "point_in_time_universe_contract_v1"
_POINT_IN_TIME_STATUSES = {
    "POINT_IN_TIME_VERIFIED",
    "EXPLICIT_STATIC_RESEARCH_UNIVERSE",
    "CURRENT_MEMBERSHIP_ONLY",
    "NOT_POINT_IN_TIME_SAFE",
}


def build_point_in_time_universe_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    included_instruments: list[dict[str, Any]] = []
    excluded_instruments: list[dict[str, str]] = []
    membership_intervals: list[dict[str, Any]] = []
    point_in_time_classifications: dict[str, list[str]] = {}
    survivorship_warnings: list[str] = []
    unsafe_used = False

    for instrument in validated["instruments"]:
        status = instrument["point_in_time_membership_status"]
        point_in_time_classifications.setdefault(status, []).append(instrument["instrument_id"])

        if status == "EXPLICIT_STATIC_RESEARCH_UNIVERSE":
            survivorship_warnings.append(
                "Universe membership is an explicit static research universe and is not survivorship-bias-free."
            )
        elif status == "CURRENT_MEMBERSHIP_ONLY":
            _require_unsafe_policy(
                allowed=validated["membership_policy"]["allow_unsafe_current_membership"],
                status=status,
            )
            unsafe_used = True
            survivorship_warnings.append(
                "Unsafe research-only membership policy includes current-membership-only instruments; survivorship bias may be present."
            )
        elif status == "NOT_POINT_IN_TIME_SAFE":
            _require_unsafe_policy(
                allowed=validated["membership_policy"]["allow_not_point_in_time_safe"],
                status=status,
            )
            unsafe_used = True
            survivorship_warnings.append(
                "Unsafe research-only membership policy includes instruments that are not point-in-time safe."
            )

        inclusion = _determine_inclusion(instrument, as_of_timestamp=validated["as_of_timestamp"])
        if inclusion is None:
            excluded_instruments.append(
                {
                    "instrument_id": instrument["instrument_id"],
                    "reason": "instrument is inactive at as_of_timestamp",
                }
            )
            continue
        included_instruments.append(instrument)
        membership_intervals.append(
            {
                "instrument_id": instrument["instrument_id"],
                "active_from": instrument["active_from"],
                "active_to": instrument["active_to"],
                "membership_from": instrument["membership_from"],
                "membership_to": instrument["membership_to"],
                "point_in_time_membership_status": status,
            }
        )

    included_instruments.sort(key=lambda item: item["instrument_id"])
    excluded_instruments.sort(key=lambda item: item["instrument_id"])
    membership_intervals.sort(key=lambda item: item["instrument_id"])
    normalized_classifications = {
        key: sorted(value) for key, value in sorted(point_in_time_classifications.items(), key=lambda item: item[0])
    }
    deduped_warnings = list(dict.fromkeys(survivorship_warnings))

    validated_universe = {
        "version": validated["version"],
        "universe_id": validated["universe_id"],
        "universe_version": validated["universe_version"],
        "as_of_timestamp": validated["as_of_timestamp"],
        "membership_policy": validated["membership_policy"],
        "base_currency": validated["base_currency"],
        "instruments": included_instruments,
        "provenance": validated["provenance"],
    }
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "validated_universe": validated_universe,
        "included_instruments": included_instruments,
        "excluded_instruments": excluded_instruments,
        "membership_intervals": membership_intervals,
        "survivorship_warnings": deduped_warnings,
        "point_in_time_classifications": normalized_classifications,
        "input_sha256": _canonical_sha256(validated),
        "safety_flags": {
            "network_used": False,
            "filesystem_writes_performed": False,
            "unsafe_membership_policy_used": unsafe_used,
            "production_runtime_supported": False,
        },
        "production_runtime_supported": False,
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
            "universe_id",
            "universe_version",
            "as_of_timestamp",
            "membership_policy",
            "base_currency",
            "instruments",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return {
        "version": version,
        "universe_id": _required_text(payload, "universe_id"),
        "universe_version": _required_text(payload, "universe_version"),
        "as_of_timestamp": _required_utc_timestamp(payload.get("as_of_timestamp"), name="as_of_timestamp"),
        "membership_policy": _validate_membership_policy(payload.get("membership_policy")),
        "base_currency": _required_upper_text(payload, "base_currency", message="base_currency must be uppercase ISO-like text."),
        "instruments": _validate_instruments(payload.get("instruments")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_membership_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="membership_policy")
    _reject_unknown_fields(
        payload,
        allowed={"allow_unsafe_current_membership", "allow_not_point_in_time_safe", "unsafe_policy_label"},
        name="membership_policy",
    )
    return {
        "allow_unsafe_current_membership": _required_bool(payload, "allow_unsafe_current_membership"),
        "allow_not_point_in_time_safe": _required_bool(payload, "allow_not_point_in_time_safe"),
        "unsafe_policy_label": _required_text(payload, "unsafe_policy_label"),
    }


def _validate_instruments(value: Any) -> list[dict[str, Any]]:
    items = _required_list(value, name="instruments")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_provider_identities: set[tuple[str, str]] = set()

    for raw in items:
        instrument = _validate_instrument(raw)
        instrument_id = instrument["instrument_id"]
        provider_identity = (instrument["provider"], instrument["provider_symbol"])
        if instrument_id in seen_ids:
            raise ValueError("duplicate instrument_id is not allowed.")
        if provider_identity in seen_provider_identities:
            raise ValueError("duplicate provider identity is not allowed.")
        seen_ids.add(instrument_id)
        seen_provider_identities.add(provider_identity)
        normalized.append(instrument)

    return normalized


def _validate_instrument(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="instrument")
    _reject_unknown_fields(
        payload,
        allowed={
            "instrument_id",
            "provider",
            "provider_symbol",
            "display_symbol",
            "instrument_type",
            "currency",
            "market_venue_group",
            "calendar_id",
            "active_from",
            "active_to",
            "membership_from",
            "membership_to",
            "point_in_time_membership_status",
            "lot_size",
            "price_precision",
            "corporate_action_policy_id",
            "source_sha256",
            "provenance",
        },
        name="instrument",
    )
    active_from = _required_utc_timestamp(payload.get("active_from"), name="active_from")
    active_to = _optional_utc_timestamp(payload.get("active_to"), name="active_to")
    membership_from = _required_utc_timestamp(payload.get("membership_from"), name="membership_from")
    membership_to = _optional_utc_timestamp(payload.get("membership_to"), name="membership_to")
    if active_to is not None and active_to < active_from:
        raise ValueError("active_to must not be earlier than active_from.")
    if membership_to is not None and membership_to < membership_from:
        raise ValueError("membership_to must not be earlier than membership_from.")
    status = _required_text(payload, "point_in_time_membership_status")
    if status not in _POINT_IN_TIME_STATUSES:
        raise ValueError("point_in_time_membership_status is not supported.")
    return {
        "instrument_id": _required_text(payload, "instrument_id"),
        "provider": _required_text(payload, "provider"),
        "provider_symbol": _required_text(payload, "provider_symbol"),
        "display_symbol": _required_text(payload, "display_symbol"),
        "instrument_type": _required_text(payload, "instrument_type"),
        "currency": _required_upper_text(payload, "currency", message="currency must be uppercase ISO-like text."),
        "market_venue_group": _required_text(payload, "market_venue_group"),
        "calendar_id": _required_text(payload, "calendar_id"),
        "active_from": active_from,
        "active_to": active_to,
        "membership_from": membership_from,
        "membership_to": membership_to,
        "point_in_time_membership_status": status,
        "lot_size": _required_positive_int(payload, "lot_size"),
        "price_precision": _required_non_negative_int(payload, "price_precision"),
        "corporate_action_policy_id": _required_text(payload, "corporate_action_policy_id"),
        "source_sha256": _required_sha256(payload, "source_sha256"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _determine_inclusion(instrument: dict[str, Any], *, as_of_timestamp: str) -> dict[str, Any] | None:
    if as_of_timestamp < instrument["membership_from"]:
        raise ValueError("membership cannot start after as_of_timestamp.")
    if as_of_timestamp < instrument["active_from"]:
        raise ValueError("instrument cannot become active after as_of_timestamp.")

    active_to = instrument["active_to"]
    membership_to = instrument["membership_to"]
    if membership_to is not None and membership_to < as_of_timestamp:
        if active_to is None:
            raise ValueError("membership cannot end before as_of_timestamp.")
        return None
    if active_to is not None and active_to < as_of_timestamp:
        if membership_to is None:
            raise ValueError("instrument cannot be active after active_to.")
        return None
    return instrument


def _require_unsafe_policy(*, allowed: bool, status: str) -> None:
    if not allowed:
        raise ValueError(f"{status} is not point-in-time safe without an explicit unsafe research-only policy.")


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


def _required_upper_text(payload: dict[str, Any], field: str, *, message: str) -> str:
    value = _required_text(payload, field)
    if value != value.upper():
        raise ValueError(message)
    return value


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean.")
    return value


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


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value


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


def _optional_utc_timestamp(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    return _required_utc_timestamp(value, name=name)


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
