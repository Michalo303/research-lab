from research_lab.execution.official_instrument_identity_manifest_v2 import (
    build_official_instrument_identity_manifest,
)


def test_builds_verified_manifest_for_the_exact_official_universe():
    result = build_official_instrument_identity_manifest()

    assert result["manifest_status"] == "VERIFIED"
    assert [item["ticker"] for item in result["preferred_universe"]] == [
        "SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT",
        "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR",
    ]
    assert all(item["provider_symbol_status"] == "REQUIRES_EODHD_SYMBOL_RESOLUTION" for item in result["preferred_universe"])
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["provider_credentials_accessed"] is False
    assert result["safety_fields"]["production_runtime_supported"] is False


def test_stale_kid_evidence_downgrades_manifest_to_review_required(monkeypatch):
    import research_lab.execution.official_instrument_identity_manifest_v2 as module

    artifact = module._load_artifact()
    artifact["evidence"][2]["evidence_status"] = "STALE"
    monkeypatch.setattr(module, "_load_artifact", lambda: artifact)

    assert module.build_official_instrument_identity_manifest()["manifest_status"] == "REVIEW_REQUIRED"


def test_rejects_secondary_only_evidence(monkeypatch):
    import research_lab.execution.official_instrument_identity_manifest_v2 as module

    artifact = module._load_artifact()
    artifact["evidence"][0]["evidence_status"] = "SECONDARY_ONLY"
    monkeypatch.setattr(module, "_load_artifact", lambda: artifact)

    try:
        module.build_official_instrument_identity_manifest()
    except ValueError as error:
        assert "secondary" in str(error)
    else:
        raise AssertionError("secondary-only evidence must fail closed")


def test_rejects_evidence_that_only_names_supported_fields(monkeypatch):
    import research_lab.execution.official_instrument_identity_manifest_v2 as module

    artifact = module._load_artifact()
    artifact["evidence"][0]["field_evidence"] = {
        "ticker": "SMH",
    }
    monkeypatch.setattr(module, "_load_artifact", lambda: artifact)

    try:
        module.build_official_instrument_identity_manifest()
    except ValueError as error:
        assert "field-level" in str(error)
    else:
        raise AssertionError("each critical identity field needs value-bound evidence")


def test_builder_is_available_from_execution_package():
    from research_lab.execution import build_official_instrument_identity_manifest

    assert build_official_instrument_identity_manifest()["contract_version"] == "official_instrument_identity_manifest_v2"
