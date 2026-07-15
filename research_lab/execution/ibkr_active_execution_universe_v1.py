"""Offline, deterministic IBKR active execution-universe eligibility review."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from research_lab.execution.instrument_identity_execution_routing_v1 import build_instrument_identity_execution_routing

REQUEST_VERSION = "ibkr_active_execution_universe_request_v1"
RESULT_VERSION = "ibkr_active_execution_universe_result_v1"
CONTRACT_VERSION = "ibkr_active_execution_universe_v1"
_FIELDS = {"version", "universe_id", "as_of_timestamp", "base_currency", "candidates", "universe_policy", "liquidity_policy", "eligibility_evidence_policy", "provenance"}
_CANDIDATE_FIELDS = {"candidate_id", "identity_routing_result", "exchange", "ticker", "trading_currency", "proposed_ibkr_execution_route", "instrument_type", "security_type", "trading_permission_category", "trading_permission_evidence", "kid_or_retail_documentation_status", "documentation_evidence", "offline_price_observation", "offline_median_volume_observation", "offline_spread_observation", "corporate_action_policy", "delisting_policy", "settlement_currency_policy", "allowed_order_types", "regular_session_policy", "provenance"}
_POLICY = {"long_only": True, "leverage_allowed": False, "margin_assumed": False, "shorting_allowed": False, "derivatives_allowed": False, "fractional_shares_assumed": False, "extended_hours_assumed": False}
_TYPES = {"COMMON_STOCK", "UCITS_ETF", "PHYSICAL_GOLD_ETC", "COMMODITY_ETC", "NON_UCITS_ETF"}
_ELIGIBLE_TYPES = {"COMMON_STOCK", "UCITS_ETF", "PHYSICAL_GOLD_ETC", "COMMODITY_ETC"}


def build_ibkr_active_execution_universe(request: dict[str, object]) -> dict[str, object]:
    """Review supplied evidence only; this function performs no provider or broker I/O."""
    value = _validate_request(request)
    results = [_review_candidate(item, value) for item in value["candidates"]]
    results.sort(key=lambda item: (item["eligibility_status"], item["candidate_id"]))
    accepted = [item for item in results if item["eligibility_status"] == "ELIGIBLE"]
    review = [item for item in results if item["eligibility_status"] == "REVIEW_REQUIRED"]
    blocked = [item for item in results if item["eligibility_status"] == "BLOCKED"]
    failed = [item for item in results if item["eligibility_status"] == "FAILED_VALIDATION"]
    status = "FAILED_VALIDATION" if failed else "REVIEW_REQUIRED" if review or blocked else "PASS"
    result: dict[str, Any] = {"version": RESULT_VERSION, "contract_version": CONTRACT_VERSION, "status": status, "validation_status": status, "universe_id": value["universe_id"], "as_of_timestamp": value["as_of_timestamp"], "base_currency": value["base_currency"], "universe_policy": copy.deepcopy(value["universe_policy"]), "liquidity_policy": copy.deepcopy(value["liquidity_policy"]), "eligibility_evidence_policy": copy.deepcopy(value["eligibility_evidence_policy"]), "instrument_results": results, "accepted_instruments": accepted, "review_required_instruments": review, "blocked_instruments": blocked, "failed_instruments": failed, "findings": sorted({finding for item in results for finding in item["findings"]}), "evidence_lineage": {item["candidate_id"]: {"identity_key": item["identity_key"], "child_hash": item["identity_routing_result"]["output_payload_sha256"]} for item in results}, "recomputed_m31a_child_hashes": {item["candidate_id"]: item["identity_routing_result"]["output_payload_sha256"] for item in results}, "input_sha256": value["input_sha256"], "provenance": copy.deepcopy(value["provenance"]), "safety_flags": {"network_used": False, "provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "ibkr_connected": False, "contract_ids_resolved": False, "live_prices_queried": False, "orders_generated": False, "orders_transmitted": False, "production_runtime_supported": False}}
    result = _canonical_decimals(result)
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validate_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - _FIELDS:
        raise ValueError("unknown request field")
    if set(raw) != _FIELDS or raw.get("version") != REQUEST_VERSION:
        raise ValueError("invalid request fields or version")
    value = copy.deepcopy(raw)
    for key in ("universe_id", "as_of_timestamp", "base_currency"):
        value[key] = _text(value[key], key)
    as_of = _timestamp(value["as_of_timestamp"], "as_of_timestamp").date()
    if not isinstance(value["candidates"], list):
        raise ValueError("candidates must be a list")
    if not isinstance(value["universe_policy"], dict) or value["universe_policy"] != _POLICY:
        raise ValueError("unsafe universe policy")
    liquidity = value["liquidity_policy"]
    if not isinstance(liquidity, dict) or set(liquidity) != {"minimum_price", "minimum_median_volume", "maximum_spread_bps"}:
        raise ValueError("invalid liquidity policy")
    for key in liquidity:
        liquidity[key] = _decimal(liquidity[key], key, positive=True)
    evidence = value["eligibility_evidence_policy"]
    if not isinstance(evidence, dict) or set(evidence) != {"maximum_evidence_age_days", "require_explicit_retail_evidence"} or isinstance(evidence["maximum_evidence_age_days"], bool) or not isinstance(evidence["maximum_evidence_age_days"], int) or evidence["maximum_evidence_age_days"] < 0 or evidence["require_explicit_retail_evidence"] is not True:
        raise ValueError("invalid eligibility evidence policy")
    if not isinstance(value["provenance"], dict):
        raise ValueError("provenance must be an object")
    seen: set[str] = set(); candidates = []
    for raw_candidate in value["candidates"]:
        candidate = _validate_candidate(raw_candidate, as_of, value)
        listing = candidate["identity_routing_result"]["identity_key"]
        if listing in seen:
            raise ValueError("duplicate exact listing")
        seen.add(listing); candidates.append(candidate)
    value["candidates"] = sorted(candidates, key=lambda item: item["candidate_id"])
    value["input_sha256"] = _sha(value)
    return value


def _validate_candidate(raw: Any, as_of: date, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _CANDIDATE_FIELDS:
        raise ValueError("candidate has unknown or missing field")
    item = copy.deepcopy(raw)
    for key in ("candidate_id", "exchange", "ticker", "trading_currency", "proposed_ibkr_execution_route", "instrument_type", "security_type", "trading_permission_category", "kid_or_retail_documentation_status", "corporate_action_policy", "delisting_policy", "settlement_currency_policy", "regular_session_policy"):
        item[key] = _text(item[key], key)
    if item["instrument_type"] not in _TYPES:
        raise ValueError("unsupported instrument type")
    if item["security_type"].upper() in {"DERIVATIVE", "OPTION", "FUTURE", "CFD"}:
        raise ValueError("derivative rejected")
    child = _verify_child(item["identity_routing_result"])
    instrument = child["instrument"]
    classification = " ".join((item["instrument_type"], item["security_type"], instrument["legal_product_classification"])).upper()
    if "LEVERAGED" in classification or "INVERSE" in classification:
        raise ValueError("leveraged and inverse products are rejected")
    if any(item[key] != instrument[child_key] for key, child_key in (("exchange", "selected_exchange"), ("ticker", "exchange_ticker"), ("trading_currency", "trading_currency"))):
        raise ValueError("conflicting exchange, ticker, or currency")
    if item["instrument_type"] != instrument["instrument_type"]:
        raise ValueError("conflicting instrument type")
    if not isinstance(item["trading_permission_evidence"], dict):
        raise ValueError("missing trading-permission evidence")
    item["_trading_permission_current"] = _evidence(item["trading_permission_evidence"], as_of, request["eligibility_evidence_policy"], "trading-permission")
    if item["documentation_evidence"] is not None:
        if not isinstance(item["documentation_evidence"], dict): raise ValueError("invalid documentation evidence")
        item["_documentation_current"] = _evidence(item["documentation_evidence"], as_of, request["eligibility_evidence_policy"], "documentation")
    else:
        item["_documentation_current"] = False
    for key, field in (("offline_price_observation", "value"), ("offline_median_volume_observation", "value"), ("offline_spread_observation", "value_bps")):
        obs = item[key]
        if not isinstance(obs, dict) or set(obs) != {field, "timestamp"}:
            raise ValueError(f"missing {key}")
        obs[field] = _decimal(obs[field], key, positive=True); _timestamp(_text(obs["timestamp"], key + " timestamp"), key + " timestamp")
    if not isinstance(item["allowed_order_types"], list) or item["allowed_order_types"] != ["LIMIT"]:
        raise ValueError("allowed order types must be review-only LIMIT")
    if item["regular_session_policy"] != "REGULAR_SESSION_ONLY" or not isinstance(item["provenance"], dict):
        raise ValueError("invalid regular-session policy or provenance")
    return item


def _review_candidate(item: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    findings: list[str] = []
    typ = item["instrument_type"]
    if not item.pop("_trading_permission_current"):
        findings.append("STALE_TRADING_PERMISSION_EVIDENCE")
    documentation_current = item.pop("_documentation_current")
    if typ == "NON_UCITS_ETF": findings.append("US_ETF_RETAIL_ELIGIBILITY_NOT_PROVEN")
    if typ not in _ELIGIBLE_TYPES and typ != "NON_UCITS_ETF": findings.append("UNSUPPORTED_PRODUCT_TYPE")
    if typ in {"UCITS_ETF", "PHYSICAL_GOLD_ETC", "COMMODITY_ETC"} and (not documentation_current or item["kid_or_retail_documentation_status"] != "CURRENT_AVAILABLE"):
        findings.append("CURRENT_RETAIL_DOCUMENTATION_REQUIRED")
    if typ in {"PHYSICAL_GOLD_ETC", "COMMODITY_ETC"} and item["identity_routing_result"]["instrument"]["legal_product_classification"] == "UCITS_ETF": findings.append("ETC_MUST_NOT_BE_CLASSIFIED_AS_UCITS")
    if item["offline_price_observation"]["value"] < request["liquidity_policy"]["minimum_price"]: findings.append("PRICE_BELOW_THRESHOLD")
    if item["offline_median_volume_observation"]["value"] < request["liquidity_policy"]["minimum_median_volume"]: findings.append("VOLUME_BELOW_THRESHOLD")
    if item["offline_spread_observation"]["value_bps"] > request["liquidity_policy"]["maximum_spread_bps"]: findings.append("SPREAD_ABOVE_THRESHOLD")
    if not item["corporate_action_policy"] or not item["delisting_policy"]: findings.append("MISSING_CORPORATE_ACTION_OR_DELISTING_POLICY")
    status = "BLOCKED" if typ == "NON_UCITS_ETF" else "REVIEW_REQUIRED" if findings else "ELIGIBLE"
    result = copy.deepcopy(item); result.update({"eligibility_status": status, "identity_key": item["identity_routing_result"]["identity_key"], "findings": sorted(findings), "automatic_execution_allowed": False, "automatic_order_generation_allowed": False})
    return result


def _verify_child(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError("malformed M31A child")
    child = copy.deepcopy(raw); declared = child.pop("output_payload_sha256", None)
    if not isinstance(declared, str) or _sha(child) != declared: raise ValueError("child hash mismatch")
    try:
        rebuilt = build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": child.get("instrument"), "execution_route": child.get("execution_route"), "provenance": child.get("provenance")})
    except ValueError as exc:
        raise ValueError("malformed M31A child") from exc
    if rebuilt != raw: raise ValueError("malformed M31A child")
    return copy.deepcopy(raw)


def _evidence(raw: dict[str, Any], as_of: date, policy: dict[str, Any], name: str) -> bool:
    if set(raw) != {"status", "as_of_date", "source"} or raw["status"] != "EXPLICIT_CURRENT" or not isinstance(raw["source"], str) or not raw["source"].strip(): raise ValueError(f"invalid {name} evidence")
    try: evidence_date = date.fromisoformat(raw["as_of_date"])
    except (TypeError, ValueError) as exc: raise ValueError(f"invalid {name} evidence") from exc
    return 0 <= (as_of - evidence_date).days <= policy["maximum_evidence_age_days"]


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _timestamp(value: str, name: str) -> datetime:
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None: raise ValueError(f"invalid {name}")
    return parsed


def _decimal(value: Any, name: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)): raise ValueError(f"invalid decimal {name}")
    try: parsed = Decimal(str(value))
    except InvalidOperation as exc: raise ValueError(f"invalid decimal {name}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0): raise ValueError(f"invalid decimal {name}")
    return parsed


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False, default=str).encode("utf-8")).hexdigest()


def _canonical_decimals(value: Any) -> Any:
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, dict): return {key: _canonical_decimals(item) for key, item in value.items()}
    if isinstance(value, list): return [_canonical_decimals(item) for item in value]
    return value
