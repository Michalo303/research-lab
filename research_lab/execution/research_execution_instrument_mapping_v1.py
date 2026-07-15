"""Deterministic review-only research-to-execution identity mapping."""
from __future__ import annotations
import copy, hashlib, json

REQUEST_VERSION="research_execution_instrument_mapping_request_v1"
_TYPES={"SAME_INSTRUMENT_SAME_LISTING","SAME_SECURITY_DIFFERENT_LISTING","ECONOMIC_PROXY","RELATED_EXPOSURE_NOT_IDENTICAL","BENCHMARK_ONLY","NO_EXECUTION_MAPPING"}
_FIELDS={"version","mapping_id","research_instrument_identity_result","execution_instrument_identity_result","mapping_type","economic_exposure","benchmark_relationship","currency_difference","underlying_economic_currency_difference","exchange_calendar_difference","fee_difference","legal_structure_difference","collateral_structure_difference","contango_backwardation_difference","distribution_difference","hedging_difference","corporate_action_difference","listing_identity_difference","benchmark_methodology_difference","futures_roll_difference","tracking_validation_policy","maximum_allowed_tracking_error","minimum_required_correlation","minimum_history_overlap","mapping_as_of_date","provenance"}

def build_research_execution_instrument_mapping(request: dict[str, object]) -> dict[str, object]:
    v=_validate(request); research=v["research_instrument_identity_result"]; execution=v["execution_instrument_identity_result"]
    review=[]
    if v["mapping_type"] in {"NO_EXECUTION_MAPPING","BENCHMARK_ONLY"}: review.append("NO_AUTOMATIC_EXECUTION_MAPPING")
    if execution and execution["execution_route"]["route"] == "IBKR_RETAIL_BLOCKED": review.append("EXECUTION_ROUTE_NOT_AUTOMATED_ELIGIBLE")
    result={"version":"research_execution_instrument_mapping_result_v1","contract_version":"research_execution_instrument_mapping_v1","mapping_id":v["mapping_id"],"mapping_status":"REVIEW_REQUIRED" if review else "PASS","mapping_type":v["mapping_type"],"research_instrument_identity":copy.deepcopy(research["instrument"]),"execution_instrument_identity":copy.deepcopy(execution["instrument"]) if execution else None,"material_differences":{k:v[k] for k in sorted({"currency_difference","underlying_economic_currency_difference","exchange_calendar_difference","fee_difference","legal_structure_difference","collateral_structure_difference","contango_backwardation_difference","distribution_difference","hedging_difference","corporate_action_difference","listing_identity_difference","benchmark_methodology_difference","futures_roll_difference"})},"tracking_requirements":{k:v[k] for k in ("tracking_validation_policy","maximum_allowed_tracking_error","minimum_required_correlation","minimum_history_overlap")},"execution_route":copy.deepcopy(execution["execution_route"]) if execution else None,"automation_allowed":False,"blocking_findings":[],"review_findings":sorted(review),"input_sha256":v["input_sha256"],"provenance":copy.deepcopy(v["provenance"]),"safety_flags":{"provider_calls_used":0,"provider_credentials_accessed":False,"broker_calls_used":0,"broker_credentials_accessed":False,"network_used":False,"automatic_orders_generated":False,"production_runtime_supported":False}}
    result["output_payload_sha256"]=_sha(result); return copy.deepcopy(result)

def _validate(raw):
    if not isinstance(raw,dict) or set(raw)-_FIELDS: raise ValueError("unknown request field")
    if set(raw)!=_FIELDS or raw.get("version")!=REQUEST_VERSION: raise ValueError("invalid request fields")
    v=copy.deepcopy(raw)
    if not isinstance(v["mapping_id"],str) or not v["mapping_id"]: raise ValueError("mapping_id")
    if v["mapping_type"] not in _TYPES: raise ValueError("mapping type")
    r=_child(v["research_instrument_identity_result"]); e=v["execution_instrument_identity_result"]
    if e is not None: e=_child(e)
    if v["mapping_type"] in {"NO_EXECUTION_MAPPING","BENCHMARK_ONLY"}:
        if e is not None: raise ValueError("no-execution mapping requires null execution identity")
    elif e is None: raise ValueError("execution identity required")
    if e and v["mapping_type"]=="SAME_INSTRUMENT_SAME_LISTING" and r["identity_key"]!=e["identity_key"]: raise ValueError("exact identity mismatch")
    if e and v["mapping_type"]=="SAME_SECURITY_DIFFERENT_LISTING" and (r["instrument"]["isin"]!=e["instrument"]["isin"] or r["identity_key"]==e["identity_key"]): raise ValueError("same security requires shared ISIN and different listing")
    if not isinstance(v["tracking_validation_policy"],str) or not v["tracking_validation_policy"]: raise ValueError("tracking policy")
    if not 0 <= v["maximum_allowed_tracking_error"] <= 1: raise ValueError("tracking error")
    if not 0 < v["minimum_required_correlation"] <= 1: raise ValueError("correlation")
    if not isinstance(v["minimum_history_overlap"],int) or v["minimum_history_overlap"]<=0: raise ValueError("history overlap")
    dates=[r["instrument"]["metadata_as_of_date"]]+([e["instrument"]["metadata_as_of_date"]] if e else [])
    if v["mapping_as_of_date"] not in dates or any(v["mapping_as_of_date"]!=x for x in dates): raise ValueError("stale mapping evidence")
    if not isinstance(v["provenance"],dict): raise ValueError("provenance")
    v["input_sha256"]=_sha(v); return v

def _child(value):
    if not isinstance(value,dict) or value.get("validation_status")!="PASS" or not isinstance(value.get("output_payload_sha256"),str): raise ValueError("conflicting identity result")
    payload={k:v for k,v in value.items() if k!="output_payload_sha256"}
    if _sha(payload)!=value["output_payload_sha256"]: raise ValueError("conflicting identity hash")
    return value

def _sha(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
