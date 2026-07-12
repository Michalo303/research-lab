from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


REQUEST_VERSION = "macro_market_asof_alignment_contract_request_v1"
RESULT_VERSION = "macro_market_asof_alignment_contract_result_v1"
CONTRACT_VERSION = "macro_market_asof_alignment_contract_v1"
_UNSAFE_CLASSIFICATIONS = {"CURRENT_VALUE_ONLY", "NOT_POINT_IN_TIME_SAFE"}
_SAFE_CLASSIFICATIONS = {"RELEASE_AWARE", "VINTAGE_AWARE"} | _UNSAFE_CLASSIFICATIONS


def build_macro_market_asof_alignment_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    aligned_bars = [
        _align_bar(
            bar,
            validated=validated,
        )
        for bar in validated["market_bars"]
    ]
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "status": "SUCCESS",
        "aligned_bars": aligned_bars,
        "source_series_identities": [series["identity"] for series in validated["macro_series_results"]],
        "unsafe_series_warnings": sorted(validated["unsafe_series_warnings"]),
        "safety_flags": {
            "network_used": False,
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "production_runtime_supported": False,
        },
        "provenance": validated["provenance"],
        "input_sha256": _canonical_sha256(validated["hashable_request"]),
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _align_bar(bar: dict[str, Any], *, validated: dict[str, Any]) -> dict[str, Any]:
    decision_timestamp_utc = _decision_timestamp_for_bar(
        bar_timestamp=bar["timestamp"],
        market_timezone=validated["market_timezone"],
        decision_time_local=validated["decision_time_local"],
    )
    macro_values: dict[str, float | None] = {}
    availability_timestamps: dict[str, str | None] = {}
    age_staleness: dict[str, int | None] = {}
    missing_indicators: dict[str, bool] = {}
    classifications: dict[str, str] = {}

    for series in validated["macro_series_results"]:
        identity = series["identity"]
        visible = _latest_visible_observation(
            series["observations"],
            decision_timestamp_utc=decision_timestamp_utc,
            availability_convention=validated["macro_availability_convention"],
            market_timezone=validated["market_timezone"],
            minimum_release_lag_minutes=validated["minimum_release_lag_minutes"],
        )
        classifications[identity] = series["point_in_time_classification"]
        if visible is None:
            macro_values[identity] = None
            availability_timestamps[identity] = None
            age_staleness[identity] = None
            missing_indicators[identity] = True
            continue
        staleness_days = (decision_timestamp_utc.date() - date.fromisoformat(visible["observation_date"])).days
        is_stale = staleness_days > validated["maximum_staleness_days"]
        if is_stale and validated["missing_data_policy"] == "MARK_MISSING":
            macro_values[identity] = None
            availability_timestamps[identity] = visible["effective_available_timestamp_utc"]
            age_staleness[identity] = staleness_days
            missing_indicators[identity] = True
            continue
        if is_stale:
            raise ValueError(f"macro series {identity} exceeds maximum_staleness_days.")
        macro_values[identity] = visible["value"]
        availability_timestamps[identity] = visible["effective_available_timestamp_utc"]
        age_staleness[identity] = staleness_days
        missing_indicators[identity] = False

    return {
        "timestamp": bar["timestamp"],
        "decision_timestamp_utc": decision_timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "macro_values": macro_values,
        "availability_timestamps_utc": availability_timestamps,
        "age_staleness_days": age_staleness,
        "missing_indicators": missing_indicators,
        "point_in_time_classifications": classifications,
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "market_bars",
            "ohlcv_adapter_result",
            "macro_series_results",
            "market_timezone",
            "decision_timestamp_convention",
            "decision_time_local",
            "macro_availability_convention",
            "minimum_release_lag_minutes",
            "maximum_staleness_days",
            "missing_data_policy",
            "unsafe_series_policy",
            "provenance",
        },
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if _required_text(payload, "decision_timestamp_convention") != "LOCAL_TIME_ON_BAR_DATE":
        raise ValueError("decision_timestamp_convention must be LOCAL_TIME_ON_BAR_DATE.")
    bars = _validate_market_bars(payload)
    market_timezone = _validated_timezone(_required_text(payload, "market_timezone"))
    decision_time_local = _required_local_time(payload, "decision_time_local")
    macro_availability_convention = _required_text(payload, "macro_availability_convention")
    if macro_availability_convention not in {"AT_START_OF_DAY", "AT_END_OF_DAY"}:
        raise ValueError("macro_availability_convention must be AT_START_OF_DAY or AT_END_OF_DAY.")
    missing_data_policy = _required_text(payload, "missing_data_policy")
    if missing_data_policy not in {"MARK_MISSING", "ERROR"}:
        raise ValueError("missing_data_policy must be MARK_MISSING or ERROR.")
    unsafe_series_policy = _required_text(payload, "unsafe_series_policy")
    if unsafe_series_policy not in {"REJECT", "ALLOW_RESEARCH_ONLY"}:
        raise ValueError("unsafe_series_policy must be REJECT or ALLOW_RESEARCH_ONLY.")

    unsafe_series_warnings: set[str] = set()
    macro_series_results = _validate_macro_series_results(
        payload.get("macro_series_results"),
        unsafe_series_policy=unsafe_series_policy,
        unsafe_series_warnings=unsafe_series_warnings,
    )
    hashable_request = {
        "version": REQUEST_VERSION,
        "market_bars": bars,
        "macro_series_results": macro_series_results,
        "market_timezone": market_timezone,
        "decision_timestamp_convention": "LOCAL_TIME_ON_BAR_DATE",
        "decision_time_local": decision_time_local.strftime("%H:%M:%S"),
        "macro_availability_convention": macro_availability_convention,
        "minimum_release_lag_minutes": _required_non_negative_int(payload, "minimum_release_lag_minutes"),
        "maximum_staleness_days": _required_non_negative_int(payload, "maximum_staleness_days"),
        "missing_data_policy": missing_data_policy,
        "unsafe_series_policy": unsafe_series_policy,
        "provenance": _validate_provenance(payload.get("provenance")),
    }
    return {
        **hashable_request,
        "decision_time_local": decision_time_local,
        "unsafe_series_warnings": unsafe_series_warnings,
        "hashable_request": hashable_request,
    }


def _validate_market_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "market_bars" in payload:
        return _validate_bar_rows(payload.get("market_bars"))
    adapter = _required_mapping(payload.get("ohlcv_adapter_result"), name="ohlcv_adapter_result")
    if adapter.get("status") != "SUCCESS":
        raise ValueError("ohlcv_adapter_result.status must be SUCCESS.")
    downstream = _required_mapping(adapter.get("downstream_adapter_result"), name="downstream_adapter_result")
    bars = _required_list(downstream.get("synthetic_bars"), name="downstream_adapter_result.synthetic_bars")
    return _validate_bar_rows(bars)


def _validate_bar_rows(value: Any) -> list[dict[str, Any]]:
    rows = _required_list(value, name="market_bars")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    seen_timestamps: set[str] = set()
    for raw in rows:
        payload = _required_mapping(raw, name="market_bar")
        _reject_unknown_fields(payload, allowed={"timestamp", "open", "high", "low", "close", "volume"}, name="market_bar")
        timestamp = _required_utc_timestamp(payload.get("timestamp"), name="market_bar.timestamp")
        if timestamp in seen_timestamps:
            raise ValueError("market_bars must be strictly ordered without duplicate timestamps.")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("market_bars must be strictly ordered without duplicate timestamps.")
        row = {
            "timestamp": timestamp,
            "open": _required_finite_number(payload, "open"),
            "high": _required_finite_number(payload, "high"),
            "low": _required_finite_number(payload, "low"),
            "close": _required_finite_number(payload, "close"),
            "volume": _required_finite_number(payload, "volume"),
        }
        seen_timestamps.add(timestamp)
        previous_timestamp = timestamp
        normalized.append(row)
    return normalized


def _validate_macro_series_results(
    value: Any,
    *,
    unsafe_series_policy: str,
    unsafe_series_warnings: set[str],
) -> list[dict[str, Any]]:
    series_list = _required_list(value, name="macro_series_results")
    normalized: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for raw in series_list:
        payload = _required_mapping(raw, name="macro_series_result")
        classification = _required_text(payload, "point_in_time_classification")
        if classification not in _SAFE_CLASSIFICATIONS:
            raise ValueError("point_in_time_classification is not supported.")
        contract = _validate_macro_series_contract(payload.get("macro_series_contract"))
        provider = _required_text(payload, "provider")
        identity = f"{provider}:{_required_text(payload, 'series_id')}"
        if identity in seen_identities:
            raise ValueError("macro_series_results contains duplicate series identity.")
        seen_identities.add(identity)
        if classification in _UNSAFE_CLASSIFICATIONS:
            if unsafe_series_policy == "REJECT":
                raise ValueError(f"unsafe historical macro series is not allowed: {identity}")
            unsafe_series_warnings.add(identity)
        observations = _validate_macro_observations(contract.get("observations"))
        normalized.append(
            {
                "identity": identity,
                "provider": provider,
                "series_id": _required_text(payload, "series_id"),
                "point_in_time_classification": classification,
                "observations": observations,
                "macro_series_output_sha256": _required_sha256(contract, "output_payload_sha256"),
            }
        )
    return normalized


def _validate_macro_series_contract(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="macro_series_contract")
    if payload.get("version") != "macro_series_contract_result_v1":
        raise ValueError("macro_series_contract.version must be macro_series_contract_result_v1.")
    if payload.get("contract_version") != "macro_series_contract_v1":
        raise ValueError("macro_series_contract.contract_version must be macro_series_contract_v1.")
    return payload


def _validate_macro_observations(value: Any) -> list[dict[str, Any]]:
    rows = _required_list(value, name="macro observations")
    normalized: list[dict[str, Any]] = []
    previous_identity: tuple[str, str, str] | None = None
    seen_identities: set[tuple[str, str, str]] = set()
    for raw in rows:
        payload = _required_mapping(raw, name="macro observation")
        point_in_time = _required_mapping(payload.get("point_in_time"), name="macro observation.point_in_time")
        available_date = _required_iso_date(point_in_time, "available_date")
        available_timestamp = point_in_time.get("available_timestamp_utc")
        if available_timestamp is not None:
            available_timestamp = _required_utc_timestamp(available_timestamp, name="available_timestamp_utc")
        identity = (
            _required_iso_date(payload, "observation_date"),
            available_date,
            available_timestamp or "",
        )
        if identity in seen_identities:
            raise ValueError("duplicate macro observation is not allowed.")
        if previous_identity is not None and identity <= previous_identity:
            raise ValueError("macro observations must be strictly ordered.")
        seen_identities.add(identity)
        previous_identity = identity
        normalized.append(
            {
                "observation_date": identity[0],
                "value": _required_finite_number(payload, "value"),
                "available_date": available_date,
                "available_timestamp_utc": available_timestamp,
            }
        )
    return normalized


def _latest_visible_observation(
    observations: list[dict[str, Any]],
    *,
    decision_timestamp_utc: datetime,
    availability_convention: str,
    market_timezone: str,
    minimum_release_lag_minutes: int,
) -> dict[str, Any] | None:
    visible: dict[str, Any] | None = None
    for observation in observations:
        effective_timestamp = _availability_timestamp(
            observation,
            availability_convention=availability_convention,
            market_timezone=market_timezone,
            minimum_release_lag_minutes=minimum_release_lag_minutes,
        )
        if effective_timestamp <= decision_timestamp_utc:
            visible = {
                **observation,
                "effective_available_timestamp_utc": effective_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        else:
            break
    return visible


def _availability_timestamp(
    observation: dict[str, Any],
    *,
    availability_convention: str,
    market_timezone: str,
    minimum_release_lag_minutes: int,
) -> datetime:
    if observation["available_timestamp_utc"] is not None:
        base = datetime.fromisoformat(observation["available_timestamp_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        local_date = date.fromisoformat(observation["available_date"])
        if availability_convention == "AT_START_OF_DAY":
            local_dt = datetime.combine(local_date, time(0, 0, 0), tzinfo=ZoneInfo(market_timezone))
        else:
            local_dt = datetime.combine(local_date, time(23, 59, 59), tzinfo=ZoneInfo(market_timezone))
        base = local_dt.astimezone(timezone.utc)
    return base + timedelta(minutes=minimum_release_lag_minutes)


def _decision_timestamp_for_bar(
    *,
    bar_timestamp: str,
    market_timezone: str,
    decision_time_local: time,
) -> datetime:
    bar_dt = datetime.fromisoformat(bar_timestamp.replace("Z", "+00:00")).astimezone(ZoneInfo(market_timezone))
    local_decision = datetime.combine(bar_dt.date(), decision_time_local, tzinfo=ZoneInfo(market_timezone))
    return local_decision.astimezone(timezone.utc)


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


def _required_local_time(payload: dict[str, Any], field: str) -> time:
    raw = _required_text(payload, field)
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO local time.") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{field} must not include timezone information.")
    return parsed


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value


def _validated_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except Exception as exc:
        raise ValueError("market_timezone must be a valid IANA timezone.") from exc
    return name


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
