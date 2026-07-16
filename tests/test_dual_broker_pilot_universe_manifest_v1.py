import copy

from research_lab.execution.dual_broker_pilot_universe_manifest_v1 import (
    build_dual_broker_pilot_universe_manifest,
)


def _request():
    return {
        "version": "dual_broker_pilot_universe_manifest_request_v1",
        "manifest_id": "pilot-universe-2026-07-16",
        "metadata_date": "2026-07-16",
        "official_evidence": {
            "USO": "fixture:uso-official", "SMH": "fixture:smh-official", "4GLD": "fixture:4gld-official",
            "VWCE": "fixture:vwce-official", "EQQQ": "fixture:eqqq-official", "EIMI": "fixture:eimi-official", "IEAC": "fixture:ieac-official",
            "MSFT": "fixture:msft-official", "JNJ": "fixture:jnj-official", "XOM": "fixture:xom-official", "ASML": "fixture:asml-official",
            "SAP": "fixture:sap-official", "NESN": "fixture:nesn-official", "NOVO-B": "fixture:novo-official", "AIR": "fixture:air-official",
        },
        "provenance": {"source": "deterministic-local-fixture"},
    }


def test_builds_deterministic_review_only_pilot_manifest():
    request = _request()
    first = build_dual_broker_pilot_universe_manifest(request)
    second = build_dual_broker_pilot_universe_manifest(copy.deepcopy(request))

    assert first == second
    assert first["manifest_status"] == "REVIEW_REQUIRED"
    assert [item["ticker"] for item in first["fio_manual_long_term"]] == ["SMH", "USO"]
    gold = next(item for item in first["ibkr_etf_etc_swing"] if item["ticker"] == "4GLD")
    assert gold["instrument_type"] == "PHYSICAL_GOLD_ETC"
    assert gold["ucits_status"] == "NOT_UCITS_EXPLICIT_EXCEPTION"
    assert len(first["ibkr_common_stock_swing"]) == 8
    assert first["safety_fields"]["provider_calls_used"] == 0
    assert first["safety_fields"]["broker_calls_used"] == 0
    assert first["safety_fields"]["data_acquisition_authorized"] is False


def test_preserves_required_non_identity_mapping_types():
    result = build_dual_broker_pilot_universe_manifest(_request())
    mappings = {item["mapping_id"]: item["mapping_type"] for item in result["proposed_mappings"]}

    assert mappings["QQQ_TO_EU_NASDAQ"] == "ECONOMIC_PROXY"
    assert mappings["GLD_TO_4GLD"] == "RELATED_EXPOSURE_NOT_IDENTICAL"
    assert mappings["USO_TO_OIL_PRODUCER"] == "RELATED_EXPOSURE_NOT_IDENTICAL"


def test_rejects_missing_required_official_evidence_and_unknown_fields():
    request = _request()
    request["official_evidence"].pop("4GLD")
    try:
        build_dual_broker_pilot_universe_manifest(request)
    except ValueError as error:
        assert "official evidence" in str(error)
    else:
        raise AssertionError("missing official evidence must fail")


def test_manifest_has_explicit_evidence_and_complete_review_analyses_for_every_instrument():
    result = build_dual_broker_pilot_universe_manifest(_request())
    preferred = result["preferred_universe"]

    assert all(item["official_evidence"] != "fixture:uso-official" or item["ticker"] == "USO" for item in preferred)
    assert all(item["official_evidence"] for item in preferred)
    assert result["overlap_analysis"]
    assert result["sector_asset_class_currency_analysis"]
    assert result["corporate_action_and_delisting_analysis"]
    assert len(result["official_evidence_index"]) == len(preferred)
    assert result["safety_fields"]["production_runtime_supported"] is False
