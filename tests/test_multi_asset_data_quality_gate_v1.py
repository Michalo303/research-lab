from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "multi_asset_data_quality_gate_v1.py"
SHA_A, SHA_B, SHA_C, SHA_D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)

def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _run(request):
    spec = importlib.util.spec_from_file_location("multi_asset_data_quality_gate_v1", MODULE_PATH)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.build_multi_asset_data_quality_gate(copy.deepcopy(request))


def _asset(instrument_id, symbol, currency="USD", start="2024-01-01", close2=101.0, **overrides):
    bars = [{"timestamp": f"{start}T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000}, {"timestamp": "2024-01-02T00:00:00Z", "open": close2, "high": close2 + 1, "low": close2 - 1, "close": close2, "volume": 1100}]
    return {"instrument_id": instrument_id, "provider_symbol": symbol, "currency": currency, "calendar_id": "XNYS", "corporate_action_policy_id": "SPLIT_ADJUSTED_ONLY", "dataset_id": f"dataset-{instrument_id}", "source_artifact_sha256": SHA_A, "normalized_bars_sha256": SHA_B, "adapter_result_sha256": SHA_C, "row_count": len(bars), "first_timestamp": bars[0]["timestamp"], "last_timestamp": bars[-1]["timestamp"], "bars": bars, **overrides}


def _request(assets=None, **overrides):
    assets = assets or [_asset("asset-a", "AAA"), _asset("asset-b", "BBB")]
    instruments = [{"instrument_id": a["instrument_id"], "provider_symbol": a["provider_symbol"], "currency": a["currency"], "calendar_id": a["calendar_id"], "corporate_action_policy_id": a["corporate_action_policy_id"]} for a in assets]
    child_hashes = {"universe": SHA_D, "snapshot": SHA_D, "calendars": {a["instrument_id"]: SHA_D for a in assets}, "corporate_actions": {a["instrument_id"]: SHA_D for a in assets}}
    bindings=[]
    for a in assets:
        adapter={"version":"local_ohlcv_file_input_adapter_result_v1","adapter_version":"local_ohlcv_file_input_adapter_v1","status":"SUCCESS","source_sha256":a["source_artifact_sha256"],"normalized_rows_hash":a["normalized_bars_sha256"],"row_count":len(a["bars"]),"first_timestamp":a["bars"][0]["timestamp"],"last_timestamp":a["bars"][-1]["timestamp"],"downstream_adapter_result":{"synthetic_bars":copy.deepcopy(a["bars"])},"network_used":False,"provider_calls_used":0,"production_runtime_supported":False}
        digest=_canonical(adapter); a["adapter_result_sha256"]=digest; bindings.append({"instrument_id":a["instrument_id"],"adapter_result":adapter,"adapter_result_sha256":digest,"provenance":{}})
    return {"version": "multi_asset_data_quality_gate_request_v1", "quality_gate_id": "quality-gate-001", "universe_result": {"output_payload_sha256": SHA_D, "validated_universe": {"universe_id": "universe-v1", "universe_version": "v1", "base_currency": "USD", "instruments": [{**i, "point_in_time_membership_status": "POINT_IN_TIME_VERIFIED"} for i in instruments]}}, "multi_asset_snapshot_result": {"output_payload_sha256": SHA_D, "validated_asset_series": assets}, "asset_bar_bindings":bindings, "calendar_results": [{"instrument_id": a["instrument_id"], "calendar_id": a["calendar_id"], "validation_status": "COMPLETE", "validation_summary": {"missing_sessions": [], "unexpected_sessions": [], "duplicate_observed_sessions": [], "bars_on_closed_sessions": [], "bars_before_open": [], "bars_after_close": [], "bars_outside_calendar_coverage": []}, "output_payload_sha256": SHA_D} for a in assets], "corporate_action_results": [{"instrument_id": a["instrument_id"], "contract_status": "COMPLETE", "adjustment_policy": a["corporate_action_policy_id"], "blocking_findings": [], "output_payload_sha256": SHA_D} for a in assets], "fx_result": None, "thresholds": {"minimum_rows_per_asset": 2, "minimum_common_overlap_days": 1, "maximum_end_staleness_days": 1, "maximum_start_delay_days": 1, "maximum_missing_session_count": 0, "maximum_missing_session_ratio": 0.0, "maximum_unexpected_session_count": 0, "maximum_single_period_return_abs": 0.5, "split_candidate_tolerance": 0.05, "volume_integrality_required": True}, "policies": {"static_universe_requires_review": True, "allow_unsafe_current_membership": False, "not_point_in_time_safe_requires_failure": True, "missing_session_severity": "FAIL", "unexpected_session_severity": "FAIL"}, "expected_child_hashes": child_hashes, "as_of_timestamp": "2024-01-02T00:00:00Z", "provenance": {"source": "test"}, **overrides}


def test_clean_two_asset_dataset_passes_with_deterministic_lineage_and_safety():
    result = _run(_request())
    assert result["overall_status"] == "PASS"
    assert result["per_asset_status"] == {"asset-a": "PASS", "asset-b": "PASS"}
    assert result["overlap_summary"]["common_overlap_days"] == 1
    assert result["provider_calls_used"] == result["broker_actions_used"] == 0
    assert result["network_used"] is False and result["data_mutation_performed"] is False


@pytest.mark.parametrize("path", [
    ("expected_child_hashes", "snapshot"),
    ("expected_child_hashes", "universe"),
    ("expected_child_hashes", "calendars", "asset-a"),
    ("expected_child_hashes", "corporate_actions", "asset-a"),
])
def test_expected_child_hash_mismatch_fails_closed(path):
    request = _request()
    target = request
    for key in path:
        target = target[key]
    target = "e" * 64
    # assign through the final path rather than mutating a detached string
    holder = request
    for key in path[:-1]:
        holder = holder[key]
    holder[path[-1]] = target
    result = _run(request)
    assert result["overall_status"] == "FAILED_VALIDATION"
    assert any(item["finding_code"] == "CHILD_OUTPUT_HASH_MISMATCH" for item in result["blocking_findings"])


def test_missing_and_extra_child_instruments_fail_closed_and_findings_are_structured():
    request = _request()
    request["calendar_results"].pop()
    request["corporate_action_results"].append({"instrument_id": "extra", "contract_status": "COMPLETE", "adjustment_policy": "SPLIT_ADJUSTED_ONLY", "blocking_findings": [], "output_payload_sha256": SHA_D})
    result = _run(request)
    assert result["overall_status"] == "FAILED_VALIDATION"
    assert {item["finding_code"] for item in result["blocking_findings"]} >= {"CHILD_INSTRUMENT_MEMBERSHIP_MISMATCH"}
    assert all("severity" in item and "instrument_id" in item for item in result["blocking_findings"])


def test_foreign_currency_requires_matching_fx_conversion_and_output_hash():
    assets = [_asset("asset-a", "AAA"), _asset("asset-b", "BBB", currency="EUR")]
    request = _request(assets)
    assert _run(request)["overall_status"] == "FAILED_VALIDATION"
    request["fx_result"] = {"conversion_status": "SUCCESS", "output_payload_sha256": SHA_D, "base_currency": "USD", "converted_values": [{"instrument_id": "asset-b", "source_currency": "EUR", "target_currency": "USD", "decision_timestamp": "2024-01-02T00:00:00Z", "rate_ages_seconds": 0}]}
    request["expected_child_hashes"]["fx"] = SHA_D
    assert _run(request)["overall_status"] == "PASS"


@pytest.mark.parametrize("case", ["missing", "extra", "duplicate", "unknown", "binding_hash", "snapshot_hash", "source", "normalized", "row_count", "first", "last", "status", "network", "provider", "production", "missing_bars"])
def test_asset_bar_binding_failures_are_fail_closed(case):
    request = _request()
    binding = request["asset_bar_bindings"][0]
    adapter = binding["adapter_result"]
    if case == "missing": request["asset_bar_bindings"].pop()
    elif case == "extra": request["asset_bar_bindings"].append(copy.deepcopy(binding) | {"instrument_id": "extra"})
    elif case == "duplicate": request["asset_bar_bindings"].append(copy.deepcopy(binding))
    elif case == "unknown": binding["fabricated_currency"] = "USD"
    elif case == "binding_hash": binding["adapter_result_sha256"] = SHA_A
    elif case == "snapshot_hash": request["multi_asset_snapshot_result"]["validated_asset_series"][0]["adapter_result_sha256"] = SHA_A
    elif case == "source": adapter["source_sha256"] = SHA_B
    elif case == "normalized": adapter["normalized_rows_hash"] = SHA_A
    elif case == "row_count": adapter["row_count"] = 3
    elif case == "first": adapter["first_timestamp"] = "2023-01-01T00:00:00Z"
    elif case == "last": adapter["last_timestamp"] = "2023-01-01T00:00:00Z"
    elif case == "status": adapter["status"] = "FAILED"
    elif case == "network": adapter["network_used"] = True
    elif case == "provider": adapter["provider_calls_used"] = 1
    elif case == "production": adapter["production_runtime_supported"] = True
    elif case == "missing_bars": adapter["downstream_adapter_result"] = {}
    assert _run(request)["overall_status"] == "FAILED_VALIDATION"


@pytest.mark.parametrize("case", ["duplicate_timestamp", "unordered_timestamp", "bad_ohlc", "nonfinite_close", "negative_volume", "insufficient_rows", "missing_session", "static_universe", "current_membership", "no_overlap", "stale_asset", "return_threshold", "return_beyond", "unknown_field", "repeat_and_immutable"], ids=lambda value: value)
def test_quality_gate_failure_and_review_matrix(case):
    request = _request()
    bars = request["asset_bar_bindings"][0]["adapter_result"]["downstream_adapter_result"]["synthetic_bars"]
    if case == "duplicate_timestamp": bars[1]["timestamp"] = "2024-01-01T00:00:00Z"; expected = "FAILED_VALIDATION"
    elif case == "unordered_timestamp": bars.reverse(); expected = "FAILED_VALIDATION"
    elif case == "bad_ohlc": bars[0]["high"] = 98; expected = "FAILED_VALIDATION"
    elif case == "nonfinite_close": bars[0]["close"] = float("nan"); expected = "FAILED_VALIDATION"
    elif case == "negative_volume": bars[0]["volume"] = -1; expected = "FAILED_VALIDATION"
    elif case == "insufficient_rows": request["thresholds"]["minimum_rows_per_asset"] = 3; expected = "FAILED_VALIDATION"
    elif case == "missing_session": request["calendar_results"][0]["validation_summary"]["missing_sessions"] = ["2024-01-02"]; expected = "FAILED_VALIDATION"
    elif case == "static_universe": request["universe_result"]["validated_universe"]["instruments"][0]["point_in_time_membership_status"] = "EXPLICIT_STATIC_RESEARCH_UNIVERSE"; expected = "REVIEW_REQUIRED"
    elif case == "current_membership": request["universe_result"]["validated_universe"]["instruments"][0]["point_in_time_membership_status"] = "CURRENT_MEMBERSHIP_ONLY"; expected = "FAILED_VALIDATION"
    elif case == "no_overlap": request["asset_bar_bindings"][1]["adapter_result"]["downstream_adapter_result"]["synthetic_bars"] = _asset("x", "X", start="2025-01-01")["bars"]; expected = "FAILED_VALIDATION"
    elif case == "stale_asset":
        stale = _asset("x", "X", start="2023-01-01")["bars"]; request["asset_bar_bindings"][1]["adapter_result"]["downstream_adapter_result"]["synthetic_bars"] = stale; expected = "FAILED_VALIDATION"
    elif case == "return_threshold": bars[1].update(open=150, high=151, low=149, close=150); expected = "FAILED_VALIDATION"
    elif case == "return_beyond": bars[1].update(open=151, high=152, low=150, close=151); expected = "FAILED_VALIDATION"
    elif case == "unknown_field": request["unexpected"] = True;
    elif case == "repeat_and_immutable":
        before = copy.deepcopy(request); first = _run(request); second = _run(request); assert request == before and first == second and len(first["input_sha256"]) == 64; return
    if case == "unknown_field":
        with pytest.raises(ValueError, match="unknown field"): _run(request)
    else: assert _run(request)["overall_status"] == expected
