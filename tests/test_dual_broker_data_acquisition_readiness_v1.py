import copy

from research_lab.execution.dual_broker_data_acquisition_readiness_v1 import (
    build_dual_broker_data_acquisition_readiness,
)
from research_lab.execution.dual_broker_pilot_universe_manifest_v1 import (
    build_dual_broker_pilot_universe_manifest,
)


def _manifest():
    evidence = {ticker: f"fixture:{ticker.lower()}-official" for ticker in (
        "SMH", "USO", "VWCE", "EQQQ", "EIMI", "IEAC", "4GLD", "MSFT", "JNJ", "XOM", "ASML", "SAP", "NESN", "NOVO-B", "AIR",
    )}
    return build_dual_broker_pilot_universe_manifest({"version": "dual_broker_pilot_universe_manifest_request_v1", "manifest_id": "pilot-universe-2026-07-16", "metadata_date": "2026-07-16", "official_evidence": evidence, "provenance": {"source": "deterministic-local-fixture"}})


def test_builds_deterministic_human_gated_plan_from_manifest_only():
    manifest = _manifest()
    request = {"version": "dual_broker_data_acquisition_readiness_request_v1", "m31g_manifest": manifest}
    first = build_dual_broker_data_acquisition_readiness(request)
    second = build_dual_broker_data_acquisition_readiness(copy.deepcopy(request))

    assert first == second
    assert first["status"] == "HUMAN_APPROVAL_REQUIRED_FOR_DATA_ACQUISITION"
    assert first["verified_m31g_manifest_sha256"] == manifest["canonical_manifest_sha256"]
    assert len(first["acquisition_plan"]) == len(manifest["preferred_universe"]) * 4
    assert sum(entry["call_class"] == "NOT_AUTHORIZED" for entry in first["acquisition_plan"]) == 15
    assert first["call_budgets"] == {"metadata_calls_max": 15, "historical_calls_max": 15, "corporate_action_calls_max": 15, "calendar_calls_max": 0, "total_calls_max": 45}
    assert first["snapshots_reused"][0]["status"] == "REUSE_EXISTING_SNAPSHOT"
    assert first["safety_fields"]["provider_calls_used"] == 0
    assert first["safety_fields"]["data_acquisition_authorized"] is False


def test_fails_closed_when_manifest_hash_or_spy_identity_is_changed():
    manifest = _manifest()
    manifest["canonical_manifest_sha256"] = "0" * 64
    try:
        build_dual_broker_data_acquisition_readiness({"version": "dual_broker_data_acquisition_readiness_request_v1", "m31g_manifest": manifest})
    except ValueError as error:
        assert "manifest" in str(error)
    else:
        raise AssertionError("tampered manifest must fail")
