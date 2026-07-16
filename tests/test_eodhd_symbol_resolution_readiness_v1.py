from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def test_exact_verified_m31i_manifest_fails_closed_without_a_bounded_metadata_endpoint():
    from research_lab.execution.eodhd_symbol_resolution_readiness_v1 import build_eodhd_symbol_resolution_readiness

    m31i = build_official_instrument_identity_manifest()
    result = build_eodhd_symbol_resolution_readiness({
        "version": "eodhd_symbol_resolution_readiness_request_v1",
        "readiness_request_id": "M31J:2026-07-16:V1",
        "m31i_manifest": m31i,
        "expected_m31i_canonical_manifest_sha256": m31i["canonical_manifest_sha256"],
        "provider_policy": "EODHD_METADATA_ONLY",
        "endpoint_policy": "BOUND_EXACT_IDENTITY_ONLY",
        "private_destination_root": "/opt/trading/private/research_market_data_snapshots/pending_symbol_resolution_v1/",
        "call_budget_policy": "ZERO_UNLESS_BOUNDED",
        "approval_policy": "HUMAN_APPROVAL_REQUIRED",
        "provenance": "M31I_MERGED_MANIFEST_ONLY",
    })

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["call_budgets"]["metadata_calls_max"] == 0
    assert len(result["blocked_resolution_items"]) == 15
    assert {item["provider_symbol_status"] for item in result["blocked_resolution_items"]} == {"NOT_AUTHORIZED_UNBOUNDED_RESOLUTION"}
    assert result["safety_fields"]["provider_calls_used"] == 0

