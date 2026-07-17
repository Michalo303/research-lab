import copy

import pytest

from research_lab.execution.eodhd_exact_identity_capability_v2 import build_eodhd_exact_identity_capability_v2
from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def _request():
    identity = build_official_instrument_identity_manifest(); capability = build_eodhd_exact_identity_capability_v2(identity)
    return {"version": "eodhd_exact_symbol_resolution_readiness_request_v3", "readiness_request_id": "M31P:V3", "m31i_manifest": identity, "expected_m31i_canonical_manifest_sha256": identity["canonical_manifest_sha256"], "m31n_capability_manifest": capability, "expected_m31n_canonical_capability_sha256": capability["canonical_capability_manifest_sha256"], "m31o_adapter_contract_version": "eodhd_approval_bound_search_metadata_adapter_v2", "provider_call_policy": "BOUNDED_EODHD_SEARCH_ONLY", "destination_policy": "PENDING_EXACT_SYMBOL_RESOLUTION_V3", "approval_policy": "EXTERNAL_HUMAN_APPROVAL_REQUIRED", "provenance": "M31I_M31N_M31O_ONLY"}


def test_builds_replayable_approval_manifest_without_provider_io():
    from research_lab.execution.eodhd_exact_symbol_resolution_readiness_v3 import build_eodhd_exact_symbol_resolution_readiness_v3
    result = build_eodhd_exact_symbol_resolution_readiness_v3(_request())
    assert result["status"] == "HUMAN_APPROVAL_REQUIRED_FOR_CONTROLLED_EODHD_SEARCH_RESOLUTION_V2"
    assert len(result["complete_plan"]) == 15
    assert result["call_budgets"]["metadata_calls_max"] <= 15
    assert result["call_budgets"]["total_calls_max"] == result["call_budgets"]["metadata_calls_max"]
    assert result["approval_manifest"]["purpose"] == "EODHD_BOUNDED_SEARCH_IDENTITY_RESOLUTION_ONLY_V2"
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["provider_credentials_accessed"] is False
    assert all(record["future_destination"].startswith("/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3/") for record in result["authorized_records"])


def test_binds_m31p_manifest_to_m31o_externally_and_rejects_mutation_before_call():
    from research_lab.eodhd_approval_bound_search_metadata_adapter_v2 import resolve_approved_eodhd_search_v2
    from research_lab.execution.eodhd_exact_symbol_resolution_readiness_v3 import build_eodhd_exact_symbol_resolution_readiness_v3
    result = build_eodhd_exact_symbol_resolution_readiness_v3(_request()); record = result["authorized_records"][0]; calls=[]
    def client(path, parameters, credentials):
        calls.append((path, parameters, credentials)); return [{"Code": record["exchange_ticker"], "Exchange": record["eodhd_exchange_code"], "Name": record["legal_name"], "Type": record["accepted_response_types"][0], "Country": "x", "Currency": record["currency"], "ISIN": record["isin"]}]
    request = {"mode":"APPROVED_EXECUTION", "approval_manifest":result["approval_manifest"], "external_approved_approval_manifest_sha256":result["approval_manifest_sha256"], "acquisition_plan_sha256":result["acquisition_plan_sha256"], "selected_sequence":record["sequence"], "selected_record":record, "allow_provider_calls":True, "consumed_call_ledger":{"consumed_metadata_calls":0}, "client":client, "credentials":"fake-secret"}
    output=resolve_approved_eodhd_search_v2(request); assert len(calls)==1 and "fake-secret" not in str(output)
    bad=copy.deepcopy(request); bad["external_approved_approval_manifest_sha256"]="bad"
    with pytest.raises(ValueError): resolve_approved_eodhd_search_v2(bad)
