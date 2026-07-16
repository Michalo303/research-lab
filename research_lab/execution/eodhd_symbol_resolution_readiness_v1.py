"""Deterministic, review-only M31J EODHD symbol-resolution readiness."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

CONTRACT_VERSION = "eodhd_symbol_resolution_readiness_v1"
_TICKERS = ("SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR")
_SUPERSEDED = "c2cad14d2c41a718fe8c5095ee0342f4bdd85127418a53ad20347089d8d77ef9"
_REQUEST_FIELDS = {"version", "readiness_request_id", "m31i_manifest", "expected_m31i_canonical_manifest_sha256", "provider_policy", "endpoint_policy", "private_destination_root", "call_budget_policy", "approval_policy", "provenance"}
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "IBKR_actions_performed": False, "filesystem_writes_performed": False, "private_snapshot_mutations_performed": False, "SPY_refetch_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "executable_orders_generated": False, "automatic_capital_allocation_performed": False, "deployment_performed": False, "service_restart_performed": False, "registry_write_performed": False, "provider_acquisition_authorized": False, "production_runtime_supported": False}


def build_eodhd_symbol_resolution_readiness(request: dict[str, object]) -> dict[str, object]:
    """Prepare no calls: the repository has no bounded exact-identity metadata endpoint."""
    value = _validate_request(request)
    manifest = value["m31i_manifest"]
    identities = copy.deepcopy(manifest["preferred_universe"])
    blocked = [{"sequence": index, "instrument_id": item["instrument_id"], "ticker": item["ticker"], "legal_name": item["legal_name"], "isin": item["isin"], "mic": item["mic"], "official_exchange": item["official_exchange"], "exchange_ticker": item["exchange_ticker"], "trading_currency": item["trading_currency"], "provider": "EODHD", "endpoint_class": "NOT_AVAILABLE_BOUNDED_EXACT_IDENTITY", "provider_symbol_status": "NOT_AUTHORIZED_UNBOUNDED_RESOLUTION", "call_count": 0, "retry_count": 0, "findings": ["NO_BOUNDED_EXACT_METADATA_ENDPOINT_IN_EXISTING_ADAPTER", "NO_TICKER_ONLY_SEARCH", "NOT_EXECUTED"]} for index, item in enumerate(identities, 1)]
    budgets = {"metadata_calls_max": 0, "historical_calls_max": 0, "corporate_action_calls_max": 0, "calendar_calls_max": 0, "total_calls_max": 0, "retries": 0, "sequential_only": True, "stop_on_first_failure": True, "fallback_provider_allowed": False, "health_check_calls": 0, "hidden_calls": 0}
    approval = {"version": "eodhd_symbol_resolution_approval_manifest_v1", "purpose": "EODHD_SYMBOL_RESOLUTION_ONLY", "official_identity_manifest_sha256": manifest["canonical_manifest_sha256"], "authorized_instruments": [], "metadata_calls_max": 0, "historical_calls_max": 0, "corporate_action_calls_max": 0, "calendar_calls_max": 0, "total_calls_max": 0, "retries": 0, "sequential_only": True, "stop_on_first_failure": True, "fallback_provider_allowed": False, "health_check_calls": 0, "hidden_calls": 0, "SPY_REFETCH_NOT_AUTHORIZED": True, "superseded_approval_hashes": [_SUPERSEDED], "safety_fields": copy.deepcopy(_SAFETY)}
    result: dict[str, Any] = {"version": "eodhd_symbol_resolution_readiness_result_v1", "contract_version": CONTRACT_VERSION, "readiness_request_id": value["readiness_request_id"], "status": "REVIEW_REQUIRED", "verified_m31i_manifest_sha256": manifest["canonical_manifest_sha256"], "exact_instrument_identities": identities, "authorized_metadata_resolution_plan": [], "blocked_resolution_items": blocked, "unresolved_instruments": [item["instrument_id"] for item in identities], "call_budgets": budgets, "deterministic_destinations": [], "result_validation_contracts": [], "spy_unchanged_evidence": {"status": "SPY_REFETCH_NOT_AUTHORIZED", "provider_call_required": False}, "superseded_approval_hashes": [_SUPERSEDED], "approval_manifest": approval, "findings": ["NO_PROVIDER_CALLS_EXECUTED", "REVIEW_REQUIRED_UNBOUNDED_ENDPOINT", "SPY_REFETCH_NOT_AUTHORIZED"], "provenance": {"universe_source": "M31I_MERGED_OFFICIAL_IDENTITY_MANIFEST_ONLY", "existing_adapter_metadata_endpoint": "NONE"}, "safety_fields": copy.deepcopy(_SAFETY)}
    result["acquisition_plan_sha256"] = _sha(result["authorized_metadata_resolution_plan"])
    approval["canonical_approval_manifest_sha256"] = _sha(approval)
    result["approval_manifest_sha256"] = approval["canonical_approval_manifest_sha256"]
    return copy.deepcopy(result)


def _validate_request(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS: raise ValueError("unknown or missing readiness request fields")
    value = copy.deepcopy(raw)
    if value["version"] != "eodhd_symbol_resolution_readiness_request_v1": raise ValueError("invalid version")
    manifest = value["m31i_manifest"]
    if not isinstance(manifest, dict) or manifest.get("manifest_status") != "VERIFIED": raise ValueError("M31I manifest must be VERIFIED")
    supplied = manifest.get("canonical_manifest_sha256")
    hashable = copy.deepcopy(manifest); hashable.pop("canonical_manifest_sha256", None)
    if supplied != _sha(hashable) or value["expected_m31i_canonical_manifest_sha256"] != supplied: raise ValueError("M31I canonical manifest hash mismatch")
    items = manifest.get("preferred_universe")
    if not isinstance(items, list) or [item.get("ticker") for item in items] != list(_TICKERS) or len({item.get("instrument_id") for item in items}) != 15: raise ValueError("exact M31I universe required")
    for item in items:
        if not all(isinstance(item.get(field), str) and item[field] for field in ("instrument_id", "legal_name", "isin", "mic", "official_exchange", "exchange_ticker", "trading_currency", "instrument_type")): raise ValueError("invalid M31I identity")
        evidence = item.get("official_evidence")
        if not isinstance(evidence, list) or len(evidence) != 1 or not evidence[0].get("evidence_sha256"): raise ValueError("unsupported identity evidence")
    if value["private_destination_root"] != "/opt/trading/private/research_market_data_snapshots/pending_symbol_resolution_v1/": raise ValueError("unsafe destination root")
    return value


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
