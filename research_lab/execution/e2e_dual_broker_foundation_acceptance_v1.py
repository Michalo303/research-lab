"""Deterministic review-only acceptance built from child requests, never results."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any

from research_lab.execution.dual_broker_exposure_risk_v1 import build_dual_broker_exposure_risk
from research_lab.execution.fio_manual_long_term_inventory_v1 import build_fio_manual_long_term_inventory
from research_lab.execution.ibkr_active_execution_universe_v1 import build_ibkr_active_execution_universe
from research_lab.execution.instrument_identity_execution_routing_v1 import build_instrument_identity_execution_routing
from research_lab.execution.point_in_time_fx_conversion_contract_v1 import build_point_in_time_fx_conversion_contract
from research_lab.execution.research_execution_instrument_mapping_v1 import build_research_execution_instrument_mapping

REQUEST_VERSION="e2e_dual_broker_foundation_acceptance_request_v1"
RESULT_VERSION="e2e_dual_broker_foundation_acceptance_result_v1"
CONTRACT_VERSION="e2e_dual_broker_foundation_acceptance_v1"
_FIELDS={"version","acceptance_request_id","as_of_timestamp","base_currency","instrument_identity_requests","research_execution_mapping_requests","fio_inventory_request","ibkr_universe_request","fx_conversion_requests","risk_request_inputs","expected_lineage","replay_policy","provenance"}
_STAGES=("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING","FIO_INVENTORY","IBKR_UNIVERSE","FX","DUAL_BROKER_RISK","ACCEPTANCE_VALIDATION","REPLAY_VALIDATION")
_SAFE={"provider_calls_used":0,"provider_credentials_accessed":False,"broker_calls_used":0,"broker_credentials_accessed":False,"Fio_actions_performed":False,"IBKR_actions_performed":False,"filesystem_writes_performed":False,"paper_trading_performed":False,"live_trading_performed":False,"executable_orders_generated":False,"automatic_liquidation_performed":False,"automatic_capital_allocation_performed":False,"deployment_performed":False,"registry_write_performed":False,"production_runtime_supported":False}
_RISK_INPUT_FIELDS={"risk_request_id","as_of_timestamp","base_currency","existing_ibkr_positions","proposed_ibkr_intents","valuation_evidence","concentration_classifications","risk_limits","provenance"}

def build_e2e_dual_broker_foundation_acceptance(request: dict[str, object]) -> dict[str, object]:
    value=_validate(request)
    first=_compose(value)
    if first["acceptance_status"]=="FAILED_VALIDATION": return first
    if value.get("_replay_policy_error"):
        return _replay_failed(first, value, value["_replay_policy_error"])
    replay=_compose(value)
    comparable=lambda x:{k:v for k,v in x.items() if k not in {"replay_status","replay_request_sha256","replay_result_sha256","output_payload_sha256"}}
    first["replay_request_sha256"]=_sha(value)
    first["replay_result_sha256"]=_sha(comparable(replay))
    expected=value["replay_policy"].get("expected_replay_hash")
    first["replay_status"]="REPLAY_MATCH" if comparable(first)==comparable(replay) and (expected is None or expected==first["replay_result_sha256"]) else "REPLAY_MISMATCH"
    first["stage_results"].append({"stage":"REPLAY_VALIDATION","status":"PASS" if first["replay_status"]=="REPLAY_MATCH" else "FAILED"})
    first["output_payload_sha256"]=_sha(first)
    return copy.deepcopy(first)

def _compose(value: dict[str, Any]) -> dict[str, Any]:
    try:
        identities=[build_instrument_identity_execution_routing(x) for x in value["instrument_identity_requests"]]
        if not identities or any(not _hash_ok(x,"instrument_identity_execution_routing_v1") for x in identities): raise ValueError("IDENTITY_INVALID")
    except Exception as exc:
        message=str(exc).lower()
        stage="EXECUTION_ROUTING" if any(term in message for term in ("route","automation","eligibility")) else "INSTRUMENT_IDENTITY"
        return _failed(value,stage,str(exc))
    by_id={x["instrument"]["instrument_id"]:x for x in identities}
    try:
        mappings=[]
        for wrapper in value["research_execution_mapping_requests"]:
            raw=copy.deepcopy(wrapper["mapping_request"])
            raw["research_instrument_identity_result"]=by_id[wrapper["research_instrument_id"]]
            raw["execution_instrument_identity_result"]=by_id.get(wrapper["execution_instrument_id"]) if wrapper["execution_instrument_id"] else None
            mappings.append(build_research_execution_instrument_mapping(raw))
        if any(not _hash_ok(x,"research_execution_instrument_mapping_v1") for x in mappings): raise ValueError("MAPPING_INVALID")
    except Exception as exc: return _failed(value,"RESEARCH_EXECUTION_MAPPING",str(exc),("INSTRUMENT_IDENTITY","EXECUTION_ROUTING"))
    try:
        fio=build_fio_manual_long_term_inventory(value["fio_inventory_request"])
        if not _hash_ok(fio,"fio_manual_long_term_inventory_v1") or fio["status"] not in {"PASS","REVIEW_REQUIRED"}: raise ValueError("FIO_INVALID")
        for position in fio["validated_positions"]:
            route=position["identity_routing_result"]["execution_route"]
            if not route["manual_only"] or route["automatic_liquidation_allowed"] or route["automatic_order_generation_allowed"]: raise ValueError("FIO_MANUAL_ONLY_EVIDENCE_INVALID")
    except Exception as exc: return _failed(value,"FIO_INVENTORY",str(exc),("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING"))
    try:
        universe_raw=copy.deepcopy(value["ibkr_universe_request"])
        for candidate in universe_raw["candidates"]:
            instrument_id=candidate["identity_routing_result"]["instrument"]["instrument_id"]
            candidate["identity_routing_result"]=by_id[instrument_id]
        universe=build_ibkr_active_execution_universe(universe_raw)
        if not _hash_ok(universe,"ibkr_active_execution_universe_v1") or universe["status"]=="FAILED_VALIDATION": raise ValueError("IBKR_INVALID")
    except Exception as exc: return _failed(value,"IBKR_UNIVERSE",str(exc),("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING","FIO_INVENTORY"))
    try:
        fx=[build_point_in_time_fx_conversion_contract(x) for x in value["fx_conversion_requests"]]
        if any(not _hash_ok(x,"point_in_time_fx_conversion_contract_v1") or x["conversion_status"]!="SUCCESS" for x in fx): raise ValueError("FX_INVALID")
    except Exception as exc: return _failed(value,"FX",str(exc),("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING","FIO_INVENTORY","IBKR_UNIVERSE"))
    try:
        risk={"version":"dual_broker_exposure_risk_request_v1",**copy.deepcopy(value["risk_request_inputs"]),"fio_inventory_result":fio,"ibkr_universe_result":universe,"research_execution_mapping_results":mappings,"point_in_time_fx_conversion_results":fx}
        risk_result=build_dual_broker_exposure_risk(risk)
        if not _hash_ok(risk_result,"dual_broker_exposure_risk_v1"): raise ValueError("RISK_INVALID")
    except Exception as exc: return _failed(value,"DUAL_BROKER_RISK",str(exc),("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING","FIO_INVENTORY","IBKR_UNIVERSE","FX"))
    if risk_result["status"]=="FAILED_VALIDATION":
        return _failed(value,"DUAL_BROKER_RISK","M31E_FAILED_VALIDATION",("INSTRUMENT_IDENTITY","EXECUTION_ROUTING","RESEARCH_EXECUTION_MAPPING","FIO_INVENTORY","IBKR_UNIVERSE","FX"))
    lineage={"m31a":[{"input_sha256":x["input_sha256"],"output_payload_sha256":x["output_payload_sha256"]} for x in identities],"m31b":[{"input_sha256":x["input_sha256"],"output_payload_sha256":x["output_payload_sha256"]} for x in mappings],"m31c":{"input_sha256":fio["input_sha256"],"source_sha256":fio["source_sha256"],"output_payload_sha256":fio["output_payload_sha256"]},"m31d":{"input_sha256":universe["input_sha256"],"output_payload_sha256":universe["output_payload_sha256"]},"fx":[{"input_sha256":x["input_sha256"],"output_payload_sha256":x["output_payload_sha256"]} for x in fx],"m31e":{"input_sha256":risk_result["input_sha256"],"output_payload_sha256":risk_result["output_payload_sha256"]},"stage_order":list(_STAGES[:-1])}
    if value["expected_lineage"] and value["expected_lineage"]!=lineage: return _failed(value,"ACCEPTANCE_VALIDATION","EXPECTED_LINEAGE_MISMATCH",_STAGES[:7])
    status="FAILED_VALIDATION" if risk_result["status"]=="FAILED_VALIDATION" else "REVIEW_REQUIRED" if risk_result["status"]=="REVIEW_REQUIRED" or universe["status"]=="REVIEW_REQUIRED" or any(x["mapping_status"]=="REVIEW_REQUIRED" for x in mappings) else "ACCEPTED_REVIEW_ONLY"
    result={"version":RESULT_VERSION,"contract_version":CONTRACT_VERSION,"acceptance_request_id":value["acceptance_request_id"],"as_of_timestamp":value["as_of_timestamp"],"base_currency":value["base_currency"],"acceptance_status":status,"failed_stage":None,"stage_results":[{"stage":x,"status":"PASS"} for x in _STAGES[:-1]],"child_lineage":lineage,"fx_lineage":copy.deepcopy(risk_result["fx_conversion_lineage"]),"mapping_lineage":copy.deepcopy(risk_result["mapping_lineage"]),"fio_manual_only_evidence":{"manual_only":True,"automatic_liquidation_performed":False},"ibkr_eligibility_evidence":copy.deepcopy(universe["instrument_results"]),"m31e_decision_evidence":copy.deepcopy(risk_result),"review_findings":sorted(set(risk_result["findings"])),"acceptance_findings":[],"input_sha256":value["input_sha256"],"provenance":copy.deepcopy(value["provenance"]),"safety_fields":copy.deepcopy(_SAFE)}
    result["acceptance_payload_sha256"]=_sha(result)
    return result

def _validate(raw: Any)->dict[str,Any]:
    if not isinstance(raw,dict) or set(raw)!=_FIELDS:
        if isinstance(raw,dict) and {"identity_routing_results","fio_inventory_result","ibkr_universe_result"}&set(raw): raise ValueError("obsolete supplied-child-results request is rejected")
        raise ValueError("unknown, missing, or invalid request field")
    if raw.get("version")!=REQUEST_VERSION: raise ValueError("invalid version")
    v=copy.deepcopy(raw)
    if not all(isinstance(v[x],str) and v[x] for x in ("acceptance_request_id","as_of_timestamp","base_currency")): raise ValueError("invalid acceptance identity")
    datetime.fromisoformat(v["as_of_timestamp"].replace("Z","+00:00"))
    if not all(isinstance(v[x],list) for x in ("instrument_identity_requests","research_execution_mapping_requests","fx_conversion_requests")): raise ValueError("request collections must be lists")
    if not all(isinstance(v[x],dict) for x in ("fio_inventory_request","ibkr_universe_request","risk_request_inputs","expected_lineage","replay_policy","provenance")): raise ValueError("invalid acceptance evidence")
    if set(v["risk_request_inputs"])!=_RISK_INPUT_FIELDS: raise ValueError("risk request inputs must not contain child results")
    policy=v["replay_policy"]
    if set(policy)-{"mode","expected_replay_hash"} or policy.get("mode")!="VERIFY_DETERMINISTIC": raise ValueError("invalid replay policy")
    v["input_sha256"]=_sha(v)
    if "expected_replay_hash" in policy and (not isinstance(policy["expected_replay_hash"],str) or len(policy["expected_replay_hash"])!=64 or any(ch not in "0123456789abcdef" for ch in policy["expected_replay_hash"])):
        v["_replay_policy_error"]="MALFORMED_EXPECTED_REPLAY_HASH"
    return v

def _hash_ok(value:dict[str,Any],contract:str)->bool:
    payload=copy.deepcopy(value); declared=payload.pop("output_payload_sha256",None)
    return value.get("contract_version")==contract and isinstance(declared,str) and _sha(payload)==declared

def _failed(v:dict[str,Any],stage:str,finding:str,completed:tuple[str,...]=())->dict[str,Any]:
    r={"version":RESULT_VERSION,"contract_version":CONTRACT_VERSION,"acceptance_request_id":v["acceptance_request_id"],"as_of_timestamp":v["as_of_timestamp"],"base_currency":v["base_currency"],"acceptance_status":"FAILED_VALIDATION","failed_stage":stage,"stage_results":[{"stage":x,"status":"PASS" if x in completed else "FAILED" if x==stage else "NOT_RUN"} for x in _STAGES],"child_lineage":{},"fx_lineage":[],"mapping_lineage":[],"fio_manual_only_evidence":{},"ibkr_eligibility_evidence":[],"m31e_decision_evidence":None,"review_findings":[],"acceptance_findings":[finding],"replay_status":"REPLAY_FAILED_VALIDATION","input_sha256":v["input_sha256"],"provenance":copy.deepcopy(v["provenance"]),"safety_fields":copy.deepcopy(_SAFE)}; r["acceptance_payload_sha256"]=_sha(r); r["output_payload_sha256"]=_sha(r); return r

def _replay_failed(result:dict[str,Any],value:dict[str,Any],finding:str)->dict[str,Any]:
    failed=copy.deepcopy(result)
    failed["acceptance_status"]="FAILED_VALIDATION"
    failed["failed_stage"]="REPLAY_VALIDATION"
    failed["stage_results"].append({"stage":"REPLAY_VALIDATION","status":"FAILED"})
    failed["acceptance_findings"].append(finding)
    failed["replay_status"]="REPLAY_FAILED_VALIDATION"
    failed["replay_request_sha256"]=_sha(value)
    failed["acceptance_payload_sha256"]=_sha({k:v for k,v in failed.items() if k not in {"acceptance_payload_sha256","output_payload_sha256"}})
    failed["output_payload_sha256"]=_sha(failed)
    return failed

def _sha(value:object)->str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False,default=str).encode()).hexdigest()
