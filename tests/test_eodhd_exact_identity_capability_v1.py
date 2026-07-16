import copy

from research_lab.execution.official_instrument_identity_manifest_v2 import (
    build_official_instrument_identity_manifest,
)


def test_builds_a_bounded_isin_search_capability_without_provider_io():
    from research_lab.execution.eodhd_exact_identity_capability_v1 import (
        build_eodhd_exact_identity_capability,
    )

    identity = build_official_instrument_identity_manifest()
    result = build_eodhd_exact_identity_capability(identity)

    assert result["capability_status"] == "BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE"
    assert result["endpoint_class"] == "EODHD_SEARCH_BY_ISIN_EXCHANGE_BOUNDED_V1"
    assert result["fixed_result_limit"] == 10
    assert result["allowed_parameters"] == ["exchange", "fmt", "limit", "type"]
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["provider_credentials_accessed"] is False
    assert result["canonical_capability_manifest_sha256"]
    assert any(item["mapping_status"] == "VERIFIED_PROVIDER_EXCHANGE_CODE" for item in result["exchange_code_mappings"])
    assert all(item["isin"] for item in result["exchange_code_mappings"])
    assert result["exchange_code_mappings"][-1]["instrument_id"] == "M31I:AIR"

    result["exchange_code_mappings"][0]["eodhd_exchange_code"] = "MUTATED"
    assert build_eodhd_exact_identity_capability(identity)["exchange_code_mappings"][0]["eodhd_exchange_code"] != "MUTATED"
    assert identity == build_official_instrument_identity_manifest()


def test_rejects_non_verified_m31i_identity_and_unsafe_capability_artifact():
    from research_lab.execution.eodhd_exact_identity_capability_v1 import (
        build_eodhd_exact_identity_capability,
    )

    identity = build_official_instrument_identity_manifest()
    invalid = copy.deepcopy(identity)
    invalid["manifest_status"] = "REVIEW_REQUIRED"
    try:
        build_eodhd_exact_identity_capability(invalid)
    except ValueError as exc:
        assert "VERIFIED" in str(exc)
    else:
        raise AssertionError("non-VERIFIED identity must fail closed")
