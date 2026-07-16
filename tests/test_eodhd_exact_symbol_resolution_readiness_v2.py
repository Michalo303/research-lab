from research_lab.execution.eodhd_exact_identity_capability_v1 import build_eodhd_exact_identity_capability
from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def test_builds_a_human_approval_plan_without_provider_calls_or_writes():
    from research_lab.execution.eodhd_exact_symbol_resolution_readiness_v2 import build_eodhd_exact_symbol_resolution_readiness

    identity = build_official_instrument_identity_manifest()
    capability = build_eodhd_exact_identity_capability(identity)
    result = build_eodhd_exact_symbol_resolution_readiness({"version": "eodhd_exact_symbol_resolution_readiness_request_v2", "readiness_request_id": "M31M:V2", "m31i_manifest": identity, "expected_m31i_canonical_manifest_sha256": identity["canonical_manifest_sha256"], "m31k_capability_manifest": capability, "expected_m31k_canonical_manifest_sha256": capability["canonical_capability_manifest_sha256"], "m31l_adapter_contract_version": "eodhd_bounded_search_metadata_adapter_v1", "provider_call_policy": "BOUNDED_SEARCH_ONLY", "destination_policy": "PENDING_EXACT_SYMBOL_RESOLUTION_V2", "approval_policy": "HUMAN_APPROVAL_REQUIRED", "provenance": "M31I_M31K_M31L_ONLY"})
    assert result["status"] == "HUMAN_APPROVAL_REQUIRED_FOR_CONTROLLED_EODHD_SEARCH_RESOLUTION"
    assert result["call_budgets"]["metadata_calls_max"] == len(result["authorized_records"])
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert all(item["future_destination"].startswith("/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v2/") for item in result["authorized_records"])
