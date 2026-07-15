from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from research_lab.execution.e2e_multi_asset_data_acceptance_v1 import (
    replay_e2e_multi_asset_data_acceptance,
    run_e2e_multi_asset_data_acceptance,
)


def test_unknown_top_level_field_is_rejected_without_side_effects():
    request = {"version": "e2e_multi_asset_data_acceptance_request_v1", "unexpected": True}
    before = copy.deepcopy(request)
    with pytest.raises(ValueError, match="unknown"):
        run_e2e_multi_asset_data_acceptance(request)
    assert request == before


def _sha(value):
    encoded = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _request(tmp_path: Path, *, static=False):
    rows = [{"timestamp": "2024-01-02T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}, {"timestamp": "2024-01-03T00:00:00Z", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 11}]
    instruments=[]; adapters=[]; actions={}
    for iid, symbol in (("asset-a", "AAA.US"), ("asset-b", "BBB.US")):
        path=tmp_path/f"{iid}.json"; path.write_text(json.dumps(rows), encoding="utf-8")
        source=_sha(path.read_bytes()); policy="SPLIT_ADJUSTED_ONLY"
        instruments.append({"instrument_id":iid,"provider":"synthetic","provider_symbol":symbol,"display_symbol":symbol,"instrument_type":"ETF","currency":"USD","market_venue_group":"US","calendar_id":"XNYS_SYNTHETIC","active_from":"2020-01-01T00:00:00Z","active_to":None,"membership_from":"2020-01-01T00:00:00Z","membership_to":None,"point_in_time_membership_status":"EXPLICIT_STATIC_RESEARCH_UNIVERSE" if static else "POINT_IN_TIME_VERIFIED","lot_size":1,"price_precision":2,"corporate_action_policy_id":policy,"source_sha256":"a"*64,"provenance":{"source":"test"}})
        adapters.append({"instrument_id":iid,"request":{"version":"local_ohlcv_file_input_adapter_request_v1","file_path":str(path),"format":"json","dataset_id":f"dataset-{iid}","symbol":symbol,"exchange":"XNYS","timezone":"UTC","expected_sha256":source,"max_bytes":10000,"max_rows":10,"provenance":{"source":"test"}}})
        actions[iid]={"version":"corporate_actions_contract_request_v1","corporate_actions_id":f"actions-{iid}","instrument_identity":{"instrument_id":iid,"provider_symbol":symbol},"adjustment_policy":policy,"actions":[{"action_id":f"none-{iid}","instrument_id":iid,"action_type":"NO_ACTIONS_DECLARED","announcement_timestamp":None,"availability_timestamp":"2024-01-01T00:00:00Z","ex_timestamp":None,"effective_timestamp":None,"record_timestamp":None,"payment_timestamp":None,"factor":None,"amount":None,"currency":None,"predecessor_symbol":None,"successor_symbol":None,"source_identity":"actions","source_sha256":"b"*64,"point_in_time_status":"POINT_IN_TIME_VERIFIED","provenance":{"source":"test"}}],"expected_price_series_identity":{"price_series_id":f"prices-{iid}","adjustment_basis":"SPLIT_ADJUSTED","source_sha256":"c"*64},"expected_source_hashes":{"actions":"b"*64},"as_of_timestamp":"2024-01-03T00:00:00Z","provenance":{"source":"test"}}
    calendar_bars=[{"bar_id":f"calendar-{d}","session_date":d,"source_sha256":"e"*64,"provenance":{"source":"test"}} for d in ("2024-01-02","2024-01-03")]
    calendar={"version":"exchange_session_calendar_contract_request_v1","calendar_id":"XNYS_SYNTHETIC","calendar_version":"v1","timezone":"America/New_York","sessions":[{"session_date":d,"session_type":"REGULAR","open_timestamp":f"{d}T09:30:00","close_timestamp":f"{d}T16:00:00","source_identity":"calendar","source_sha256":"d"*64,"provenance":{"source":"test"}} for d in ("2024-01-02","2024-01-03")],"holiday_policy":"EXPLICIT_CLOSED_SESSIONS","partial_session_policy":"ALLOW_EXPLICIT_PARTIAL","validation_request":{"instrument_id":"asset-a","calendar_id":"XNYS_SYNTHETIC","bars":calendar_bars,"bar_interval":"P1D","bar_timestamp_semantics":"SESSION_DATE","expected_bars_source_sha256":_sha(calendar_bars),"expected_normalized_bars_sha256":_sha([{key:item[key] for key in sorted(item)} for item in calendar_bars]),"coverage_start":"2024-01-02","coverage_end":"2024-01-03","missing_session_policy":"FAIL_ON_ANY_MISSING","unexpected_session_policy":"FAIL_ON_ANY_UNEXPECTED","provenance":{"source":"test"}},"provenance":{"source":"test"}}
    return {"version":"e2e_multi_asset_data_acceptance_request_v1","acceptance_id":"acceptance-1","universe_request":{"version":"point_in_time_universe_contract_request_v1","universe_id":"universe-1","universe_version":"v1","as_of_timestamp":"2024-01-03T00:00:00Z","membership_policy":{"allow_unsafe_current_membership":False,"allow_not_point_in_time_safe":False,"unsafe_policy_label":"review-only"},"base_currency":"USD","instruments":instruments,"provenance":{"source":"test"}},"local_ohlcv_adapter_requests":adapters,"snapshot_metadata":{"snapshot_id":"snapshot-1","alignment_policy":"INDEPENDENT_SERIES","missing_session_policy":"ALLOW_MISSING"},"calendar_requests":{"XNYS_SYNTHETIC":calendar},"corporate_action_requests":actions,"fx_request":None,"quality_gate_thresholds":{"minimum_rows_per_asset":2,"minimum_common_overlap_days":1,"maximum_end_staleness_days":1,"maximum_start_delay_days":1,"maximum_missing_session_count":0,"maximum_missing_session_ratio":0.0,"maximum_unexpected_session_count":0,"maximum_single_period_return_abs":0.5,"split_candidate_tolerance":0.05,"volume_integrality_required":True},"quality_gate_policies":{"static_universe_requires_review":True,"allow_unsafe_current_membership":False,"not_point_in_time_safe_requires_failure":True,"missing_session_severity":"FAIL","unexpected_session_severity":"FAIL"},"expected_identities":{},"expected_child_hashes":{},"replay_policy":{"mode":"RERUN"},"as_of_timestamp":"2024-01-03T00:00:00Z","provenance":{"source":"test"}}


def test_two_asset_composition_is_review_only_deterministic_and_replayable(tmp_path):
    request=_request(tmp_path); before=copy.deepcopy(request)
    result=run_e2e_multi_asset_data_acceptance(request)
    assert result["acceptance_status"] == "ACCEPTED_REVIEW_ONLY"
    assert result["failed_stage"] is None and result["safety_flags"]["provider_calls_used"] == 0
    assert request == before
    assert replay_e2e_multi_asset_data_acceptance(request, result)["replay_status"] == "REPLAY_MATCH"


def test_static_universe_quality_review_is_not_upgraded(tmp_path):
    assert run_e2e_multi_asset_data_acceptance(_request(tmp_path, static=True))["acceptance_status"] == "REVIEW_REQUIRED"


def test_duplicate_adapter_identity_fails_closed_at_adapter_stage(tmp_path):
    request = _request(tmp_path)
    request["local_ohlcv_adapter_requests"].append(copy.deepcopy(request["local_ohlcv_adapter_requests"][0]))

    result = run_e2e_multi_asset_data_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == "LOCAL_OHLCV_ADAPTER"


def test_changed_explicit_local_file_fails_deterministic_replay(tmp_path):
    request = _request(tmp_path)
    original = run_e2e_multi_asset_data_acceptance(request)
    path = Path(request["local_ohlcv_adapter_requests"][0]["request"]["file_path"])
    path.write_text("[]", encoding="utf-8")

    replay = replay_e2e_multi_asset_data_acceptance(request, original)

    assert replay["replay_status"] == "REPLAY_FAILED_VALIDATION"
    assert replay["failed_stage"] == "LOCAL_OHLCV_ADAPTER"


def test_missing_calendar_is_exact_calendar_stage_failure(tmp_path):
    request = _request(tmp_path)
    request["calendar_requests"] = {}

    result = run_e2e_multi_asset_data_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == "CALENDAR"


@pytest.mark.parametrize(
    ("mutator", "stage"),
    [
        (lambda request: request["universe_request"].update(version="bad"), "UNIVERSE"),
        (lambda request: request["local_ohlcv_adapter_requests"][0]["request"].update(expected_sha256="0" * 64), "LOCAL_OHLCV_ADAPTER"),
        (lambda request: request.update(corporate_action_requests={}), "CORPORATE_ACTIONS"),
        (lambda request: request["quality_gate_thresholds"].update(minimum_rows_per_asset=3), "QUALITY_GATE"),
    ],
)
def test_real_child_failures_are_not_presented_as_acceptance(tmp_path, mutator, stage):
    request = _request(tmp_path)
    mutator(request)

    result = run_e2e_multi_asset_data_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == stage
    assert result["replay_eligibility"] is False
