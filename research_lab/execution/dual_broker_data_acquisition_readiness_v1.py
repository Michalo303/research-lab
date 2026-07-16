"""Deterministic M31H data-acquisition readiness and human approval gate."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

REQUEST_VERSION = "dual_broker_data_acquisition_readiness_request_v1"
CONTRACT_VERSION = "dual_broker_data_acquisition_readiness_v1"
_SPY = {"instrument": "SPY.US", "dataset_id": "eodhd-spy-us-daily-2015-2026-v1", "start_date": "2015-01-01", "end_date": "2026-06-30", "normalized_canonical_sha256": "cbe71c7e501407137f41d708d8fc72018c8a864b2ea4fcb0beb9c37ca8f8c00e", "source_sha256": "d3002fdaf8e4f2ea6c091bb10a4e54ac7e1056b2cec24457c388adae7eeab3d2", "rows_sha256": "b394795204207ffdfd27dfb85a628514b1b32f9877951f2b3a3e22138a3730f2", "status": "REUSE_EXISTING_SNAPSHOT", "no_refetch": True}
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "IBKR_actions_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "executable_orders_generated": False, "automatic_liquidation_performed": False, "automatic_capital_allocation_performed": False, "deployment_performed": False, "service_restart_performed": False, "registry_write_performed": False, "production_runtime_supported": False, "data_acquisition_authorized": False, "broker_execution_authorized": False}


def build_dual_broker_data_acquisition_readiness(request: dict[str, object]) -> dict[str, object]:
    manifest = _validate(request)
    plan = []
    for sequence, item in enumerate(manifest["preferred_universe"], start=1):
        for call_class, endpoint_class, call_count in (("SYMBOL_OR_METADATA_RESOLUTION", "EODHD_SYMBOL_METADATA", 1), ("HISTORICAL_DAILY_OHLCV", "EODHD_HISTORICAL_DAILY", 1), ("CORPORATE_ACTIONS", "EODHD_CORPORATE_ACTIONS", 1), ("NOT_AUTHORIZED", "EXCHANGE_CALENDAR_UNBOUNDED", 0)):
            plan.append({"sequence": len(plan) + 1, "instrument_sequence": sequence, "instrument_id": item["instrument_id"], "exact_listing": item["exchange"], "isin": item["isin"], "provider_symbol_status": item["provider_symbol_status"], "proposed_provider_symbol": "REQUIRES_EODHD_SYMBOL_RESOLUTION", "call_class": call_class, "endpoint_class": endpoint_class, "start_date": "2015-01-01", "end_date": "2026-06-30", "interval": "1d" if call_class == "HISTORICAL_DAILY_OHLCV" else "NOT_APPLICABLE", "call_count": call_count, "retry_count": 0, "dependencies": [manifest["canonical_manifest_sha256"]], "dataset_id": f"future-eodhd-{item['ticker'].lower()}-daily-v1", "future_private_destination": f"/opt/trading/private/research_market_data_snapshots/pending_m31h/{item['ticker'].lower()}_v1/", "validation_contract": "HUMAN_APPROVAL_AND_HASHED_IDENTITY_REQUIRED", "provenance": "M31G_MERGED_MANIFEST_ONLY", "findings": ["NOT_EXECUTED", "HUMAN_APPROVAL_REQUIRED"] if call_count else ["NOT_AUTHORIZED", "UNBOUNDED_EXCHANGE_CALENDAR"]})
    budgets = {"metadata_calls_max": len(manifest["preferred_universe"]), "historical_calls_max": len(manifest["preferred_universe"]), "corporate_action_calls_max": len(manifest["preferred_universe"]), "calendar_calls_max": 0, "total_calls_max": sum(entry["call_count"] for entry in plan)}
    approval = {"manifest_sha256": manifest["canonical_manifest_sha256"], "plans": plan, "call_budgets": budgets, "retries": 0, "sequential_only": True, "stop_on_first_failure": True, "fallback_provider_allowed": False, "health_check_calls": 0, "hidden_calls": 0, "snapshots_reused": [_SPY], "safety_fields": _SAFETY}
    result: dict[str, Any] = {"version": "dual_broker_data_acquisition_readiness_result_v1", "contract_version": CONTRACT_VERSION, "status": "HUMAN_APPROVAL_REQUIRED_FOR_DATA_ACQUISITION", "verified_m31g_manifest_sha256": manifest["canonical_manifest_sha256"], "acquisition_plan": plan, "acquisition_plan_sha256": _sha(plan), "snapshots_reused": [copy.deepcopy(_SPY)], "unresolved_symbols": [item["instrument_id"] for item in manifest["preferred_universe"]], "blocked_instruments": [], "call_budgets": budgets, "destinations": [entry["future_private_destination"] for entry in plan], "validation_contracts": [entry["validation_contract"] for entry in plan], "approval_manifest": approval, "approval_manifest_sha256": _sha(approval), "findings": ["NO_PROVIDER_CALLS_EXECUTED", "SPY_REUSE_ONLY", "HUMAN_APPROVAL_REQUIRED"], "provenance": {"universe_source": "M31G_MERGED_MANIFEST_ONLY"}, "safety_fields": copy.deepcopy(_SAFETY)}
    return copy.deepcopy(result)


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"version", "m31g_manifest"} or raw.get("version") != REQUEST_VERSION:
        raise ValueError("invalid readiness request")
    manifest = copy.deepcopy(raw["m31g_manifest"])
    if not isinstance(manifest, dict) or not isinstance(manifest.get("preferred_universe"), list) or not manifest["preferred_universe"]:
        raise ValueError("invalid m31g manifest")
    supplied = manifest.pop("canonical_manifest_sha256", None)
    manifest.pop("output_payload_sha256", None)
    if not isinstance(supplied, str) or _sha(manifest) != supplied:
        raise ValueError("m31g manifest hash mismatch")
    manifest["canonical_manifest_sha256"] = supplied
    return manifest


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
