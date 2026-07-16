import copy

from research_lab.execution.eodhd_exact_identity_capability_v1 import build_eodhd_exact_identity_capability
from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def _request(*, allow=False):
    manifest = build_official_instrument_identity_manifest()
    capability = build_eodhd_exact_identity_capability(manifest)
    identity = manifest["preferred_universe"][0]
    mapping = capability["exchange_code_mappings"][0]
    return {"version": "eodhd_bounded_search_metadata_request_v1", "request_id": "M31L:SMH:1", "m31i_manifest": manifest, "m31i_identity": identity, "expected_m31i_manifest_sha256": manifest["canonical_manifest_sha256"], "m31k_capability_manifest": capability, "expected_m31k_capability_sha256": capability["canonical_capability_manifest_sha256"], "provider_exchange_mapping": mapping, "endpoint_policy": "EODHD_SEARCH_BY_ISIN_EXCHANGE_BOUNDED_V1", "response_limit_policy": 10, "call_budget_policy": {"max_provider_calls": 1, "consumed_provider_calls": 0}, "allow_provider_calls": allow, "provenance": "M31I_M31K_ONLY"}


def test_dry_run_constructs_exact_isin_request_without_client_call():
    from research_lab.eodhd_bounded_search_metadata_adapter_v1 import resolve_bounded_eodhd_search

    calls = []
    result = resolve_bounded_eodhd_search(_request(), client=lambda *_: calls.append(True))

    assert result["resolution_status"] == "DRY_RUN_PROVIDER_CALL_NOT_AUTHORIZED"
    assert result["redacted_request_plan"] == {"path": "/api/search/US92189F6768", "parameters": {"exchange": "US", "fmt": "json", "limit": 10, "type": "ETF"}}
    assert calls == []
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["historical_data_requested"] is False


def test_live_path_requires_matching_approval_and_parses_only_exact_candidate():
    from research_lab.eodhd_bounded_search_metadata_adapter_v1 import resolve_bounded_eodhd_search

    request = _request(allow=True)
    try:
        resolve_bounded_eodhd_search(request, client=lambda *_: [])
    except ValueError as exc:
        assert "approval" in str(exc).lower()
    else:
        raise AssertionError("allow flag alone must not call the provider")

    approved = copy.deepcopy(request)
    approved["external_human_approval_hash"] = "not-a-match"
    try:
        resolve_bounded_eodhd_search(approved, client=lambda *_: [], credentials="injected")
    except ValueError as exc:
        assert "approval" in str(exc).lower()
    else:
        raise AssertionError("mismatched approval must fail closed")
