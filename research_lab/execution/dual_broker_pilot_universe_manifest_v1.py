"""Deterministic, review-only M31G pilot universe manifest."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from typing import Any

REQUEST_VERSION = "dual_broker_pilot_universe_manifest_request_v1"
CONTRACT_VERSION = "dual_broker_pilot_universe_manifest_v1"
_FIELDS = {"version", "manifest_id", "metadata_date", "official_evidence", "provenance"}
_TICKERS = ("SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR")
_PROVIDER = "REQUIRES_EODHD_SYMBOL_RESOLUTION"
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "IBKR_actions_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "executable_orders_generated": False, "automatic_liquidation_performed": False, "automatic_capital_allocation_performed": False, "deployment_performed": False, "service_restart_performed": False, "registry_write_performed": False, "production_runtime_supported": False, "data_acquisition_authorized": False, "broker_execution_authorized": False}


def build_dual_broker_pilot_universe_manifest(request: dict[str, object]) -> dict[str, object]:
    value = _validate(request)
    fio = [_instrument("SMH", "VanEck Semiconductor ETF", "US", "USD", "ETF", "NON_UCITS", "FIO_MANUAL_LONG_TERM", "US_LISTED_SEMICONDUCTOR_ETF", value), _instrument("USO", "United States Oil Fund", "US", "USD", "ETF", "NON_UCITS", "FIO_MANUAL_LONG_TERM", "FUTURES_BASED_CRUDE_OIL_EXPOSURE", value)]
    etfs = [_instrument("VWCE", "Vanguard FTSE All-World UCITS ETF", "IE", "EUR", "UCITS_ETF", "UCITS", "IBKR_REVIEW_REQUIRED", "GLOBAL_EQUITY", value), _instrument("EQQQ", "Invesco EQQQ NASDAQ-100 UCITS ETF", "IE", "GBP", "UCITS_ETF", "UCITS", "IBKR_REVIEW_REQUIRED", "NASDAQ_TECHNOLOGY", value), _instrument("EIMI", "iShares Core MSCI Emerging Markets IMI UCITS ETF", "IE", "USD", "UCITS_ETF", "UCITS", "IBKR_REVIEW_REQUIRED", "EMERGING_MARKETS", value), _instrument("IEAC", "iShares Core EUR Corporate Bond UCITS ETF", "IE", "EUR", "UCITS_ETF", "UCITS", "IBKR_REVIEW_REQUIRED", "HIGH_QUALITY_BONDS", value), _instrument("4GLD", "Xetra-Gold", "DE", "EUR", "PHYSICAL_GOLD_ETC", "NOT_UCITS_EXPLICIT_EXCEPTION", "IBKR_REVIEW_REQUIRED", "PHYSICAL_GOLD", value)]
    stocks = [_instrument(t, n, d, c, "COMMON_STOCK", "NOT_APPLICABLE", "IBKR_REVIEW_REQUIRED", s, value) for t, n, d, c, s in [("MSFT", "Microsoft Corporation", "US", "USD", "SOFTWARE"), ("JNJ", "Johnson & Johnson", "US", "USD", "HEALTHCARE"), ("XOM", "Exxon Mobil Corporation", "US", "USD", "ENERGY"), ("ASML", "ASML Holding N.V.", "NL", "EUR", "SEMICONDUCTORS"), ("SAP", "SAP SE", "DE", "EUR", "ENTERPRISE_SOFTWARE"), ("NESN", "Nestle S.A.", "CH", "CHF", "CONSUMER_STAPLES"), ("NOVO-B", "Novo Nordisk A/S", "DK", "DKK", "HEALTHCARE"), ("AIR", "Airbus SE", "NL", "EUR", "AEROSPACE")]]
    mappings = [{"mapping_id": "QQQ_TO_EU_NASDAQ", "mapping_type": "ECONOMIC_PROXY"}, {"mapping_id": "GLD_TO_4GLD", "mapping_type": "RELATED_EXPOSURE_NOT_IDENTICAL"}, {"mapping_id": "USO_TO_OIL_PRODUCER", "mapping_type": "RELATED_EXPOSURE_NOT_IDENTICAL"}]
    preferred = fio + etfs + stocks
    result: dict[str, Any] = {"version": "dual_broker_pilot_universe_manifest_result_v1", "contract_version": CONTRACT_VERSION, "manifest_id": value["manifest_id"], "manifest_version": CONTRACT_VERSION, "metadata_date": value["metadata_date"], "manifest_status": "REVIEW_REQUIRED", "preferred_universe": preferred, "fio_manual_long_term": fio, "ibkr_etf_etc_swing": etfs, "ibkr_common_stock_swing": stocks, "backups": [], "rejected_candidates": [], "overlap_analysis": [{"scope": "FIO_AND_IBKR", "status": "AGGREGATE_RISK_REVIEW_REQUIRED", "fio_manual_only": ["SMH", "USO"]}], "legal_product_analysis": [{"ticker": item["ticker"], "instrument_type": item["instrument_type"], "ucits_status": item["ucits_status"]} for item in etfs], "sector_asset_class_currency_analysis": [{"ticker": item["ticker"], "research_role": item["research_role"], "currency": item["currency"]} for item in preferred], "corporate_action_and_delisting_analysis": [{"ticker": item["ticker"], "status": "OFFICIAL_EVIDENCE_REVIEW_REQUIRED"} for item in preferred], "proposed_mappings": mappings, "unresolved_retail_eligibility": [item["instrument_id"] for item in etfs + stocks], "unresolved_provider_symbols": [item["instrument_id"] for item in preferred], "official_evidence_index": {ticker: value["official_evidence"][ticker] for ticker in _TICKERS}, "findings": ["ELIGIBILITY_REQUIRES_M31D_EVIDENCE", "PROVIDER_SYMBOL_RESOLUTION_REQUIRED", "NO_AUTOMATIC_EXECUTION_OR_ACQUISITION"], "provenance": copy.deepcopy(value["provenance"]), "input_sha256": value["input_sha256"], "safety_fields": copy.deepcopy(_SAFETY)}
    result["canonical_manifest_sha256"] = _sha(result)
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _instrument(ticker: str, legal_name: str, domicile: str, currency: str, typ: str, ucits: str, route: str, role: str, value: dict[str, Any]) -> dict[str, object]:
    evidence_key = ticker
    return {"instrument_id": f"M31G:{ticker}", "ticker": ticker, "legal_name": legal_name, "issuer": "REVIEW_REQUIRED", "instrument_type": typ, "legal_product_type": typ, "isin": "REQUIRES_OFFICIAL_CONFIRMATION", "exchange": "REQUIRES_OFFICIAL_CONFIRMATION", "currency": currency, "vehicle_jurisdiction": domicile, "domicile": domicile, "ucits_status": ucits, "kid_status": "REQUIRES_CURRENT_KID_EVIDENCE" if typ != "COMMON_STOCK" else "NOT_REQUIRED_COMMON_STOCK", "distribution": "REQUIRES_OFFICIAL_CONFIRMATION", "hedging": "REQUIRES_OFFICIAL_CONFIRMATION", "benchmark": "REQUIRES_OFFICIAL_CONFIRMATION", "replication": "REQUIRES_OFFICIAL_CONFIRMATION", "inception_or_listing_date": "REQUIRES_OFFICIAL_CONFIRMATION", "official_cost_evidence": "REQUIRES_OFFICIAL_CONFIRMATION", "corporate_action_and_delisting_considerations": "REVIEW_REQUIRED", "proposed_ibkr_route": route, "retail_eligibility_status": "ELIGIBILITY_REQUIRES_M31D_EVIDENCE", "research_role": role, "m31b_mapping_type": "NO_EXECUTION_MAPPING" if route.startswith("FIO") else "SAME_INSTRUMENT_SAME_LISTING", "provider_symbol_status": _PROVIDER, "unresolved_issues": ["OFFICIAL_LISTING_CONFIRMATION_REQUIRED"], "official_evidence": value["official_evidence"][evidence_key], "manual_only": route.startswith("FIO"), "aggregate_risk_inclusion_required": route.startswith("FIO"), "automatic_action_allowed": False}


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _FIELDS or raw.get("version") != REQUEST_VERSION: raise ValueError("invalid manifest request")
    value = copy.deepcopy(raw)
    if not isinstance(value["manifest_id"], str) or not value["manifest_id"]: raise ValueError("invalid manifest_id")
    try: date.fromisoformat(value["metadata_date"])
    except Exception as exc: raise ValueError("invalid metadata_date") from exc
    if not isinstance(value["official_evidence"], dict) or not all(isinstance(value["official_evidence"].get(key), str) and value["official_evidence"][key] for key in _TICKERS): raise ValueError("required official evidence is missing")
    if not isinstance(value["provenance"], dict): raise ValueError("invalid provenance")
    value["input_sha256"] = _sha(value); return value


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
