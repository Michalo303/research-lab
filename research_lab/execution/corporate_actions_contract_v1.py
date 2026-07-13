from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


REQUEST_VERSION = "corporate_actions_contract_request_v1"
RESULT_VERSION = "corporate_actions_contract_result_v1"
CONTRACT_VERSION = "corporate_actions_contract_v1"
_ACTION_TYPES = {
    "CASH_DIVIDEND",
    "STOCK_DIVIDEND",
    "SPLIT",
    "REVERSE_SPLIT",
    "SYMBOL_CHANGE",
    "MERGER",
    "SPINOFF",
    "DELISTING",
    "NO_ACTIONS_DECLARED",
}
_POINT_IN_TIME_STATUSES = {"POINT_IN_TIME_VERIFIED", "HISTORICAL_CORRECTION", "SOURCE_DECLARED"}
_POLICY_BASES = {
    "RAW_PRICES_NO_ADJUSTMENT": "RAW_PRICES",
    "SPLIT_ADJUSTED_ONLY": "SPLIT_ADJUSTED",
    "PROVIDER_ADJUSTED_AS_SUPPLIED": "PROVIDER_ADJUSTED",
    "EXPLICIT_ACTION_RECONSTRUCTION": "RAW_PRICES",
}


def build_corporate_actions_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    actions = sorted(validated["actions"], key=lambda item: (item["availability_timestamp"], item["action_id"]))
    timeline = [
        {
            "action_id": action["action_id"],
            "action_type": action["action_type"],
            "availability_timestamp": action["availability_timestamp"],
            "effective_timestamp": action["effective_timestamp"],
            "point_in_time_status": action["point_in_time_status"],
        }
        for action in actions
    ]
    symbol_edges = [
        {
            "action_id": action["action_id"],
            "predecessor_symbol": action["predecessor_symbol"],
            "successor_symbol": action["successor_symbol"],
            "effective_timestamp": action["effective_timestamp"],
        }
        for action in actions
        if action["action_type"] == "SYMBOL_CHANGE"
    ]
    delistings = [
        {
            "action_id": action["action_id"],
            "effective_timestamp": action["effective_timestamp"],
            "source_identity": action["source_identity"],
        }
        for action in actions
        if action["action_type"] == "DELISTING"
    ]
    no_actions_declared = any(action["action_type"] == "NO_ACTIONS_DECLARED" for action in actions)
    warnings = []
    if no_actions_declared:
        warnings.append("NO_ACTIONS_DECLARED is evidence limited to the declared source and as-of timestamp.")
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "corporate_actions_id": validated["corporate_actions_id"],
        "instrument_identity": validated["instrument_identity"],
        "validated_actions": actions,
        "action_timeline": timeline,
        "adjustment_policy": validated["adjustment_policy"],
        "point_in_time_coverage": {
            "as_of_timestamp": validated["as_of_timestamp"],
            "visible_action_count": len(actions),
            "no_actions_declared": no_actions_declared,
        },
        "symbol_lineage": symbol_edges,
        "delisting_evidence": delistings,
        "price_series_compatibility_status": "COMPATIBLE",
        "blocking_findings": [],
        "warnings": warnings,
        "input_sha256": _canonical_sha256(validated),
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        {
            "version", "corporate_actions_id", "instrument_identity", "adjustment_policy", "actions",
            "expected_price_series_identity", "expected_source_hashes", "as_of_timestamp", "provenance",
        },
        name="request",
    )
    policy = _required_text(payload, "adjustment_policy")
    if policy not in _POLICY_BASES:
        raise ValueError("adjustment_policy is not supported.")
    price_series = _validate_price_series(payload.get("expected_price_series_identity"))
    required_basis = _POLICY_BASES[policy]
    if price_series["adjustment_basis"] != required_basis:
        raise ValueError(f"{policy} requires {required_basis} price series.")
    instrument_identity = _validate_instrument_identity(payload.get("instrument_identity"))
    expected_hashes = _validate_hashes(payload.get("expected_source_hashes"))
    as_of_timestamp = _required_timestamp(payload.get("as_of_timestamp"), name="as_of_timestamp")
    actions = _validate_actions(
        payload.get("actions"),
        instrument_id=instrument_identity["instrument_id"],
        expected_hashes=expected_hashes,
        as_of_timestamp=as_of_timestamp,
    )
    return {
        "version": _required_exact_version(payload),
        "corporate_actions_id": _required_text(payload, "corporate_actions_id"),
        "instrument_identity": instrument_identity,
        "adjustment_policy": policy,
        "actions": actions,
        "expected_price_series_identity": price_series,
        "expected_source_hashes": expected_hashes,
        "as_of_timestamp": as_of_timestamp,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_actions(value: Any, *, instrument_id: str, expected_hashes: dict[str, str], as_of_timestamp: str) -> list[dict[str, Any]]:
    raw_actions = _required_list(value, name="actions")
    actions = [_validate_action(item) for item in raw_actions]
    ids: set[str] = set()
    semantic: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for action in actions:
        if action["action_id"] in ids:
            raise ValueError("duplicate action_id is not allowed.")
        ids.add(action["action_id"])
        if action["instrument_id"] != instrument_id:
            raise ValueError("action instrument_id does not match instrument_identity.")
        if action["source_sha256"] != expected_hashes.get(action["source_identity"]):
            raise ValueError("action source_sha256 does not match expected_source_hashes.")
        if action["availability_timestamp"] > as_of_timestamp:
            raise ValueError("action is not available at as_of_timestamp.")
        signature = (action["action_type"], action["effective_timestamp"], action["predecessor_symbol"])
        existing = semantic.get(signature)
        if existing is not None:
            if action["action_type"] in {"SPLIT", "REVERSE_SPLIT"} and action["factor"] != existing["factor"]:
                raise ValueError("contradictory split factors are not allowed.")
            raise ValueError("duplicate semantic action is not allowed.")
        semantic[signature] = action
    if any(action["action_type"] == "NO_ACTIONS_DECLARED" for action in actions) and len(actions) != 1:
        raise ValueError("NO_ACTIONS_DECLARED cannot be combined with other actions.")
    _reject_symbol_cycles(actions)
    return actions


def _validate_action(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="action")
    _reject_unknown_fields(
        payload,
        {
            "action_id", "instrument_id", "action_type", "announcement_timestamp", "availability_timestamp",
            "ex_timestamp", "effective_timestamp", "record_timestamp", "payment_timestamp", "factor", "amount",
            "currency", "predecessor_symbol", "successor_symbol", "source_identity", "source_sha256",
            "point_in_time_status", "provenance",
        },
        name="action",
    )
    action_type = _required_text(payload, "action_type")
    if action_type not in _ACTION_TYPES:
        raise ValueError("action_type is not supported.")
    announcement = _optional_timestamp(payload.get("announcement_timestamp"), name="announcement_timestamp")
    availability = _required_timestamp(payload.get("availability_timestamp"), name="availability_timestamp")
    ex_timestamp = _optional_timestamp(payload.get("ex_timestamp"), name="ex_timestamp")
    effective = _optional_timestamp(payload.get("effective_timestamp"), name="effective_timestamp")
    if action_type != "NO_ACTIONS_DECLARED" and (ex_timestamp is None or effective is None):
        raise ValueError("action ex_timestamp and effective_timestamp are required.")
    if announcement is not None and announcement > availability:
        raise ValueError("announcement_timestamp must not be later than availability_timestamp.")
    if ex_timestamp is not None and availability > ex_timestamp:
        raise ValueError("availability_timestamp must not be later than ex_timestamp.")
    if effective is not None and availability > effective:
        raise ValueError("availability_timestamp must not be later than effective_timestamp.")
    factor = _optional_finite_number(payload.get("factor"), name="factor")
    amount = _optional_finite_number(payload.get("amount"), name="amount")
    if action_type in {"SPLIT", "REVERSE_SPLIT", "STOCK_DIVIDEND"} and (factor is None or factor <= 0):
        raise ValueError("split and stock-dividend actions require a positive finite factor.")
    currency = _optional_upper_text(payload.get("currency"), name="currency")
    if action_type == "CASH_DIVIDEND":
        if amount is None:
            raise ValueError("cash dividends require a finite amount.")
        if currency is None:
            raise ValueError("cash dividends require currency.")
    predecessor = _optional_text(payload.get("predecessor_symbol"), name="predecessor_symbol")
    successor = _optional_text(payload.get("successor_symbol"), name="successor_symbol")
    if action_type == "SYMBOL_CHANGE" and (predecessor is None or successor is None):
        raise ValueError("symbol changes require predecessor_symbol and successor_symbol.")
    if action_type in {"MERGER", "SPINOFF"} and successor is None:
        raise ValueError("merger and spinoff actions require successor_symbol.")
    status = _required_text(payload, "point_in_time_status")
    if status not in _POINT_IN_TIME_STATUSES:
        raise ValueError("point_in_time_status is not supported.")
    return {
        "action_id": _required_text(payload, "action_id"), "instrument_id": _required_text(payload, "instrument_id"),
        "action_type": action_type, "announcement_timestamp": announcement, "availability_timestamp": availability,
        "ex_timestamp": ex_timestamp, "effective_timestamp": effective,
        "record_timestamp": _optional_timestamp(payload.get("record_timestamp"), name="record_timestamp"),
        "payment_timestamp": _optional_timestamp(payload.get("payment_timestamp"), name="payment_timestamp"),
        "factor": factor, "amount": amount, "currency": currency, "predecessor_symbol": predecessor,
        "successor_symbol": successor, "source_identity": _required_text(payload, "source_identity"),
        "source_sha256": _required_sha256(payload, "source_sha256"), "point_in_time_status": status,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_instrument_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="instrument_identity")
    _reject_unknown_fields(payload, {"instrument_id", "provider_symbol"}, name="instrument_identity")
    return {"instrument_id": _required_text(payload, "instrument_id"), "provider_symbol": _required_text(payload, "provider_symbol")}


def _validate_price_series(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="expected_price_series_identity")
    _reject_unknown_fields(payload, {"price_series_id", "adjustment_basis", "source_sha256"}, name="expected_price_series_identity")
    return {
        "price_series_id": _required_text(payload, "price_series_id"),
        "adjustment_basis": _required_text(payload, "adjustment_basis"),
        "source_sha256": _required_sha256(payload, "source_sha256"),
    }


def _validate_hashes(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="expected_source_hashes")
    if not payload:
        raise ValueError("expected_source_hashes must not be empty.")
    return {_required_text({"value": key}, "value"): _required_sha256({"value": item}, "value") for key, item in payload.items()}


def _reject_symbol_cycles(actions: list[dict[str, Any]]) -> None:
    edges = {action["predecessor_symbol"]: action["successor_symbol"] for action in actions if action["action_type"] == "SYMBOL_CHANGE"}
    for start in edges:
        seen: set[str] = set()
        current = start
        while current in edges:
            if current in seen:
                raise ValueError("symbol-change cycle is not allowed.")
            seen.add(current)
            current = edges[current]


def _required_exact_version(payload: dict[str, Any]) -> str:
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return version


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


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


def _optional_text(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    return _required_text({"value": value}, "value")


def _optional_upper_text(value: Any, *, name: str) -> str | None:
    text = _optional_text(value, name=name)
    if text is not None and text != text.upper():
        raise ValueError(f"{name} must be uppercase text.")
    return text


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value


def _required_timestamp(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.strip().endswith("Z"):
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp ending in Z.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 UTC timestamp.") from exc
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional_timestamp(value: Any, *, name: str) -> str | None:
    return None if value is None else _required_timestamp(value, name=name)


def _optional_finite_number(value: Any, *, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")
    return float(value)


def _validate_provenance(value: Any) -> dict[str, str | int | float | bool | None]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, item in payload.items():
        key_text = str(key).strip()
        if not key_text:
            raise ValueError("provenance keys must be non-empty text.")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"provenance.{key_text} must be a JSON scalar.")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"provenance.{key_text} must be finite.")
        normalized[key_text] = item
    return normalized


def _reject_unknown_fields(payload: dict[str, Any], allowed: set[str], *, name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")
