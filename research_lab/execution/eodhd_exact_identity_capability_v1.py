"""Offline M31K capability manifest for one bounded EODHD Search request."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "eodhd_exact_identity_capability_v1"
ENDPOINT_CLASS = "EODHD_SEARCH_BY_ISIN_EXCHANGE_BOUNDED_V1"
_TICKERS = ("SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR")
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "filesystem_writes_to_private_snapshots": False, "acquisition_authorized": False, "production_runtime_supported": False}


def build_eodhd_exact_identity_capability(m31i_manifest: dict[str, object]) -> dict[str, object]:
    """Compose committed provider references with the exact M31I identity manifest."""
    manifest = _validated_manifest(m31i_manifest)
    artifact = _load_artifact()
    _validate_artifact(artifact)
    mappings = []
    source = artifact["exchange_source"]
    for identity in manifest["preferred_universe"]:
        code, status, provider_type = artifact["mappings"][identity["instrument_id"]]
        evidence = {**source, "instrument_id": identity["instrument_id"], "eodhd_exchange_code": code}
        mapping = {"instrument_id": identity["instrument_id"], "isin": identity["isin"], "selected_mic": identity["mic"], "official_exchange": identity["official_exchange"], "official_exchange_ticker": identity["exchange_ticker"], "trading_currency": identity["trading_currency"], "eodhd_exchange_code": code, "provider_type": provider_type, "official_eodhd_source_evidence": evidence, "evidence_status": source["evidence_status"], "evidence_accessed_date": artifact["accessed_date"], "evidence_sha256": _sha(evidence), "mapping_status": status}
        if identity["instrument_type"] == "PHYSICAL_GOLD_ETC":
            mapping["type_taxonomy_status"] = "PROVIDER_TYPE_TAXONOMY_NOT_EXACT"
        mappings.append(mapping)
    verified = [item["instrument_id"] for item in mappings if item["mapping_status"] == "VERIFIED_PROVIDER_EXCHANGE_CODE"]
    blocked = [item["instrument_id"] for item in mappings if item["mapping_status"] == "BLOCKED_PROVIDER_EXCHANGE_CODE"]
    result: dict[str, Any] = {"version": "eodhd_exact_identity_capability_manifest_result_v1", "contract_version": CONTRACT_VERSION, "capability_manifest_id": "eodhd-exact-identity-capability-2026-07-16-v1", "capability_status": "BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE" if verified else "FAILED_VALIDATION", "provider": "EODHD", "endpoint_class": ENDPOINT_CLASS, "endpoint_template": artifact["search_capability_evidence"]["endpoint_template"], "allowed_parameters": artifact["search_capability_evidence"]["allowed_parameters"], "forbidden_parameters": ["api_token", "cursor", "offset", "page", "retry"], "fixed_result_limit": 10, "response_contract": {"required_candidate_fields": artifact["search_capability_evidence"]["response_fields"], "maximum_records": 10, "exact_match_fields": ["ISIN", "Exchange", "Currency", "Code", "Type"]}, "validation_outcomes": ["EXACT_PROVIDER_IDENTITY_MATCH", "NO_EXACT_MATCH", "AMBIGUOUS_EXACT_MATCH", "PROVIDER_IDENTITY_CONFLICT", "MALFORMED_PROVIDER_RESPONSE", "RESPONSE_LIMIT_EXCEEDED"], "exchange_code_mappings": mappings, "authorized_capability_instruments": verified, "blocked_capability_instruments": blocked, "official_evidence_index": {"search": artifact["search_capability_evidence"], "exchange": source}, "selected_resolution_method": ENDPOINT_CLASS, "documented_alternatives": artifact["documented_alternatives"], "call_cost_evidence": artifact["search_capability_evidence"]["call_cost"], "no_pagination_evidence": {"pagination_allowed": False, "second_page_allowed": False, "fallback_allowed": False, "retry_allowed": False}, "input_sha256": _sha({"m31i_manifest": manifest, "artifact": artifact}), "findings": ["OFFLINE_REVIEW_ONLY", "EXACT_ISIN_REQUIRED", "NO_PROVIDER_CALL_EXECUTED", "NO_PAGINATION", "NO_FALLBACK"], "provenance": {"m31i_source": "M31I_MERGED_OFFICIAL_IDENTITY_MANIFEST_ONLY", "network_used": False, "provider_credentials_accessed": False}, "safety_fields": copy.deepcopy(_SAFETY)}
    result["canonical_capability_manifest_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validated_manifest(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("manifest_status") != "VERIFIED":
        raise ValueError("M31I manifest must be VERIFIED")
    value = copy.deepcopy(raw); supplied = value.pop("canonical_manifest_sha256", None)
    if supplied != _sha(value):
        raise ValueError("M31I canonical manifest hash mismatch")
    items = value.get("preferred_universe")
    if not isinstance(items, list) or [item.get("ticker") for item in items] != list(_TICKERS):
        raise ValueError("exact M31I universe required")
    return raw


def _load_artifact() -> dict[str, Any]:
    return json.loads((Path(__file__).parents[1] / "evidence" / "eodhd_exact_identity_capability_v1.json").read_text(encoding="utf-8"))


def _validate_artifact(value: dict[str, Any]) -> None:
    evidence = value.get("search_capability_evidence", {})
    if evidence.get("authority") != "EODHD_OFFICIAL_DOCUMENTATION" or "isin" not in evidence.get("query_string_support", []):
        raise ValueError("official ISIN Search evidence required")
    if evidence.get("allowed_parameters") != ["exchange", "fmt", "limit", "type"] or set(evidence.get("response_fields", [])) != {"Code", "Exchange", "Name", "Type", "Country", "Currency", "ISIN", "isPrimary"}:
        raise ValueError("strict bounded Search contract required")
    if set(value.get("mappings", {})) != {f"M31I:{ticker}" for ticker in _TICKERS}:
        raise ValueError("exact mapping coverage required")
    if any(item[1] not in {"VERIFIED_PROVIDER_EXCHANGE_CODE", "REVIEW_REQUIRED_PROVIDER_EXCHANGE_CODE", "BLOCKED_PROVIDER_EXCHANGE_CODE"} for item in value["mappings"].values()):
        raise ValueError("invalid mapping status")


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
