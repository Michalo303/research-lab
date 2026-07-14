from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN
from typing import Any


REQUEST_VERSION = "point_in_time_fx_conversion_contract_request_v1"
RESULT_VERSION = "point_in_time_fx_conversion_contract_result_v1"
CONTRACT_VERSION = "point_in_time_fx_conversion_contract_v1"
_ROUNDING = {"ROUND_HALF_EVEN": ROUND_HALF_EVEN, "ROUND_HALF_UP": ROUND_HALF_UP, "ROUND_DOWN": ROUND_DOWN}


def build_point_in_time_fx_conversion_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    latest_decision = max(item["decision_timestamp"] for item in validated["instrument_values"])
    if any(observation["observation_timestamp"] > latest_decision for observation in validated["fx_observations"]):
        raise ValueError("observation_timestamp must not be newer than decision timestamp.")
    converted: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for item in validated["instrument_values"]:
        converted_item, used = _convert(item, validated)
        converted.append(converted_item)
        selected.extend(used)
    converted.sort(key=lambda item: item["instrument_id"])
    selected.sort(key=lambda item: item["observation_id"])
    result: dict[str, Any] = {
        "version": RESULT_VERSION, "contract_version": CONTRACT_VERSION, "conversion_id": validated["conversion_id"],
        "base_currency": validated["base_currency"], "conversion_status": "SUCCESS", "converted_values": converted,
        "conversion_paths": [{key: item[key] for key in ("instrument_id", "path_type", "path_id", "selected_observation_ids", "arithmetic_formula")} for item in converted],
        "selected_observations": selected, "direct_conversions": [item["instrument_id"] for item in converted if item["path_type"] == "DIRECT"],
        "inverse_conversions": [item["instrument_id"] for item in converted if item["path_type"] == "INVERSE"],
        "cross_conversions": [item["instrument_id"] for item in converted if item["path_type"] == "CROSS"],
        "same_currency_conversions": [item["instrument_id"] for item in converted if item["path_type"] == "SAME_CURRENCY"],
        "rate_ages_seconds": {item["instrument_id"]: item["rate_ages_seconds"] for item in converted}, "stale_conversions": [], "missing_conversions": [],
        "blocking_findings": [], "review_findings": [], "warnings": [], "complete_lineage": {item["instrument_id"]: {"instrument_source_sha256": item["source_hashes"]["instrument_source_sha256"], "fx_source_sha256": item["source_hashes"]["fx_source_sha256"]} for item in converted},
        "input_sha256": _canonical_sha256(_public_validated(validated)), "provider_calls_used": 0, "network_used": False,
        "filesystem_reads_performed": False, "filesystem_writes_performed": False, "registry_write_performed": False,
        "broker_actions_used": 0, "paper_trading_performed": False, "deployment_performed": False,
        "production_runtime_supported": False, "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _convert(item: dict[str, Any], request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source, target, decision = item["currency"], request["base_currency"], item["decision_timestamp"]
    if source == target:
        return _output(item, request, "SAME_CURRENCY", None, Decimal("1"), [], "source_value * 1", [])
    direct = _select(request["fx_observations"], source, target, decision)
    if direct is not None:
        return _output(item, request, "DIRECT", None, direct["rate"], [direct], "source_value * direct_rate", [direct])
    inverse = _select(request["fx_observations"], target, source, decision)
    if inverse is not None and request["inverse_rate_policy"] == "ALLOW_EXPLICIT_INVERSE":
        return _output(item, request, "INVERSE", None, Decimal("1") / inverse["rate"], [inverse], "source_value / inverse_rate", [inverse])
    paths = [path for path in request["declared_cross_paths"] if path["source_currency"] == source and path["target_currency"] == target]
    if request["cross_rate_policy"] == "ALLOW_DECLARED_CROSS_PATHS" and not paths and any(o["base_currency"] == source for o in request["fx_observations"]):
        raise ValueError("undeclared cross path is not allowed.")
    if request["cross_rate_policy"] == "ALLOW_DECLARED_CROSS_PATHS" and len(paths) > 1:
        raise ValueError("ambiguous cross paths are not allowed.")
    if request["cross_rate_policy"] == "ALLOW_DECLARED_CROSS_PATHS" and paths:
        path = paths[0]
        first = _leg(request["fx_observations"], path["first_pair_id"], source, path["intermediary_currency"], decision, path["first_arithmetic_orientation"])
        second = _leg(request["fx_observations"], path["second_pair_id"], path["intermediary_currency"], target, decision, path["second_arithmetic_orientation"])
        if first is None or second is None:
            raise ValueError("missing cross leg.")
        age = _age(first, decision) + _age(second, decision)
        if age > path["maximum_combined_staleness_seconds"]:
            raise ValueError("stale cross leg.")
        rate = _oriented_rate(first, path["first_arithmetic_orientation"]) * _oriented_rate(second, path["second_arithmetic_orientation"])
        output, _ = _output(item, request, "CROSS", path["path_id"], rate, [first, second], "source_value * first_leg_rate * second_leg_rate", [first, second])
        return output, [_selected(first, decision), _selected(second, decision)]
    raise ValueError("missing required conversion.")


def _output(item: dict[str, Any], request: dict[str, Any], path_type: str, path_id: str | None, rate: Decimal, observations: list[dict[str, Any]], formula: str, age_observations: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    converted = item["value"] * rate
    quantized = converted.quantize(request["quantum"], rounding=request["rounding"])
    if not quantized.is_finite(): raise ValueError("non-finite output.")
    ages = [_age(observation, item["decision_timestamp"]) for observation in age_observations]
    if any(age > request["maximum_staleness_seconds"] for age in ages): raise ValueError("stale rate.")
    return {"instrument_id": item["instrument_id"], "source_currency": item["currency"], "target_currency": request["base_currency"],
            "source_value": _decimal(item["value"], request), "converted_value": _decimal(quantized, request), "effective_rate": _decimal(rate, request),
            "path_type": path_type, "path_id": path_id, "decision_timestamp": item["decision_timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selected_observation_ids": [observation["observation_id"] for observation in observations],
            "source_hashes": {"instrument_source_sha256": item["source_sha256"], "fx_source_sha256": [observation["source_sha256"] for observation in observations]},
            "rate_ages_seconds": max(ages, default=0), "arithmetic_formula": formula}, [_selected(observation, item["decision_timestamp"]) for observation in observations]


def _select(observations: list[dict[str, Any]], base: str, quote: str, decision: datetime) -> dict[str, Any] | None:
    candidates = [o for o in observations if o["base_currency"] == base and o["quote_currency"] == quote and o["availability_timestamp"] <= decision and o["observation_timestamp"] <= decision]
    if not candidates: return None
    candidates.sort(key=lambda o: (o["availability_timestamp"], o["observation_timestamp"]), reverse=True)
    if len(candidates) > 1 and (candidates[0]["availability_timestamp"], candidates[0]["observation_timestamp"]) == (candidates[1]["availability_timestamp"], candidates[1]["observation_timestamp"]):
        raise ValueError("tied non-identical observations are ambiguous.")
    return candidates[0]


def _leg(observations: list[dict[str, Any]], pair_id: str, source: str, target: str, decision: datetime, orientation: str) -> dict[str, Any] | None:
    if orientation not in {"MULTIPLY", "DIVIDE"}: raise ValueError("invalid rate orientation.")
    base, quote = (source, target) if orientation == "MULTIPLY" else (target, source)
    for observation in observations:
        if observation["pair_id"] == pair_id and (observation["base_currency"], observation["quote_currency"]) != (base, quote):
            raise ValueError("cross path pair identity has a currency mismatch.")
    observation = _select(observations, base, quote, decision)
    if observation is not None and observation["pair_id"] != pair_id: return None
    return observation


def _oriented_rate(observation: dict[str, Any], orientation: str) -> Decimal:
    return observation["rate"] if orientation == "MULTIPLY" else Decimal("1") / observation["rate"]

def _age(observation: dict[str, Any], decision: datetime) -> int: return int((decision - observation["observation_timestamp"]).total_seconds())
def _selected(observation: dict[str, Any], decision: datetime) -> dict[str, Any]: return {"observation_id": observation["observation_id"], "pair_id": observation["pair_id"], "source_sha256": observation["source_sha256"], "age_seconds": _age(observation, decision)}
def _decimal(value: Decimal, request: dict[str, Any]) -> str: return format(value.quantize(request["quantum"], rounding=request["rounding"]), "f")


def _validate_request(raw: dict[str, object]) -> dict[str, Any]:
    request = _mapping(raw, "request")
    _unknown(request, {"version", "conversion_id", "base_currency", "instrument_values", "fx_observations", "decision_timestamps", "maximum_staleness_seconds", "direct_rate_policy", "inverse_rate_policy", "cross_rate_policy", "declared_cross_paths", "expected_source_hashes", "precision_policy", "provenance"}, "request")
    if _text(request.get("version"), "version") != REQUEST_VERSION: raise ValueError(f"version must be {REQUEST_VERSION}.")
    precision = _mapping(request.get("precision_policy"), "precision_policy"); _unknown(precision, {"decimal_places", "rounding_mode"}, "precision_policy")
    places = request_int(precision.get("decimal_places"), "decimal_places", allow_zero=True); mode = _text(precision.get("rounding_mode"), "rounding_mode")
    if mode not in _ROUNDING: raise ValueError("rounding_mode is not supported.")
    instruments = [_instrument(item) for item in _list(request.get("instrument_values"), "instrument_values")]
    observations = [_observation(item) for item in _list(request.get("fx_observations"), "fx_observations", allow_empty=True)]
    _unique(instruments, "instrument_id", "duplicate instrument_id") ; _unique(observations, "observation_id", "duplicate observation_id")
    if observations != sorted(observations, key=lambda o: (o["availability_timestamp"], o["observation_timestamp"], o["observation_id"])): raise ValueError("fx_observations must use deterministic chronological order.")
    semantic: set[tuple[Any, ...]] = set()
    for o in observations:
        fingerprint = (o["pair_id"], o["base_currency"], o["quote_currency"], o["observation_timestamp"], o["availability_timestamp"], str(o["rate"]), o["source_sha256"])
        if fingerprint in semantic: raise ValueError("duplicate semantic observation is not allowed.")
        semantic.add(fingerprint)
    decisions = _mapping(request.get("decision_timestamps"), "decision_timestamps")
    for item in instruments:
        if _timestamp(decisions.get(item["instrument_id"]), "decision_timestamps") != item["decision_timestamp"]: raise ValueError("decision_timestamps must exactly match instrument decision_timestamp.")
    expected = _mapping(request.get("expected_source_hashes"), "expected_source_hashes"); _unknown(expected, {"instrument_values", "fx_observations"}, "expected_source_hashes")
    for kind, items, identity in (("instrument_values", instruments, "instrument_id"), ("fx_observations", observations, "observation_id")):
        hashes = _mapping(expected.get(kind), f"expected_source_hashes.{kind}")
        for item in items:
            if hashes.get(item[identity]) != item["source_sha256"]: raise ValueError("expected source hash is missing or mismatched.")
    paths = [_path(item) for item in _list(request.get("declared_cross_paths"), "declared_cross_paths", allow_empty=True)]; _unique(paths, "path_id", "duplicate path_id")
    return {"version": REQUEST_VERSION, "conversion_id": _text(request.get("conversion_id"), "conversion_id"), "base_currency": _currency(request.get("base_currency"), "base_currency"), "instrument_values": instruments, "fx_observations": observations, "decision_timestamps": {key: _timestamp(value, "decision_timestamp").strftime("%Y-%m-%dT%H:%M:%SZ") for key, value in decisions.items()}, "maximum_staleness_seconds": request_int(request.get("maximum_staleness_seconds"), "maximum_staleness_seconds", allow_zero=True), "direct_rate_policy": _policy(request.get("direct_rate_policy"), {"REQUIRE_EXPLICIT_DIRECT_PAIR", "ALLOW_DIRECT_WHEN_AVAILABLE"}, "direct_rate_policy"), "inverse_rate_policy": _policy(request.get("inverse_rate_policy"), {"REJECT_INVERSE", "ALLOW_EXPLICIT_INVERSE"}, "inverse_rate_policy"), "cross_rate_policy": _policy(request.get("cross_rate_policy"), {"REJECT_CROSS_RATE", "ALLOW_DECLARED_CROSS_PATHS"}, "cross_rate_policy"), "declared_cross_paths": paths, "expected_source_hashes": expected, "precision_policy": {"decimal_places": places, "rounding_mode": mode}, "quantum": Decimal(1).scaleb(-places), "rounding": _ROUNDING[mode], "provenance": _provenance(request.get("provenance"))}


def _instrument(raw: Any) -> dict[str, Any]:
    value = _mapping(raw, "instrument_value"); _unknown(value, {"instrument_id", "currency", "decision_timestamp", "value", "source_identity", "source_sha256", "provenance"}, "instrument_value")
    return {"instrument_id": _text(value.get("instrument_id"), "instrument_id"), "currency": _currency(value.get("currency"), "currency"), "decision_timestamp": _timestamp(value.get("decision_timestamp"), "decision_timestamp"), "value": _number(value.get("value"), "value", positive=False), "source_identity": _text(value.get("source_identity"), "source_identity"), "source_sha256": _sha(value.get("source_sha256"), "source_sha256"), "provenance": _provenance(value.get("provenance"))}
def _observation(raw: Any) -> dict[str, Any]:
    value = _mapping(raw, "fx_observation"); _unknown(value, {"observation_id", "pair_id", "base_currency", "quote_currency", "observation_timestamp", "availability_timestamp", "rate", "source_identity", "source_sha256", "point_in_time_status", "provenance"}, "fx_observation")
    base, quote = _currency(value.get("base_currency"), "base_currency"), _currency(value.get("quote_currency"), "quote_currency")
    if base == quote: raise ValueError("FX pair currencies must differ.")
    pair_id = _text(value.get("pair_id"), "pair_id")
    observed, available = _timestamp(value.get("observation_timestamp"), "observation_timestamp"), _timestamp(value.get("availability_timestamp"), "availability_timestamp")
    if available < observed: raise ValueError("availability_timestamp must not be earlier than observation_timestamp.")
    return {"observation_id": _text(value.get("observation_id"), "observation_id"), "pair_id": pair_id, "base_currency": base, "quote_currency": quote, "observation_timestamp": observed, "availability_timestamp": available, "rate": _number(value.get("rate"), "rate", positive=True), "source_identity": _text(value.get("source_identity"), "source_identity"), "source_sha256": _sha(value.get("source_sha256"), "source_sha256"), "point_in_time_status": _text(value.get("point_in_time_status"), "point_in_time_status"), "provenance": _provenance(value.get("provenance"))}
def _path(raw: Any) -> dict[str, Any]:
    value = _mapping(raw, "declared_cross_path"); _unknown(value, {"path_id", "source_currency", "intermediary_currency", "target_currency", "first_pair_id", "first_arithmetic_orientation", "second_pair_id", "second_arithmetic_orientation", "maximum_combined_staleness_seconds", "provenance"}, "declared_cross_path")
    source, intermediate, target = _currency(value.get("source_currency"), "source_currency"), _currency(value.get("intermediary_currency"), "intermediary_currency"), _currency(value.get("target_currency"), "target_currency")
    if len({source, intermediate, target}) != 3: raise ValueError("cyclic cross path is not allowed.")
    first_orientation = _text(value.get("first_arithmetic_orientation"), "first_arithmetic_orientation")
    second_orientation = _text(value.get("second_arithmetic_orientation"), "second_arithmetic_orientation")
    if first_orientation not in {"MULTIPLY", "DIVIDE"} or second_orientation not in {"MULTIPLY", "DIVIDE"}:
        raise ValueError("invalid rate orientation.")
    return {"path_id": _text(value.get("path_id"), "path_id"), "source_currency": source, "intermediary_currency": intermediate, "target_currency": target, "first_pair_id": _text(value.get("first_pair_id"), "first_pair_id"), "first_arithmetic_orientation": first_orientation, "second_pair_id": _text(value.get("second_pair_id"), "second_pair_id"), "second_arithmetic_orientation": second_orientation, "maximum_combined_staleness_seconds": request_int(value.get("maximum_combined_staleness_seconds"), "maximum_combined_staleness_seconds", allow_zero=True), "provenance": _provenance(value.get("provenance"))}

def _public_validated(value: Any) -> Any:
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict): return {key: _public_validated(item) for key, item in value.items() if key not in {"quantum", "rounding"}}
    if isinstance(value, list): return [_public_validated(item) for item in value]
    return value
def _canonical_sha256(payload: Any) -> str: return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError(f"{name} must be an object.")
    return dict(raw)
def _list(raw: Any, name: str, allow_empty: bool = False) -> list[Any]:
    if not isinstance(raw, list) or (not raw and not allow_empty): raise ValueError(f"{name} must be a non-empty list.")
    return list(raw)
def _unknown(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown: raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}.")
def _text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip(): raise ValueError(f"{name} must be non-empty text.")
    return raw
def _currency(raw: Any, name: str) -> str:
    value = _text(raw, name)
    if value != value.upper(): raise ValueError(f"{name} must be uppercase.")
    return value
def _timestamp(raw: Any, name: str) -> datetime:
    value = _text(raw, name)
    if not value.endswith("Z"): raise ValueError(f"{name} must be UTC and end in Z.")
    try: return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc: raise ValueError(f"{name} must be a valid UTC timestamp.") from exc
def _number(raw: Any, name: str, positive: bool) -> Decimal:
    if isinstance(raw, bool): raise ValueError(f"{name} must be finite.")
    try: value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc: raise ValueError(f"{name} must be finite.") from exc
    if not value.is_finite(): raise ValueError(f"{name} must be {'positive finite' if positive else 'finite'}.")
    if positive and value <= 0: raise ValueError(f"{name} must be positive finite.")
    return value
def _sha(raw: Any, name: str) -> str:
    value = _text(raw, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value): raise ValueError(f"{name} must be a lowercase SHA-256.")
    return value
def _provenance(raw: Any) -> dict[str, str | int | float | bool | None]:
    value = _mapping(raw, "provenance"); result: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or isinstance(item, (dict, list)) or (isinstance(item, float) and not math.isfinite(item)): raise ValueError("provenance must contain JSON scalars.")
        result[key] = item
    return result
def _policy(raw: Any, allowed: set[str], name: str) -> str:
    value = _text(raw, name)
    if value not in allowed: raise ValueError(f"{name} is not supported.")
    return value
def request_int(raw: Any, name: str, allow_zero: bool) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or (raw == 0 and not allow_zero): raise ValueError(f"{name} must be a non-negative integer.")
    return raw
def _unique(items: list[dict[str, Any]], key: str, message: str) -> None:
    if len({item[key] for item in items}) != len(items): raise ValueError(f"{message} is not allowed.")
