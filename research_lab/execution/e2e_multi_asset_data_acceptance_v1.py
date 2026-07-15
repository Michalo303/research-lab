"""Deterministic, review-only composition of the multi-asset data contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from research_lab.execution.corporate_actions_contract_v1 import build_corporate_actions_contract
from research_lab.execution.exchange_session_calendar_contract_v1 import build_exchange_session_calendar_contract
from research_lab.execution.local_ohlcv_file_input_adapter_v1 import build_local_ohlcv_file_input_adapter
from research_lab.execution.multi_asset_data_quality_gate_v1 import build_multi_asset_data_quality_gate
from research_lab.execution.multi_asset_immutable_ohlcv_snapshot_contract_v1 import build_multi_asset_immutable_ohlcv_snapshot_contract
from research_lab.execution.point_in_time_fx_conversion_contract_v1 import build_point_in_time_fx_conversion_contract
from research_lab.execution.point_in_time_universe_contract_v1 import build_point_in_time_universe_contract

REQUEST_VERSION = "e2e_multi_asset_data_acceptance_request_v1"
RESULT_VERSION = "e2e_multi_asset_data_acceptance_result_v1"
CONTRACT_VERSION = "e2e_multi_asset_data_acceptance_v1"


def run_e2e_multi_asset_data_acceptance(request: dict[str, object]) -> dict[str, object]:
    """Run the real child contracts in their required fail-closed order."""
    value = _validate(request)
    try:
        universe = build_point_in_time_universe_contract(value["universe_request"])
    except Exception as exc:
        return _failed(value, "UNIVERSE", None, exc)
    instruments = universe["included_instruments"]
    wanted = {item["instrument_id"]: item for item in instruments}
    adapters: list[dict[str, Any]] = []
    supplied = {item["instrument_id"]: item for item in value["local_ohlcv_adapter_requests"]}
    if len(supplied) != len(value["local_ohlcv_adapter_requests"]) or set(supplied) != set(wanted):
        return _failed(value, "LOCAL_OHLCV_ADAPTER", None, ValueError("adapter instruments must exactly match included universe"))
    for iid in sorted(wanted):
        try:
            result = build_local_ohlcv_file_input_adapter(supplied[iid]["request"])
            if result.get("status") != "SUCCESS" or result.get("symbol") != wanted[iid]["provider_symbol"]:
                raise ValueError("adapter identity or status mismatch")
            adapters.append({"instrument_id": iid, "result": result})
        except Exception as exc:
            return _failed(value, "LOCAL_OHLCV_ADAPTER", iid, exc)
    try:
        snapshot_request = _snapshot_request(value, universe, adapters)
        snapshot = build_multi_asset_immutable_ohlcv_snapshot_contract(snapshot_request)
    except Exception as exc:
        return _failed(value, "IMMUTABLE_SNAPSHOT", None, exc)
    calendars = []
    required_calendars = {item["calendar_id"] for item in instruments}
    if set(value["calendar_requests"]) != required_calendars:
        return _failed(value, "CALENDAR", None, ValueError("calendar coverage must exactly match universe"))
    for calendar_id in sorted(required_calendars):
        try:
            result = build_exchange_session_calendar_contract(value["calendar_requests"][calendar_id])
            if result.get("calendar_id") != calendar_id or result.get("validation_status") == "FAILED_VALIDATION":
                raise ValueError("calendar identity or validation mismatch")
            calendars.append(result)
        except Exception as exc:
            return _failed(value, "CALENDAR", calendar_id, exc)
    actions = []
    if set(value["corporate_action_requests"]) != set(wanted):
        return _failed(value, "CORPORATE_ACTIONS", None, ValueError("corporate-action coverage must exactly match universe"))
    for iid in sorted(wanted):
        try:
            result = build_corporate_actions_contract(value["corporate_action_requests"][iid])
            if result.get("instrument_identity", {}).get("instrument_id") != iid or result.get("blocking_findings"):
                raise ValueError("corporate-action identity or validation mismatch")
            actions.append(result)
        except Exception as exc:
            return _failed(value, "CORPORATE_ACTIONS", iid, exc)
    fx = None
    foreign = any(item["currency"] != universe["validated_universe"]["base_currency"] for item in instruments)
    if foreign and value["fx_request"] is None:
        return _failed(value, "FX", None, ValueError("explicit FX request is required for foreign currencies"))
    if value["fx_request"] is not None:
        try:
            fx = build_point_in_time_fx_conversion_contract(value["fx_request"])
            if fx.get("conversion_status") != "SUCCESS": raise ValueError("FX conversion did not succeed")
        except Exception as exc:
            return _failed(value, "FX", None, exc)
    try:
        gate = build_multi_asset_data_quality_gate(_quality_request(value, universe, snapshot, adapters, calendars, actions, fx))
    except Exception as exc:
        return _failed(value, "QUALITY_GATE", None, exc)
    status = {"PASS": "ACCEPTED_REVIEW_ONLY", "REVIEW_REQUIRED": "REVIEW_REQUIRED", "FAILED_VALIDATION": "FAILED_VALIDATION"}.get(gate["overall_status"], "FAILED_VALIDATION")
    result = _result(value, status, "QUALITY_GATE" if status == "FAILED_VALIDATION" else None, universe, adapters, snapshot, calendars, actions, fx, gate, [])
    return result


def replay_e2e_multi_asset_data_acceptance(request: dict[str, object], original_result: dict[str, object]) -> dict[str, object]:
    """Rerun without mutation and compare full semantic lineage plus payload hash."""
    before_request, before_result = copy.deepcopy(request), copy.deepcopy(original_result)
    rerun = run_e2e_multi_asset_data_acceptance(request)
    category = "REPLAY_MATCH"
    stage = None
    if rerun.get("acceptance_status") == "FAILED_VALIDATION": category, stage = "REPLAY_FAILED_VALIDATION", rerun.get("failed_stage")
    elif rerun.get("exact_child_lineage") != original_result.get("exact_child_lineage"): category, stage = "REPLAY_MISMATCH", "CHILD_LINEAGE"
    elif rerun.get("output_payload_sha256") != original_result.get("output_payload_sha256"): category, stage = "REPLAY_MISMATCH", "OUTPUT_PAYLOAD"
    if request != before_request or original_result != before_result: raise RuntimeError("replay must not mutate caller objects")
    payload = {"version": "e2e_multi_asset_data_acceptance_replay_result_v1", "replay_status": category, "failed_stage": stage, "rerun_output_payload_sha256": rerun.get("output_payload_sha256"), "original_output_payload_sha256": original_result.get("output_payload_sha256")}
    payload["output_payload_sha256"] = _sha(payload); return payload


def _snapshot_request(v, universe, adapters):
    assets=[]
    for item in adapters:
        iid, adapter = item["instrument_id"], item["result"]; instrument=next(x for x in universe["included_instruments"] if x["instrument_id"] == iid)
        assets.append({"instrument_id":iid,"provider_symbol":instrument["provider_symbol"],"dataset_id":adapter["dataset_id"],"adapter_result":adapter,"source_artifact_sha256":adapter["source_sha256"],"normalized_bars_sha256":adapter["normalized_rows_hash"],"row_count":adapter["row_count"],"first_timestamp":adapter["first_timestamp"],"last_timestamp":adapter["last_timestamp"],"adjustment_status":instrument["corporate_action_policy_id"],"provenance":v["provenance"]})
    return {"version":"multi_asset_immutable_ohlcv_snapshot_contract_request_v1","snapshot_id":v["snapshot_metadata"]["snapshot_id"],"universe_result":universe,"asset_inputs":assets,"expected_asset_identities":[{k:a[k] for k in ("instrument_id","provider_symbol","dataset_id")} for a in assets],"expected_source_hashes":{a["instrument_id"]:a["source_artifact_sha256"] for a in assets},"expected_normalized_row_hashes":{a["instrument_id"]:a["normalized_bars_sha256"] for a in assets},"alignment_policy":v["snapshot_metadata"]["alignment_policy"],"missing_session_policy":v["snapshot_metadata"]["missing_session_policy"],"created_at":v["as_of_timestamp"],"output_dir":None,"provenance":v["provenance"]}


def _quality_request(v, universe, snapshot, adapters, calendars, actions, fx):
    bindings=[]
    for x in adapters:
        adapter=x["result"]; bindings.append({"instrument_id":x["instrument_id"],"adapter_result":adapter,"adapter_result_sha256":_sha(adapter),"provenance":v["provenance"]})
    calendar_map={x["calendar_id"]:x for x in calendars}
    calendar_bindings=[{"instrument_id":asset["instrument_id"],"calendar_id":asset["calendar_id"],"result":calendar_map[asset["calendar_id"]]} for asset in universe["included_instruments"]]
    action_bindings=[{"instrument_id":x["instrument_identity"]["instrument_id"],"result":x} for x in actions]
    hashes={"universe":universe["output_payload_sha256"],"snapshot":snapshot["output_payload_sha256"],"calendars":{x["instrument_id"]:x["result"]["output_payload_sha256"] for x in calendar_bindings},"corporate_actions":{x["instrument_id"]:x["result"]["output_payload_sha256"] for x in action_bindings}}
    if fx is not None: hashes["fx"]=fx["output_payload_sha256"]
    return {"version":"multi_asset_data_quality_gate_request_v1","quality_gate_id":v["acceptance_id"],"universe_result":universe,"multi_asset_snapshot_result":snapshot,"asset_bar_bindings":bindings,"calendar_results":calendar_bindings,"corporate_action_results":action_bindings,"fx_result":fx,"thresholds":v["quality_gate_thresholds"],"policies":v["quality_gate_policies"],"expected_child_hashes":hashes,"as_of_timestamp":v["as_of_timestamp"],"provenance":v["provenance"]}


def _failed(v, stage, child, exc):
    evidence={"stage":stage,"child_identity":child,"instrument_id":child if stage in {"LOCAL_OHLCV_ADAPTER","CORPORATE_ACTIONS"} else None,"error_category":type(exc).__name__,"expected_identity_or_hash":None,"observed_identity_or_hash":None,"message":str(exc)}
    return _result(v,"FAILED_VALIDATION",stage,None,[],None,[],[],None,None,[evidence])


def _result(v,status,failed,universe,adapters,snapshot,calendars,actions,fx,gate,findings):
    lineage={"universe": universe.get("output_payload_sha256") if universe else None,"adapters":{x["instrument_id"]:_sha(x["result"]) for x in sorted(adapters,key=lambda x:x["instrument_id"])},"snapshot":snapshot.get("output_payload_sha256") if snapshot else None,"calendars":{x["calendar_id"]:x["output_payload_sha256"] for x in sorted(calendars,key=lambda x:x["calendar_id"])},"corporate_actions":{x["instrument_identity"]["instrument_id"]:x["output_payload_sha256"] for x in sorted(actions,key=lambda x:x["instrument_identity"]["instrument_id"])},"fx":fx.get("output_payload_sha256") if fx else None,"quality_gate":gate.get("output_payload_sha256") if gate else None}
    result={"version":RESULT_VERSION,"contract_version":CONTRACT_VERSION,"acceptance_id":v["acceptance_id"],"acceptance_status":status,"failed_stage":failed,"universe_result":universe,"local_adapter_results":[x["result"] for x in adapters],"multi_asset_snapshot_result":snapshot,"calendar_results":calendars,"corporate_action_results":actions,"fx_result":fx,"quality_gate_result":gate,"exact_child_lineage":lineage,"stage_statuses":{},"blocking_findings":findings if findings else (gate.get("blocking_findings",[]) if gate else []),"review_findings":gate.get("review_findings",[]) if gate else [],"warnings":[],"replay_eligibility":status != "FAILED_VALIDATION","input_sha256":v["input_sha256"],"safety_flags":{"provider_calls_used":0,"network_used":False,"external_data_used":False,"broker_actions_used":0,"paper_trading_performed":False,"registry_write_performed":False,"deployment_performed":False,"promotion_performed":False,"generated_code_executed":False,"automatic_strategy_application_performed":False,"production_runtime_supported":False,"filesystem_writes_performed":False,"local_input_reads_performed":bool(adapters)},"provenance":v["provenance"]}
    result["output_payload_sha256"]=_sha(result); return result


def _validate(raw):
    if not isinstance(raw,dict): raise ValueError("request must be an object")
    allowed={"version","acceptance_id","universe_request","local_ohlcv_adapter_requests","snapshot_metadata","calendar_requests","corporate_action_requests","fx_request","quality_gate_thresholds","quality_gate_policies","expected_identities","expected_child_hashes","replay_policy","as_of_timestamp","provenance"}
    unknown=set(raw)-allowed
    if unknown: raise ValueError("unknown top-level request field")
    if raw.get("version") != REQUEST_VERSION: raise ValueError("version")
    required=("acceptance_id","universe_request","local_ohlcv_adapter_requests","snapshot_metadata","calendar_requests","corporate_action_requests","quality_gate_thresholds","quality_gate_policies","as_of_timestamp","provenance")
    if any(k not in raw for k in required): raise ValueError("missing required request field")
    v=copy.deepcopy(raw)
    if not isinstance(v["acceptance_id"],str) or not v["acceptance_id"]: raise ValueError("acceptance_id")
    if not isinstance(v["local_ohlcv_adapter_requests"],list): raise ValueError("local_ohlcv_adapter_requests")
    for x in v["local_ohlcv_adapter_requests"]:
        if not isinstance(x,dict) or set(x)!={"instrument_id","request"}: raise ValueError("adapter request entry")
    for key in ("calendar_requests","corporate_action_requests","snapshot_metadata","quality_gate_thresholds","quality_gate_policies","provenance","universe_request"):
        if not isinstance(v[key],dict): raise ValueError(key)
    if set(v["snapshot_metadata"]) != {"snapshot_id","alignment_policy","missing_session_policy"}: raise ValueError("snapshot_metadata")
    v["input_sha256"]=_sha(v); return v


def _sha(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
