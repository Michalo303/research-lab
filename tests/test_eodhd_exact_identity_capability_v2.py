import copy

import pytest

from research_lab.execution.official_instrument_identity_manifest_v2 import (
    build_official_instrument_identity_manifest,
)


def test_composes_exact_m31i_mappings_with_separate_search_and_response_taxonomies():
    from research_lab.execution.eodhd_exact_identity_capability_v2 import (
        build_eodhd_exact_identity_capability_v2,
    )

    result = build_eodhd_exact_identity_capability_v2(
        build_official_instrument_identity_manifest()
    )

    assert result["capability_status"] == "BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE_V2"
    assert result["official_capability_evidence"]["endpoint_template"] == "/api/search/{query_string}"
    assert len(result["exchange_code_mappings"]) == 15
    by_ticker = {item["ticker_label"]: item for item in result["exchange_code_mappings"]}
    assert by_ticker["SMH"]["search_type_parameter"] == "etf"
    assert "ETF" in by_ticker["SMH"]["accepted_response_types"]
    assert by_ticker["MSFT"]["search_type_parameter"] == "stock"
    assert "Common Stock" in by_ticker["MSFT"]["accepted_response_types"]
    assert by_ticker["4GLD"]["legal_product_type"] == "PHYSICAL_GOLD_ETC"
    assert by_ticker["4GLD"]["search_type_parameter"] == "all"
    assert by_ticker["4GLD"]["type_taxonomy_status"] == "PROVIDER_TYPE_TAXONOMY_NOT_EXACT"
    assert by_ticker["USO"]["legal_product_type"] != "COMMON_STOCK"
    assert by_ticker["SMH"]["provider_namespace_classification"] == "PROVIDER_MULTI_VENUE_NAMESPACE"
    assert by_ticker["SMH"]["selected_mic_membership_status"] == "SELECTED_MIC_CONTAINED_IN_PROVIDER_OPERATING_MICS"
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["provider_credentials_accessed"] is False


def test_rejects_tampered_m31i_and_returns_deeply_immutable_deterministic_results():
    from research_lab.execution.eodhd_exact_identity_capability_v2 import (
        build_eodhd_exact_identity_capability_v2,
    )

    manifest = build_official_instrument_identity_manifest()
    first = build_eodhd_exact_identity_capability_v2(manifest)
    second = build_eodhd_exact_identity_capability_v2(manifest)
    assert first == second
    first["exchange_code_mappings"][0]["accepted_response_types"].append("MUTATED")
    assert "MUTATED" not in build_eodhd_exact_identity_capability_v2(manifest)["exchange_code_mappings"][0]["accepted_response_types"]

    tampered = copy.deepcopy(manifest)
    tampered["preferred_universe"][0]["isin"] = "MUTATED"
    with pytest.raises(ValueError, match="hash mismatch"):
        build_eodhd_exact_identity_capability_v2(tampered)


def test_exports_v2_builder_from_execution_package():
    from research_lab.execution import build_eodhd_exact_identity_capability_v2

    assert build_eodhd_exact_identity_capability_v2(
        build_official_instrument_identity_manifest()
    )["contract_version"] == "eodhd_exact_identity_capability_v2"
