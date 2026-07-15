"""Deterministic, review-only instrument identity and execution routing."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from typing import Any


REQUEST_VERSION = "instrument_identity_execution_routing_request_v1"
RESULT_VERSION = "instrument_identity_execution_routing_result_v1"
CONTRACT_VERSION = "instrument_identity_execution_routing_v1"

_TOP_LEVEL = {"version", "instrument", "execution_route", "provenance"}
_INSTRUMENT = {
    "instrument_id", "legal_name", "instrument_type", "security_type", "isin",
    "primary_exchange", "selected_exchange", "exchange_ticker", "provider_symbol",
    "trading_currency", "issuer", "domicile", "share_class_identity",
    "distribution_policy", "currency_hedging", "legal_product_classification",
    "kid_status", "point_in_time_metadata_status", "metadata_as_of_date", "provenance",
}
_ROUTE = {
    "route", "automation_allowed", "manual_only", "risk_inclusion_required",
    "automatic_liquidation_allowed", "automatic_order_generation_allowed",
    "expected_holding_horizon", "eligibility_evidence", "eligibility_as_of_date",
}
_INSTRUMENT_TYPES = {
    "COMMON_STOCK", "UCITS_ETF", "NON_UCITS_ETF", "PHYSICAL_GOLD_ETC",
    "COMMODITY_ETC", "INDEX_BENCHMARK", "RESEARCH_PROXY", "OTHER_UNSUPPORTED",
}
_ROUTES = {
    "FIO_MANUAL_LONG_TERM", "IBKR_AUTOMATED_ELIGIBLE", "IBKR_REVIEW_REQUIRED",
    "IBKR_RETAIL_BLOCKED", "RESEARCH_ONLY", "UNSUPPORTED",
}


def build_instrument_identity_execution_routing(request: dict[str, object]) -> dict[str, object]:
    """Validate exact identity and fail-closed execution eligibility without I/O."""
    value = _validate(request)
    instrument, route = value["instrument"], value["execution_route"]
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "validation_status": "PASS",
        "instrument": instrument,
        "execution_route": route,
        "identity_key": _identity_key(instrument),
        "input_sha256": value["input_sha256"],
        "safety_flags": {
            "broker_calls_used": 0,
            "provider_calls_used": 0,
            "network_used": False,
            "automatic_orders_generated": False,
            "automatic_liquidation_allowed": False,
            "production_runtime_supported": False,
        },
        "provenance": value["provenance"],
    }
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validate(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("request must be an object")
    if set(raw) - _TOP_LEVEL:
        raise ValueError("unknown top-level request field")
    if set(raw) != _TOP_LEVEL or raw.get("version") != REQUEST_VERSION:
        raise ValueError("invalid request version or required fields")
    value: dict[str, Any] = copy.deepcopy(raw)
    instrument, route = value["instrument"], value["execution_route"]
    if not isinstance(instrument, dict) or set(instrument) != _INSTRUMENT:
        raise ValueError("invalid instrument fields")
    if not isinstance(route, dict) or set(route) != _ROUTE:
        raise ValueError("invalid execution route fields")
    if not isinstance(value["provenance"], dict) or not isinstance(instrument["provenance"], dict):
        raise ValueError("provenance must be an object")
    _validate_instrument(instrument)
    _validate_route(instrument, route)
    value["input_sha256"] = _sha(value)
    return value


def _validate_instrument(item: dict[str, Any]) -> None:
    for field in _INSTRUMENT - {"isin", "provider_symbol"}:
        if field == "provenance":
            continue
        if not isinstance(item[field], str) or not item[field].strip():
            raise ValueError(f"instrument {field} is required")
    if item["isin"] is not None and (not isinstance(item["isin"], str) or not item["isin"].strip()):
        raise ValueError("invalid isin")
    if item["provider_symbol"] is not None and (not isinstance(item["provider_symbol"], str) or not item["provider_symbol"].strip()):
        raise ValueError("invalid provider_symbol")
    if item["instrument_type"] not in _INSTRUMENT_TYPES:
        raise ValueError("unsupported instrument_type")
    try:
        date.fromisoformat(item["metadata_as_of_date"])
    except ValueError as exc:
        raise ValueError("invalid metadata_as_of_date") from exc
    ticker = item["exchange_ticker"].upper()
    if ticker == "4GLD" and (item["instrument_type"] != "PHYSICAL_GOLD_ETC" or item["legal_product_classification"] != "PHYSICAL_GOLD_ETC"):
        raise ValueError("4GLD must be PHYSICAL_GOLD_ETC")
    if ticker == "USO" and "SPOT" in item["security_type"].upper():
        raise ValueError("USO must not be classified as spot oil")
    if ticker == "SMH" and item["instrument_type"] == "COMMON_STOCK":
        raise ValueError("SMH must not be classified as a common stock")


def _validate_route(instrument: dict[str, Any], route: dict[str, Any]) -> None:
    if route["route"] not in _ROUTES:
        raise ValueError("unsupported route")
    for field in ("automation_allowed", "manual_only", "risk_inclusion_required", "automatic_liquidation_allowed", "automatic_order_generation_allowed"):
        if not isinstance(route[field], bool):
            raise ValueError(f"route {field} must be boolean")
    for field in ("expected_holding_horizon", "eligibility_evidence", "eligibility_as_of_date"):
        if not isinstance(route[field], str) or not route[field].strip():
            raise ValueError(f"route {field} is required")
    try:
        date.fromisoformat(route["eligibility_as_of_date"])
    except ValueError as exc:
        raise ValueError("invalid eligibility_as_of_date") from exc
    if route["eligibility_as_of_date"] != instrument["metadata_as_of_date"]:
        raise ValueError("stale or mismatched eligibility evidence")
    if route["route"] == "FIO_MANUAL_LONG_TERM":
        required = {"automation_allowed": False, "manual_only": True, "risk_inclusion_required": True, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False}
        if any(route[key] != expected for key, expected in required.items()):
            raise ValueError("FIO route must be manual-only and risk-included")
    if route["automation_allowed"] and route["route"] != "IBKR_AUTOMATED_ELIGIBLE":
        raise ValueError("automation requires explicit IBKR eligibility")
    if route["route"] == "IBKR_AUTOMATED_ELIGIBLE":
        if not route["automation_allowed"] or route["manual_only"]:
            raise ValueError("IBKR eligible route flags are inconsistent")
        if (instrument["instrument_type"] == "NON_UCITS_ETF" and instrument["domicile"] == "US"
                and "reviewed_us_etf_retail_exception" not in route["eligibility_evidence"].lower()):
            raise ValueError("US ETF is blocked without explicit reviewed exception")
        if instrument["instrument_type"] in {"UCITS_ETF", "PHYSICAL_GOLD_ETC", "COMMODITY_ETC"} and not instrument["kid_status"].startswith("REVIEWED_KID"):
            raise ValueError("retail product requires reviewed KID status")
        if "reviewed" not in route["eligibility_evidence"].lower():
            raise ValueError("explicit reviewed eligibility evidence is required")


def _identity_key(item: dict[str, Any]) -> str:
    return "|".join(str(item[key]) for key in ("isin", "selected_exchange", "exchange_ticker", "trading_currency", "share_class_identity", "distribution_policy", "currency_hedging", "legal_product_classification"))


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
