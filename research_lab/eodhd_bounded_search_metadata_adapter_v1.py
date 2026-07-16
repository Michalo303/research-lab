"""Strict, injected-client adapter for one bounded EODHD Search metadata request."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Callable

CONTRACT_VERSION = "eodhd_bounded_search_metadata_adapter_v1"
_REQUIRED = {"version", "request_id", "m31i_manifest", "m31i_identity", "expected_m31i_manifest_sha256", "m31k_capability_manifest", "expected_m31k_capability_sha256", "provider_exchange_mapping", "endpoint_policy", "response_limit_policy", "call_budget_policy", "allow_provider_calls", "provenance"}
_OPTIONAL = {"external_human_approval_hash"}
_FIELDS = {"Code", "Exchange", "Name", "Type", "Country", "Currency", "ISIN"}
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "retries_used": 0, "fallback_used": False, "filesystem_writes_performed": False, "historical_data_requested": False, "corporate_actions_requested": False, "production_runtime_supported": False}


def resolve_bounded_eodhd_search(request: dict[str, object], client: Callable[[str, dict[str, object], object], object] | None = None, credentials: object | None = None) -> dict[str, object]:
    """Validate and optionally execute exactly one pre-approved bounded Search request."""
    value = _validate_request(request)
    identity = value["m31i_identity"]; mapping = value["provider_exchange_mapping"]
    plan = {"path": f"/api/search/{identity['isin']}", "parameters": {"exchange": mapping["eodhd_exchange_code"], "fmt": "json", "limit": 10, "type": mapping["provider_type"]}}
    output: dict[str, Any] = {"version": "eodhd_bounded_search_metadata_result_v1", "contract_version": CONTRACT_VERSION, "request_id": value["request_id"], "provider": "EODHD", "endpoint_class": value["endpoint_policy"], "redacted_request_plan": plan, "provider_call_count": 0, "call_budget_status": "NOT_CONSUMED", "m31i_identity_sha256": _sha(identity), "m31k_capability_sha256": value["expected_m31k_capability_sha256"], "candidate_count": 0, "exact_match_count": 0, "selected_candidate": None, "resolved_provider_symbol": None, "review_findings": ["NO_TOKEN_IN_REQUEST_PLAN", "NO_PAGINATION", "NO_RETRY", "NO_FALLBACK"], "input_sha256": _sha(value), "provenance": value["provenance"], "safety_fields": copy.deepcopy(_SAFETY)}
    if not value["allow_provider_calls"]:
        output["resolution_status"] = "DRY_RUN_PROVIDER_CALL_NOT_AUTHORIZED"
        output["review_findings"].append("PROVIDER_CALL_NOT_AUTHORIZED")
        return _finish(output)
    approval = value.get("external_human_approval_hash")
    approval_payload = copy.deepcopy(value); approval_payload.pop("external_human_approval_hash", None)
    if not isinstance(approval, str) or approval != _sha(approval_payload):
        raise ValueError("exact external human approval hash required")
    if credentials is None or client is None:
        raise ValueError("injected credentials and client required")
    response = client(plan["path"], copy.deepcopy(plan["parameters"]), credentials)
    output["provider_call_count"] = 1; output["call_budget_status"] = "CONSUMED_EXACTLY_ONCE"; output["safety_fields"]["provider_calls_used"] = 1
    parsed = _parse(response, identity, mapping, 10)
    output.update(parsed)
    return _finish(output)


def _validate_request(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - _OPTIONAL != _REQUIRED:
        raise ValueError("unknown or missing adapter request fields")
    value = copy.deepcopy(raw)
    if value["version"] != "eodhd_bounded_search_metadata_request_v1" or value["endpoint_policy"] != "EODHD_SEARCH_BY_ISIN_EXCHANGE_BOUNDED_V1" or value["response_limit_policy"] != 10:
        raise ValueError("invalid bounded endpoint policy")
    budget = value["call_budget_policy"]
    if budget != {"max_provider_calls": 1, "consumed_provider_calls": 0}:
        raise ValueError("exact one-call unconsumed budget required")
    m31i = value["m31i_manifest"]
    if not isinstance(m31i, dict):
        raise ValueError("M31I manifest required")
    m31i_hashable = copy.deepcopy(m31i); supplied_m31i = m31i_hashable.pop("canonical_manifest_sha256", None)
    if supplied_m31i != _sha(m31i_hashable) or supplied_m31i != value["expected_m31i_manifest_sha256"]:
        raise ValueError("M31I manifest hash mismatch")
    capability = value["m31k_capability_manifest"]
    supplied = capability.get("canonical_capability_manifest_sha256") if isinstance(capability, dict) else None
    hashable = copy.deepcopy(capability); hashable.pop("canonical_capability_manifest_sha256", None)
    if supplied != _sha(hashable) or supplied != value["expected_m31k_capability_sha256"]:
        raise ValueError("M31K capability hash mismatch")
    identity = value["m31i_identity"]; mapping = value["provider_exchange_mapping"]
    if not isinstance(identity, dict) or identity not in m31i.get("preferred_universe", []) or not isinstance(mapping, dict) or mapping.get("instrument_id") != identity.get("instrument_id") or mapping.get("isin") != identity.get("isin"):
        raise ValueError("exact M31I identity mapping required")
    if mapping.get("mapping_status") != "VERIFIED_PROVIDER_EXCHANGE_CODE" or mapping.get("provider_type") not in {"ETF", "Stock", "all"}:
        raise ValueError("verified bounded provider mapping required")
    if not all(isinstance(identity.get(key), str) and identity[key] for key in ("isin", "exchange_ticker", "trading_currency", "instrument_type")):
        raise ValueError("invalid M31I identity")
    return value


def _parse(response: object, identity: dict[str, Any], mapping: dict[str, Any], limit: int) -> dict[str, object]:
    if not isinstance(response, list):
        return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["MALFORMED_PROVIDER_RESPONSE"]}
    if len(response) > limit:
        return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["RESPONSE_LIMIT_EXCEEDED"]}
    candidates = []
    for item in response:
        if not isinstance(item, dict) or not _FIELDS.issubset(item) or any(not isinstance(item[key], str) or not item[key] for key in _FIELDS):
            return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["MALFORMED_PROVIDER_RESPONSE"]}
        if any(not math.isfinite(value) for value in item.values() if isinstance(value, float)):
            return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["MALFORMED_PROVIDER_RESPONSE"]}
        candidates.append({key: item[key].strip() for key in _FIELDS})
    exact = [item for item in candidates if item["ISIN"] == identity["isin"] and item["Exchange"] == mapping["eodhd_exchange_code"] and item["Currency"] == identity["trading_currency"] and item["Code"] == identity["exchange_ticker"]]
    common = {"candidate_count": len(candidates), "exact_match_count": len(exact), "response_sha256": _sha(candidates)}
    if not exact:
        return {**common, "resolution_status": "REVIEW_REQUIRED_NO_EXACT_MATCH", "review_findings": ["NO_EXACT_MATCH"]}
    unique = {json.dumps(item, sort_keys=True) for item in exact}
    if len(unique) != 1 or len(exact) != 1:
        return {**common, "resolution_status": "REVIEW_REQUIRED_AMBIGUOUS_EXACT_MATCH", "review_findings": ["AMBIGUOUS_EXACT_MATCH"]}
    if mapping["provider_type"] == "all":
        return {**common, "resolution_status": "REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY", "selected_candidate": exact[0], "review_findings": ["PROVIDER_TYPE_TAXONOMY_NOT_EXACT"]}
    if exact[0]["Type"] != mapping["provider_type"]:
        return {**common, "resolution_status": "REVIEW_REQUIRED_NO_EXACT_MATCH", "review_findings": ["PRODUCT_TYPE_MISMATCH"]}
    return {**common, "resolution_status": "RESOLVED_EXACT_PROVIDER_SYMBOL", "selected_candidate": exact[0], "resolved_provider_symbol": f"{exact[0]['Code']}.{exact[0]['Exchange']}", "review_findings": ["EXACT_PROVIDER_IDENTITY_MATCH"]}


def _finish(value: dict[str, Any]) -> dict[str, object]:
    value["output_payload_sha256"] = _sha(value)
    return copy.deepcopy(value)


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
