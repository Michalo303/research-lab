"""Deterministic M31N provider capability manifest; no provider I/O."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "eodhd_exact_identity_capability_v2"
_TICKERS = ("SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR")
_STATUSES = {"VERIFIED_PROVIDER_EXCHANGE_MAPPING", "REVIEW_REQUIRED_PROVIDER_EXCHANGE_MAPPING", "BLOCKED_PROVIDER_EXCHANGE_MAPPING"}
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "filesystem_writes_performed": False, "historical_data_requested": False, "corporate_actions_requested": False, "calendar_data_requested": False, "production_runtime_supported": False}


def build_eodhd_exact_identity_capability_v2(m31i_manifest: dict[str, object]) -> dict[str, object]:
    """Build compact, auditable mappings from the exact merged M31I manifest."""
    manifest = _validated_m31i(m31i_manifest)
    artifact = _artifact()
    _validate_artifact(artifact)
    search_evidence = copy.deepcopy(artifact["search_capability_evidence"])
    exchange_evidence = copy.deepcopy(artifact["exchange_evidence"])
    mappings: list[dict[str, Any]] = []
    for identity in manifest["preferred_universe"]:
        ticker = identity["ticker"]
        code, exchange_name, operating_mics, namespace, request_type, response_types, taxonomy = artifact["mappings"][ticker]
        membership = "SELECTED_MIC_CONTAINED_IN_PROVIDER_OPERATING_MICS" if identity["mic"] in operating_mics else "SELECTED_MIC_NOT_CONTAINED_IN_PROVIDER_OPERATING_MICS"
        status = "VERIFIED_PROVIDER_EXCHANGE_MAPPING" if membership == "SELECTED_MIC_CONTAINED_IN_PROVIDER_OPERATING_MICS" else "BLOCKED_PROVIDER_EXCHANGE_MAPPING"
        exchange_record = {**exchange_evidence, "provider_exchange_code": code, "provider_exchange_name": exchange_name, "provider_operating_mics": operating_mics}
        mapping = {"instrument_id": identity["instrument_id"], "ticker_label": ticker, "legal_name": identity["legal_name"], "legal_product_type": identity["instrument_type"], "isin": identity["isin"], "selected_mic": identity["mic"], "official_exchange": identity["official_exchange"], "exchange_ticker": identity["exchange_ticker"], "currency": identity["trading_currency"], "provider_exchange_code": code, "provider_exchange_name": exchange_name, "provider_operating_mics": operating_mics, "provider_namespace_classification": namespace, "selected_mic_membership_status": membership, "search_type_parameter": request_type, "accepted_response_types": response_types, "type_taxonomy_status": taxonomy, "official_capability_evidence_sha256": _sha(search_evidence), "official_exchange_evidence": exchange_record, "official_exchange_evidence_sha256": _sha(exchange_record), "mapping_status": status, "findings": ["M31I_SELECTED_MIC_BOUND", "OFFICIAL_EODHD_EVIDENCE_COMPACT_RECORD", "NO_PROVIDER_CALL_EXECUTED"]}
        mapping["canonical_mapping_sha256"] = _sha(mapping)
        mappings.append(mapping)
    verified = [item["instrument_id"] for item in mappings if item["mapping_status"] == "VERIFIED_PROVIDER_EXCHANGE_MAPPING"]
    result: dict[str, Any] = {"version": "eodhd_exact_identity_capability_manifest_result_v2", "contract_version": CONTRACT_VERSION, "capability_manifest_id": "eodhd-exact-identity-capability-2026-07-17-v2", "capability_status": "BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE_V2" if verified else "FAILED_VALIDATION", "provider": "EODHD", "m31i_canonical_manifest_sha256": manifest["canonical_manifest_sha256"], "official_capability_evidence": search_evidence, "exchange_code_mappings": mappings, "authorized_capability_instruments": verified, "blocked_capability_instruments": [item["instrument_id"] for item in mappings if item["mapping_status"] == "BLOCKED_PROVIDER_EXCHANGE_MAPPING"], "findings": ["OFFLINE_REVIEW_ONLY", "EXACT_ISIN_REQUIRED", "REQUEST_AND_RESPONSE_TYPE_TAXONOMIES_SEPARATED"], "safety_fields": copy.deepcopy(_SAFETY)}
    result["canonical_capability_manifest_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validated_m31i(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("manifest_status") != "VERIFIED":
        raise ValueError("M31I manifest must be VERIFIED")
    value = copy.deepcopy(raw); supplied = value.pop("canonical_manifest_sha256", None)
    if supplied != _sha(value):
        raise ValueError("M31I canonical manifest hash mismatch")
    if [item.get("ticker") for item in value.get("preferred_universe", [])] != list(_TICKERS):
        raise ValueError("exact M31I universe required")
    return raw


def _artifact() -> dict[str, Any]:
    return json.loads((Path(__file__).parents[1] / "evidence" / "eodhd_exact_identity_capability_v2.json").read_text(encoding="utf-8"))


def _validate_artifact(value: dict[str, Any]) -> None:
    search = value.get("search_capability_evidence", {})
    if search.get("endpoint_template") != "/api/search/{query_string}" or search.get("allowed_parameters") != ["exchange", "fmt", "limit", "type"] or "isin" not in search.get("query_string_support", []) or not search.get("response_is_json_list"):
        raise ValueError("official bounded Search evidence required")
    if set(search.get("documented_type_parameters", [])) != {"all", "stock", "etf", "fund", "bond", "index", "crypto"}:
        raise ValueError("Search type taxonomy evidence required")
    mappings = value.get("mappings", {})
    if tuple(mappings) != _TICKERS:
        raise ValueError("exact mapping coverage required")
    for ticker, mapping in mappings.items():
        if not isinstance(mapping, list) or len(mapping) != 7 or mapping[4] not in {"all", "stock", "etf"} or not all(isinstance(value, str) and value for value in mapping[5]) or mapping[6] not in {"EXACT_PROVIDER_TYPE_TAXONOMY", "PROVIDER_TYPE_TAXONOMY_NOT_EXACT", "BLOCKED_PROVIDER_TYPE_TAXONOMY"}:
            raise ValueError(f"invalid mapping for {ticker}")


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
