from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REQUEST_VERSION = "exchange_session_calendar_contract_request_v1"
RESULT_VERSION = "exchange_session_calendar_contract_result_v1"
CONTRACT_VERSION = "exchange_session_calendar_contract_v1"
_SESSION_TYPES = {"REGULAR", "PARTIAL", "CLOSED"}
_HOLIDAY_POLICIES = {"EXPLICIT_CLOSED_SESSIONS", "EXPLICIT_SESSION_LIST_ONLY"}
_PARTIAL_POLICIES = {"ALLOW_EXPLICIT_PARTIAL", "REJECT_PARTIAL", "REQUIRE_EXPLICIT_PARTIAL"}
_TIMESTAMP_SEMANTICS = {"SESSION_DATE", "BAR_OPEN_TIME", "BAR_CLOSE_TIME"}
_MISSING_POLICIES = {"FAIL_ON_ANY_MISSING", "ALLOW_EXPLICIT_THRESHOLD", "REPORT_ONLY"}
_UNEXPECTED_POLICIES = {"FAIL_ON_ANY_UNEXPECTED", "REPORT_ONLY"}


def build_exchange_session_calendar_contract(request: dict[str, object]) -> dict[str, object]:
    """Validate an explicit, deterministic exchange-session calendar without side effects."""
    validated = _validate_request(copy.deepcopy(request))
    validation_summary, validation_status, blocking_findings, review_findings = _validate_bars(validated)
    sessions = validated["sessions"]
    boundaries = [
        {
            "session_date": item["session_date"],
            "session_type": item["session_type"],
            "open_timestamp_utc": item.get("open_timestamp_utc"),
            "close_timestamp_utc": item.get("close_timestamp_utc"),
        }
        for item in sessions
    ]
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "calendar_id": validated["calendar_id"],
        "calendar_version": validated["calendar_version"],
        "timezone": validated["timezone"],
        "normalized_sessions": sessions,
        "UTC_session_boundaries": boundaries,
        "holiday_policy": validated["holiday_policy"],
        "partial_session_policy": validated["partial_session_policy"],
        "coverage_start": sessions[0]["session_date"],
        "coverage_end": sessions[-1]["session_date"],
        "regular_session_count": sum(item["session_type"] == "REGULAR" for item in sessions),
        "partial_session_count": sum(item["session_type"] == "PARTIAL" for item in sessions),
        "closed_session_count": sum(item["session_type"] == "CLOSED" for item in sessions),
        "validation_status": validation_status,
        "validation_summary": validation_summary,
        "blocking_findings": blocking_findings,
        "review_findings": review_findings,
        "warnings": [],
        "input_sha256": _canonical_sha256(validated),
        "provider_calls_used": 0,
        "network_used": False,
        "filesystem_writes_performed": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: Any) -> dict[str, Any]:
    payload = _mapping(request, "request")
    _reject_unknown(payload, {"version", "calendar_id", "calendar_version", "timezone", "sessions", "holiday_policy", "partial_session_policy", "validation_request", "provenance"}, "request")
    if _text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    timezone_name = _text(payload, "timezone")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be a valid IANA timezone.") from exc
    holiday_policy = _text(payload, "holiday_policy")
    partial_policy = _text(payload, "partial_session_policy")
    if holiday_policy not in _HOLIDAY_POLICIES:
        raise ValueError("holiday_policy is not supported.")
    if partial_policy not in _PARTIAL_POLICIES:
        raise ValueError("partial_session_policy is not supported.")
    sessions = _validate_sessions(payload.get("sessions"), zone, partial_policy)
    return {
        "version": REQUEST_VERSION,
        "calendar_id": _text(payload, "calendar_id"),
        "calendar_version": _text(payload, "calendar_version"),
        "timezone": timezone_name,
        "sessions": sessions,
        "holiday_policy": holiday_policy,
        "partial_session_policy": partial_policy,
        "validation_request": _validate_validation_request(payload.get("validation_request"), timezone_name) if "validation_request" in payload else None,
        "provenance": _provenance(payload.get("provenance")),
    }


def _validate_sessions(value: Any, zone: ZoneInfo, partial_policy: str) -> list[dict[str, Any]]:
    raw = _list(value, "sessions")
    if not raw:
        raise ValueError("sessions must not be empty.")
    sessions: list[dict[str, Any]] = []
    previous_date: date | None = None
    previous_close: datetime | None = None
    seen_dates: set[str] = set()
    seen_semantics: set[tuple[Any, ...]] = set()
    for item in raw:
        payload = _mapping(item, "session")
        _reject_unknown(payload, {"session_date", "session_type", "open_timestamp", "close_timestamp", "source_identity", "source_sha256", "provenance", "closure_provenance"}, "session")
        session_date = _date_text(payload.get("session_date"), "session_date")
        parsed_date = date.fromisoformat(session_date)
        if session_date in seen_dates:
            raise ValueError("duplicate session_date is not allowed.")
        if previous_date is not None and parsed_date < previous_date:
            raise ValueError("sessions must be in chronological order.")
        seen_dates.add(session_date)
        session_type = _text(payload, "session_type")
        if session_type not in _SESSION_TYPES:
            raise ValueError("session_type is not supported.")
        if session_type == "PARTIAL" and partial_policy == "REJECT_PARTIAL":
            raise ValueError("partial_session_policy rejects PARTIAL sessions.")
        source_identity = _text(payload, "source_identity")
        source_sha256 = _sha(payload.get("source_sha256"), "source_sha256")
        provenance = _provenance(payload.get("provenance"))
        if session_type == "CLOSED":
            if payload.get("open_timestamp") is not None or payload.get("close_timestamp") is not None:
                raise ValueError("CLOSED sessions must not carry tradable boundaries.")
            closure = _provenance(payload.get("closure_provenance"), name="closure_provenance")
            normalized = {
                "session_date": session_date, "session_type": session_type, "source_identity": source_identity,
                "source_sha256": source_sha256, "provenance": provenance, "closure_provenance": closure,
            }
        else:
            if "closure_provenance" in payload:
                raise ValueError("closure_provenance is only valid for CLOSED sessions.")
            open_utc, open_local = _local_timestamp(payload.get("open_timestamp"), zone, "open_timestamp")
            close_utc, close_local = _local_timestamp(payload.get("close_timestamp"), zone, "close_timestamp")
            if open_local.date() != parsed_date or close_local.date() != parsed_date:
                raise ValueError("session boundaries must match session_date.")
            if open_utc >= close_utc:
                raise ValueError("open_timestamp must be before close_timestamp.")
            if previous_close is not None and open_utc < previous_close:
                raise ValueError("tradable sessions must not overlap.")
            previous_close = close_utc
            normalized = {
                "session_date": session_date, "session_type": session_type,
                "open_timestamp": open_local.isoformat(), "close_timestamp": close_local.isoformat(),
                "open_timestamp_utc": _utc_text(open_utc), "close_timestamp_utc": _utc_text(close_utc),
                "source_identity": source_identity, "source_sha256": source_sha256, "provenance": provenance,
            }
        semantic = (session_date, session_type, normalized.get("open_timestamp"), normalized.get("close_timestamp"))
        if semantic in seen_semantics:
            raise ValueError("duplicate semantic session is not allowed.")
        seen_semantics.add(semantic)
        sessions.append(normalized)
        previous_date = parsed_date
    return sessions


def _local_timestamp(value: Any, zone: ZoneInfo, name: str) -> tuple[datetime, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is not None:
        local = parsed.astimezone(zone)
        if local.replace(tzinfo=None) != parsed.replace(tzinfo=None):
            raise ValueError(f"{name} timezone offset is inconsistent with calendar timezone.")
        if not _is_unambiguous_local(local.replace(tzinfo=None), zone):
            raise ValueError(f"{name} is ambiguous or nonexistent without an explicit deterministic fold policy.")
        return local.astimezone(timezone.utc), local
    if not _is_unambiguous_local(parsed, zone):
        raise ValueError(f"{name} is ambiguous or nonexistent without an explicit deterministic fold policy.")
    local = parsed.replace(tzinfo=zone, fold=0)
    return local.astimezone(timezone.utc), local


def _is_unambiguous_local(value: datetime, zone: ZoneInfo) -> bool:
    candidates = [
        candidate
        for fold in (0, 1)
        if (candidate := value.replace(tzinfo=zone, fold=fold)).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == value
    ]
    return len(candidates) == 2 and len({candidate.utcoffset() for candidate in candidates}) == 1


def _validate_validation_request(value: Any, timezone_name: str) -> dict[str, Any]:
    payload = _mapping(value, "validation_request")
    _reject_unknown(payload, {"instrument_id", "calendar_id", "bars", "bar_interval", "bar_timestamp_semantics", "expected_bars_source_sha256", "expected_normalized_bars_sha256", "coverage_start", "coverage_end", "missing_session_policy", "unexpected_session_policy", "maximum_missing_count", "maximum_missing_ratio", "boundary_tolerance_seconds", "provenance"}, "validation_request")
    semantics = _text(payload, "bar_timestamp_semantics")
    if semantics not in _TIMESTAMP_SEMANTICS:
        raise ValueError("bar_timestamp_semantics is not supported.")
    missing_policy = _text(payload, "missing_session_policy")
    unexpected_policy = _text(payload, "unexpected_session_policy")
    if missing_policy not in _MISSING_POLICIES:
        raise ValueError("missing_session_policy is not supported.")
    if unexpected_policy not in _UNEXPECTED_POLICIES:
        raise ValueError("unexpected_session_policy is not supported.")
    maximum_count = payload.get("maximum_missing_count")
    maximum_ratio = payload.get("maximum_missing_ratio")
    if missing_policy == "ALLOW_EXPLICIT_THRESHOLD":
        if (maximum_count is None) == (maximum_ratio is None):
            raise ValueError("threshold policy requires exactly one explicit threshold.")
        if maximum_count is not None and (not isinstance(maximum_count, int) or isinstance(maximum_count, bool) or maximum_count < 0):
            raise ValueError("maximum_missing_count must be a non-negative integer.")
        if maximum_ratio is not None and (not _finite(maximum_ratio) or not 0.0 <= float(maximum_ratio) <= 1.0):
            raise ValueError("maximum_missing_ratio must be finite and between zero and one.")
    elif maximum_count is not None or maximum_ratio is not None:
        raise ValueError("missing thresholds are only allowed for ALLOW_EXPLICIT_THRESHOLD.")
    tolerance = payload.get("boundary_tolerance_seconds", 0)
    if not _finite(tolerance) or float(tolerance) < 0:
        raise ValueError("boundary_tolerance_seconds must be finite and non-negative.")
    return {
        "instrument_id": _text(payload, "instrument_id"), "calendar_id": _text(payload, "calendar_id"),
        "bars": _list(payload.get("bars"), "bars"), "bar_interval": _text(payload, "bar_interval"),
        "bar_timestamp_semantics": semantics, "expected_bars_source_sha256": _sha(payload.get("expected_bars_source_sha256"), "expected_bars_source_sha256"),
        "expected_normalized_bars_sha256": _sha(payload.get("expected_normalized_bars_sha256"), "expected_normalized_bars_sha256"),
        "coverage_start": _date_text(payload.get("coverage_start"), "coverage_start"), "coverage_end": _date_text(payload.get("coverage_end"), "coverage_end"),
        "missing_session_policy": missing_policy, "unexpected_session_policy": unexpected_policy,
        "maximum_missing_count": maximum_count, "maximum_missing_ratio": maximum_ratio,
        "boundary_tolerance_seconds": float(tolerance), "provenance": _provenance(payload.get("provenance")),
        "timezone": timezone_name,
    }


def _validate_bars(validated: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str], list[str]]:
    request = validated["validation_request"]
    if request is None:
        return None, "NOT_EVALUATED", [], []
    if request["calendar_id"] != validated["calendar_id"]:
        raise ValueError("validation_request calendar_id does not match calendar_id.")
    if date.fromisoformat(request["coverage_start"]) > date.fromisoformat(request["coverage_end"]):
        raise ValueError("coverage_start must not be after coverage_end.")
    if _canonical_sha256(request["bars"]) != request["expected_bars_source_sha256"]:
        raise ValueError("expected bars source hash does not bind supplied bars.")
    bars = [_validate_bar(item, request) for item in request["bars"]]
    normalized_hash_payload = [{key: item["raw"][key] for key in sorted(item["raw"])} for item in bars]
    if _canonical_sha256(normalized_hash_payload) != request["expected_normalized_bars_sha256"]:
        raise ValueError("expected normalized bars hash does not bind supplied bars.")
    sessions = {item["session_date"]: item for item in validated["sessions"]}
    calendar_start, calendar_end = validated["sessions"][0]["session_date"], validated["sessions"][-1]["session_date"]
    expected = [key for key, item in sessions.items() if item["session_type"] != "CLOSED" and request["coverage_start"] <= key <= request["coverage_end"]]
    observed: dict[str, list[dict[str, Any]]] = {}
    closed, outside, undeclared, before, after, out_of_session = [], [], [], [], [], []
    for bar in bars:
        session_date = bar["session_date"]
        session = sessions.get(session_date)
        if session_date < calendar_start or session_date > calendar_end:
            outside.append(bar["bar_id"])
            continue
        if session is None:
            undeclared.append(bar["bar_id"])
            continue
        if session["session_type"] == "CLOSED":
            closed.append(bar["bar_id"])
            continue
        observed.setdefault(session_date, []).append(bar)
        if bar["timestamp_utc"] is not None:
            open_utc = _parse_utc(session["open_timestamp_utc"])
            close_utc = _parse_utc(session["close_timestamp_utc"])
            stamp = bar["timestamp_utc"]
            tolerance = request["boundary_tolerance_seconds"]
            if stamp < open_utc:
                before.append(bar["bar_id"])
            elif stamp > close_utc:
                after.append(bar["bar_id"])
            elif request["bar_timestamp_semantics"] == "BAR_OPEN_TIME" and abs((stamp - open_utc).total_seconds()) > tolerance:
                out_of_session.append(bar["bar_id"])
            elif request["bar_timestamp_semantics"] == "BAR_CLOSE_TIME" and abs((stamp - close_utc).total_seconds()) > tolerance:
                out_of_session.append(bar["bar_id"])
    observed_dates = sorted(observed)
    missing = sorted(set(expected) - set(observed_dates))
    unexpected_dates = sorted({bar["session_date"] for bar in bars if bar["bar_id"] in set(closed + outside + undeclared)})
    duplicates = sorted(key for key, items in observed.items() if len(items) > 1)
    matched = sorted(set(expected) & set(observed_dates))
    unexpected_count = len(unexpected_dates)
    ratio = len(matched) / len(expected) if expected else 1.0
    summary = {
        "expected_sessions": expected, "observed_sessions": observed_dates, "missing_sessions": missing,
        "unexpected_sessions": unexpected_dates, "duplicate_observed_sessions": duplicates,
        "out_of_session_bars": sorted(out_of_session), "bars_before_open": sorted(before), "bars_after_close": sorted(after),
        "bars_on_closed_sessions": sorted(closed), "bars_outside_calendar_coverage": sorted(outside),
        "bars_on_undeclared_sessions": sorted(undeclared), "coverage_start": request["coverage_start"], "coverage_end": request["coverage_end"],
        "expected_session_count": len(expected), "observed_session_count": len(observed_dates), "matched_session_count": len(matched),
        "missing_session_count": len(missing), "unexpected_session_count": unexpected_count,
        "calendar_coverage_ratio": ratio,
    }
    blocking: list[str] = []
    if _missing_exceeds(request, len(missing), ratio):
        blocking.append("Missing-session policy failed.")
    if unexpected_count and request["unexpected_session_policy"] == "FAIL_ON_ANY_UNEXPECTED":
        blocking.append("Unexpected-session policy failed.")
    status = "FAILED_VALIDATION" if blocking else ("COMPLETE" if not missing and not unexpected_count and not duplicates and not before and not after and not out_of_session else "PARTIAL")
    summary["calendar_coverage_status"] = status
    review = [] if status == "COMPLETE" else ["Calendar validation requires review of reported gaps or anomalies."]
    return summary, status, blocking, review


def _validate_bar(value: Any, request: dict[str, Any]) -> dict[str, Any]:
    payload = _mapping(value, "bar")
    _reject_unknown(payload, {"bar_id", "session_date", "timestamp", "source_sha256", "provenance"}, "bar")
    bar_id = _text(payload, "bar_id")
    _sha(payload.get("source_sha256"), "bar source_sha256")
    _provenance(payload.get("provenance"))
    timestamp = payload.get("timestamp")
    timestamp_utc = _parse_utc(timestamp) if timestamp is not None else None
    supplied_date = _date_text(payload.get("session_date"), "bar session_date") if payload.get("session_date") is not None else None
    if request["bar_timestamp_semantics"] == "SESSION_DATE":
        if supplied_date is None:
            raise ValueError("SESSION_DATE bars require session_date.")
        session_date = supplied_date
    else:
        if timestamp_utc is None:
            raise ValueError("BAR_OPEN_TIME and BAR_CLOSE_TIME bars require timestamp.")
        derived_date = timestamp_utc.astimezone(ZoneInfo(request["timezone"])).date().isoformat()
        if supplied_date is not None and supplied_date != derived_date:
            raise ValueError("bar session_date does not match timestamp timezone date.")
        session_date = derived_date
    return {"bar_id": bar_id, "session_date": session_date, "timestamp_utc": timestamp_utc, "raw": payload}


def _missing_exceeds(request: dict[str, Any], missing_count: int, coverage_ratio: float) -> bool:
    policy = request["missing_session_policy"]
    if policy == "REPORT_ONLY":
        return False
    if policy == "FAIL_ON_ANY_MISSING":
        return missing_count > 0
    maximum_count = request["maximum_missing_count"]
    if maximum_count is not None:
        return missing_count > maximum_count
    return (1.0 - coverage_ratio) > float(request["maximum_missing_ratio"])


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")
    return value


def _text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    return value


def _date_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date.") from exc
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256.")
    return value


def _provenance(value: Any, name: str = "provenance") -> dict[str, Any]:
    payload = _mapping(value, name)
    if not payload:
        raise ValueError(f"{name} must not be empty.")
    return payload


def _reject_unknown(payload: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}.")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("bar timestamp must be an ISO-8601 timezone-aware timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("bar timestamp must be an ISO-8601 timezone-aware timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("bar timestamp must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return _utc_text(value)
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}.")
