import copy
import hashlib
import json

import pytest


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _approval():
    record = {"sequence": 1, "instrument_id": "M31I:SMH", "isin": "US92189F6768", "selected_mic": "XNAS", "eodhd_exchange_code": "US", "request_path": "/api/search/US92189F6768", "query_parameters": {"exchange": "US", "type": "etf", "limit": 10, "fmt": "json"}, "accepted_response_types": ["ETF"], "currency": "USD", "exchange_ticker": "SMH", "provider_namespace_classification": "PROVIDER_MULTI_VENUE_NAMESPACE", "type_taxonomy_status": "EXACT_PROVIDER_TYPE_TAXONOMY", "future_destination": "/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3/M31I_SMH/search-response.json", "authorization_status": "AUTHORIZABLE_BOUNDED_SEARCH_V2"}
    record["canonical_per_call_record_sha256"] = _sha(record)
    manifest = {"purpose": "EODHD_BOUNDED_SEARCH_IDENTITY_RESOLUTION_ONLY_V2", "acquisition_plan_sha256": "plan-hash", "adapter_contract_version": "eodhd_approval_bound_search_metadata_adapter_v2", "call_budgets": {"metadata_calls_max": 1}, "authorized_records": [record]}
    manifest["canonical_approval_manifest_sha256"] = _sha(manifest)
    return manifest, record


def test_dry_run_never_calls_provider_or_accesses_credentials():
    from research_lab.eodhd_approval_bound_search_metadata_adapter_v2 import resolve_approved_eodhd_search_v2
    manifest, record = _approval()
    result = resolve_approved_eodhd_search_v2({"mode": "DRY_RUN", "approval_manifest": manifest, "external_approved_approval_manifest_sha256": manifest["canonical_approval_manifest_sha256"], "acquisition_plan_sha256": "plan-hash", "selected_sequence": 1, "selected_record": record, "allow_provider_calls": False, "consumed_call_ledger": {"consumed_metadata_calls": 0}})
    assert result["resolution_status"] == "DRY_RUN_PROVIDER_CALL_NOT_AUTHORIZED"
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["provider_credentials_accessed"] is False


def test_execution_requires_external_manifest_hash_and_validates_one_exact_fake_response():
    from research_lab.eodhd_approval_bound_search_metadata_adapter_v2 import resolve_approved_eodhd_search_v2
    manifest, record = _approval(); calls = []
    def client(path, parameters, credentials):
        calls.append((path, parameters, credentials)); return [{"Code": "SMH", "Exchange": "US", "Name": "VanEck Semiconductor ETF", "Type": "ETF", "Country": "US", "Currency": "USD", "ISIN": "US92189F6768"}]
    request = {"mode": "APPROVED_EXECUTION", "approval_manifest": manifest, "external_approved_approval_manifest_sha256": manifest["canonical_approval_manifest_sha256"], "acquisition_plan_sha256": "plan-hash", "selected_sequence": 1, "selected_record": record, "allow_provider_calls": True, "consumed_call_ledger": {"consumed_metadata_calls": 0}, "client": client, "credentials": "secret"}
    result = resolve_approved_eodhd_search_v2(request)
    assert calls == [("/api/search/US92189F6768", {"exchange": "US", "type": "etf", "limit": 10, "fmt": "json"}, "secret")]
    assert result["resolution_status"] == "RESOLVED_EXACT_PROVIDER_SYMBOL"
    assert result["safety_fields"]["provider_credentials_accessed"] is True
    assert "secret" not in json.dumps(result)
    for key, value in (("external_approved_approval_manifest_sha256", "wrong"), ("allow_provider_calls", False)):
        bad = copy.deepcopy(request); bad[key] = value
        with pytest.raises(ValueError): resolve_approved_eodhd_search_v2(bad)


def test_rejects_mutated_or_replayed_records_before_fake_call():
    from research_lab.eodhd_approval_bound_search_metadata_adapter_v2 import resolve_approved_eodhd_search_v2
    manifest, record = _approval(); mutated = copy.deepcopy(record); mutated["query_parameters"]["type"] = "stock"
    request = {"mode": "APPROVED_EXECUTION", "approval_manifest": manifest, "external_approved_approval_manifest_sha256": manifest["canonical_approval_manifest_sha256"], "acquisition_plan_sha256": "plan-hash", "selected_sequence": 1, "selected_record": mutated, "allow_provider_calls": True, "consumed_call_ledger": {"consumed_metadata_calls": 1}, "client": lambda *_: pytest.fail("client must not run"), "credentials": "secret"}
    with pytest.raises(ValueError): resolve_approved_eodhd_search_v2(request)
