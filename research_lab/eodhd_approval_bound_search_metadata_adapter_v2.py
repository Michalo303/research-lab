"""M31O exact, externally approval-bound EODHD Search adapter."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

CONTRACT_VERSION = "eodhd_approval_bound_search_metadata_adapter_v2"
PURPOSE = "EODHD_BOUNDED_SEARCH_IDENTITY_RESOLUTION_ONLY_V2"
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "retries_used": 0, "fallback_used": False, "pagination_calls": 0, "health_check_calls": 0, "historical_data_requested": False, "corporate_actions_requested": False, "filesystem_writes_performed": False, "production_runtime_supported": False}
_FIELDS = {"Code", "Exchange", "Name", "Type", "Country", "Currency", "ISIN"}


def resolve_approved_eodhd_search_v2(request: dict[str, object]) -> dict[str, object]:
    """Perform one fake-or-future approved Search call; no self-approval exists."""
    value = _validate_request(request)
    record = value["selected_record"]
    output: dict[str, Any] = {"version": "eodhd_approval_bound_search_metadata_result_v2", "contract_version": CONTRACT_VERSION, "selected_sequence": record["sequence"], "instrument_id": record["instrument_id"], "redacted_request_plan": {"path": record["request_path"], "parameters": copy.deepcopy(record["query_parameters"])}, "resolution_status": "DRY_RUN_PROVIDER_CALL_NOT_AUTHORIZED", "candidate_count": 0, "exact_match_count": 0, "selected_candidate": None, "review_findings": ["NO_RETRY", "NO_FALLBACK", "NO_PAGINATION", "NO_HEALTH_CHECK", "NO_CREDENTIAL_IN_OUTPUT"], "safety_fields": copy.deepcopy(_SAFETY)}
    if value["mode"] == "DRY_RUN":
        return _finish(output)
    client = value["client"]; credentials = value["credentials"]
    response = client(record["request_path"], copy.deepcopy(record["query_parameters"]), credentials)
    output["safety_fields"]["provider_calls_used"] = 1
    output["safety_fields"]["provider_credentials_accessed"] = True
    output.update(_parse(response, record))
    return _finish(output)


def _validate_request(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError("request must be a mapping")
    required = {"mode", "approval_manifest", "external_approved_approval_manifest_sha256", "acquisition_plan_sha256", "selected_sequence", "selected_record", "allow_provider_calls", "consumed_call_ledger"}
    if raw.get("mode") == "APPROVED_EXECUTION": required |= {"client", "credentials"}
    if set(raw) != required: raise ValueError("unknown or missing request fields")
    value = copy.deepcopy(raw)
    if value["mode"] not in {"DRY_RUN", "APPROVED_EXECUTION"}: raise ValueError("invalid mode")
    manifest = value["approval_manifest"]
    if not isinstance(manifest, dict): raise ValueError("approval manifest required")
    hashable = copy.deepcopy(manifest); supplied = hashable.pop("canonical_approval_manifest_sha256", None)
    if not isinstance(supplied, str) or supplied != _sha(hashable): raise ValueError("approval manifest was mutated")
    if value["external_approved_approval_manifest_sha256"] != supplied: raise ValueError("external approved approval-manifest hash required")
    if manifest.get("purpose") != PURPOSE or manifest.get("adapter_contract_version") != CONTRACT_VERSION or value["acquisition_plan_sha256"] != manifest.get("acquisition_plan_sha256"): raise ValueError("exact approval contract required")
    record = value["selected_record"]
    if not isinstance(record, dict) or value["selected_sequence"] != record.get("sequence"): raise ValueError("exact selected sequence required")
    record_hash = record.get("canonical_per_call_record_sha256"); record_hashable = copy.deepcopy(record); record_hashable.pop("canonical_per_call_record_sha256", None)
    if record_hash != _sha(record_hashable): raise ValueError("per-call record hash mismatch")
    records = manifest.get("authorized_records")
    if not isinstance(records, list) or record not in records or record.get("authorization_status") != "AUTHORIZABLE_BOUNDED_SEARCH_V2": raise ValueError("record absent or blocked")
    if record["request_path"] != f"/api/search/{record['isin']}" or record["query_parameters"] != {"exchange": record["eodhd_exchange_code"], "type": record["query_parameters"].get("type"), "limit": 10, "fmt": "json"} or record["query_parameters"]["type"] not in {"all", "stock", "etf"}: raise ValueError("exact request contract required")
    ledger = value["consumed_call_ledger"]
    if not isinstance(ledger, dict) or ledger.get("consumed_metadata_calls") != 0 or manifest.get("call_budgets", {}).get("metadata_calls_max", 0) < 1: raise ValueError("metadata budget exhausted")
    if value["mode"] == "APPROVED_EXECUTION":
        if not value["allow_provider_calls"] or not callable(value["client"]) or value["credentials"] is None: raise ValueError("approved execution requires allow flag, client, and credentials")
    elif value["allow_provider_calls"]: raise ValueError("dry run cannot allow provider calls")
    return value


def _parse(response: object, record: dict[str, Any]) -> dict[str, object]:
    if not isinstance(response, list) or len(response) > 10: return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["MALFORMED_RESPONSE" if not isinstance(response, list) else "RESPONSE_LIMIT_EXCEEDED"]}
    candidates = []
    for item in response:
        if not isinstance(item, dict) or not _FIELDS.issubset(item) or any(not isinstance(item[key], str) or not item[key] for key in _FIELDS): return {"resolution_status": "FAILED_VALIDATION", "review_findings": ["MALFORMED_RESPONSE"]}
        candidates.append({key: item[key].strip() for key in _FIELDS})
    exact = [item for item in candidates if item["ISIN"] == record["isin"] and item["Exchange"] == record["eodhd_exchange_code"] and item["Currency"] == record["currency"] and item["Code"] == record["exchange_ticker"]]
    common = {"candidate_count": len(candidates), "exact_match_count": len(exact)}
    if not exact: return {**common, "resolution_status": "REVIEW_REQUIRED_NO_EXACT_MATCH", "review_findings": ["NO_EXACT_MATCH"]}
    if len(exact) != 1: return {**common, "resolution_status": "REVIEW_REQUIRED_AMBIGUOUS_EXACT_MATCH", "review_findings": ["AMBIGUOUS_EXACT_MATCH"]}
    if exact[0]["Type"] not in record["accepted_response_types"]: return {**common, "resolution_status": "REVIEW_REQUIRED_NO_EXACT_MATCH", "review_findings": ["RESPONSE_TYPE_MISMATCH"]}
    if record["type_taxonomy_status"] != "EXACT_PROVIDER_TYPE_TAXONOMY": return {**common, "resolution_status": "REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY", "selected_candidate": exact[0], "review_findings": ["PROVIDER_TYPE_TAXONOMY_NOT_EXACT"]}
    dimensions = {"instrument_identity_match": True, "provider_namespace_match": True, "selected_mic_supported_by_mapping_evidence": True, "response_type_match": True}
    return {**common, "resolution_status": "RESOLVED_EXACT_PROVIDER_SYMBOL", "selected_candidate": exact[0], "resolved_provider_symbol": f"{exact[0]['Code']}.{exact[0]['Exchange']}", "evidence_dimensions": dimensions, "review_findings": ["EXACT_PROVIDER_IDENTITY_MATCH"]}


def _finish(value: dict[str, Any]) -> dict[str, object]:
    value["output_payload_sha256"] = _sha(value)
    return copy.deepcopy(value)


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
