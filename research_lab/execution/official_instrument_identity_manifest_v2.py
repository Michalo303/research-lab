"""Deterministic, offline M31I official instrument identity manifest."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "official_instrument_identity_manifest_v2"
_TICKERS = ("SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR")
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "data_acquisition_authorized": False, "broker_execution_authorized": False, "Fio_actions_performed": False, "IBKR_actions_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "executable_orders_generated": False, "automatic_liquidation_performed": False, "automatic_capital_allocation_performed": False, "filesystem_writes_to_private_snapshots": False, "deployment_performed": False, "service_restart_performed": False, "registry_write_performed": False, "production_runtime_supported": False}
_REQUIRED = {"instrument_id", "ticker", "legal_name", "issuer", "instrument_type", "security_type", "legal_structure", "isin", "mic", "official_exchange", "exchange_ticker", "trading_currency", "domicile", "listing_date", "inception_date", "listing_status", "share_class", "ucits_status", "evidence_id"}
_CRITICAL_IDENTITY_FIELDS = _REQUIRED - {"instrument_id", "evidence_id"}


def build_official_instrument_identity_manifest() -> dict[str, object]:
    """Build the committed-evidence manifest without network, credential, or provider I/O."""
    artifact = _load_artifact()
    items = copy.deepcopy(artifact["instruments"])
    review_required = _validate(items, artifact["evidence"])
    evidence = {record["evidence_id"]: {**record, "evidence_sha256": _sha({key: value for key, value in record.items() if key != "evidence_sha256"})} for record in artifact["evidence"]}
    preferred = [{**item, "official_evidence": [copy.deepcopy(evidence[item["evidence_id"]])], "provider_symbol_status": "REQUIRES_EODHD_SYMBOL_RESOLUTION", "proposed_provider_symbol": None} for item in items]
    result: dict[str, Any] = {"version": "official_instrument_identity_manifest_result_v2", "contract_version": CONTRACT_VERSION, "manifest_id": "official-instrument-identity-manifest-2026-07-16-v2", "manifest_status": "REVIEW_REQUIRED" if review_required else "VERIFIED", "metadata_as_of_date": "2026-07-16", "preferred_universe": preferred, "backup_or_rejected_listings": [], "official_evidence_index": evidence, "legal_product_analysis": [{"instrument_id": item["instrument_id"], "instrument_type": item["instrument_type"], "ucits_status": item["ucits_status"]} for item in preferred], "listing_identity_analysis": [{"instrument_id": item["instrument_id"], "identity_key": _identity_key(item)} for item in preferred], "currency_analysis": [{"instrument_id": item["instrument_id"], "trading_currency": item["trading_currency"]} for item in preferred], "ucits_kid_analysis": [{"instrument_id": item["instrument_id"], "ucits_status": item["ucits_status"], "kid_status": item["kid_status"]} for item in preferred], "corporate_action_considerations": [{"instrument_id": item["instrument_id"], "consideration": item["corporate_action_consideration"]} for item in preferred], "provider_resolution_inputs": [{key: item[key] for key in ("instrument_id", "legal_name", "isin", "mic", "official_exchange", "exchange_ticker", "trading_currency", "provider_symbol_status")} for item in preferred], "mappings": [{"mapping_id": "M31I:QQQ_TO_EQQQ", "mapping_type": "ECONOMIC_PROXY"}, {"mapping_id": "M31I:GLD_TO_4GLD", "mapping_type": "RELATED_EXPOSURE_NOT_IDENTICAL"}, {"mapping_id": "M31I:USO_TO_OIL_STOCK", "mapping_type": "RELATED_EXPOSURE_NOT_IDENTICAL"}], "routing": [{"instrument_id": item["instrument_id"], "execution_route": item["execution_route"], "automatic_execution": False} for item in preferred], "unresolved_non_critical_items": ["EODHD_SYMBOL_RESOLUTION_REQUIRED"] + (["STALE_KID_EVIDENCE"] if review_required else []), "rejected_items": [], "findings": ["OFFICIAL_LISTING_IDENTITY_VERIFIED", "NO_PROVIDER_CALLS_EXECUTED", "PROVIDER_SYMBOL_RESOLUTION_REQUIRED"], "provenance": {"evidence_artifact": "research_lab/evidence/official_instrument_identity_manifest_v2.json", "network_used": False, "historical_m31g_used_as_source": False}, "safety_fields": copy.deepcopy(_SAFETY)}
    result["input_sha256"] = _sha(artifact)
    result["canonical_manifest_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _load_artifact() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "evidence" / "official_instrument_identity_manifest_v2.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(items: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> bool:
    if not isinstance(items, list) or [item.get("ticker") for item in items] != list(_TICKERS): raise ValueError("exact preferred universe is required")
    if len({_identity_key(item) for item in items}) != len(items): raise ValueError("duplicate selected identity")
    index = {record.get("evidence_id"): record for record in evidence}
    review_required = False
    for item in items:
        if set(item) != _REQUIRED | {"kid_status", "distribution_policy", "currency_hedging", "benchmark", "replication_method", "ongoing_charge", "corporate_action_consideration", "execution_route"}: raise ValueError("invalid instrument fields")
        if any(not isinstance(item[field], str) or not item[field] for field in _REQUIRED - {"listing_date", "inception_date"}): raise ValueError("missing exact identity field")
        record = index.get(item["evidence_id"])
        if not isinstance(record, dict) or record.get("authority_type") not in {"OFFICIAL_ISSUER", "OFFICIAL_EXCHANGE", "OFFICIAL_REGULATOR"}: raise ValueError("missing official source")
        if record.get("evidence_status") == "SECONDARY_ONLY": raise ValueError("secondary-only evidence is not permitted")
        if not isinstance(record.get("official_url"), str) or not record["official_url"].startswith("https://"): raise ValueError("malformed official URL")
        if not set(_REQUIRED - {"evidence_id"}).issubset(set(record.get("supported_fields", []))): raise ValueError("evidence does not support asserted identity fields")
        field_evidence = record.get("field_evidence")
        if not isinstance(field_evidence, dict) or set(field_evidence) != _CRITICAL_IDENTITY_FIELDS:
            raise ValueError("field-level evidence must cover every critical identity field")
        if any(field_evidence[field] != item[field] for field in _CRITICAL_IDENTITY_FIELDS):
            raise ValueError("field-level evidence must bind asserted identity values")
        review_required = review_required or (item["instrument_type"] == "UCITS_ETF" and record.get("evidence_status") != "CURRENT")
    by_ticker = {item["ticker"]: item for item in items}
    if by_ticker["4GLD"]["instrument_type"] != "PHYSICAL_GOLD_ETC" or by_ticker["4GLD"]["ucits_status"] != "NOT_UCITS_EXPLICIT_EXCEPTION": raise ValueError("4GLD invariant")
    if "FUTURES" not in by_ticker["USO"]["legal_structure"] or by_ticker["USO"]["instrument_type"] == "COMMON_STOCK": raise ValueError("USO invariant")
    if by_ticker["SMH"]["instrument_type"] != "NON_UCITS_ETF": raise ValueError("SMH invariant")
    if any(item["instrument_type"] == "COMMON_STOCK" and item["security_type"] != "ORDINARY_COMMON_SHARE" for item in items): raise ValueError("ADR or share-class ambiguity")
    return review_required


def _identity_key(item: dict[str, Any]) -> str:
    return "|".join(item[key] for key in ("isin", "mic", "exchange_ticker", "trading_currency", "share_class"))


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
