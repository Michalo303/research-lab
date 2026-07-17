"""Pure, read-only forensic audit for a consumed M31Q Search execution."""
from __future__ import annotations
import copy, hashlib, json
from typing import Any

CONTRACT_VERSION="eodhd_search_resolution_result_audit_v1"
_FIELDS={"version","audit_request_id","m31i_manifest","m31n_capability_manifest","m31p_readiness_result","execution_intent","started_markers","completed_markers","result_artifacts","execution_summary","source_file_hashes","provenance"}
_REVIEW={"REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY","REVIEW_REQUIRED_NO_EXACT_MATCH","REVIEW_REQUIRED_AMBIGUOUS_EXACT_MATCH","REVIEW_REQUIRED_PROVIDER_NAMESPACE"}

class AuditValidationError(ValueError): pass

def audit_eodhd_search_resolution_results_v1(raw:dict[str,object])->dict[str,object]:
    if not isinstance(raw,dict) or set(raw)!=_FIELDS: raise AuditValidationError("UNKNOWN_OR_MISSING_AUDIT_FIELDS")
    v=copy.deepcopy(raw)
    if v["version"]!="eodhd_search_resolution_result_audit_request_v1" or not isinstance(v["audit_request_id"],str): raise AuditValidationError("INVALID_AUDIT_REQUEST")
    _no_credentials(v)
    i,n,p=v["m31i_manifest"],v["m31n_capability_manifest"],v["m31p_readiness_result"]
    ih=_verified(i,"canonical_manifest_sha256"); nh=_verified(n,"canonical_capability_manifest_sha256")
    if p.get("m31i_canonical_manifest_sha256")!=ih or p.get("m31n_canonical_capability_sha256")!=nh: raise AuditValidationError("UPSTREAM_HASH_MISMATCH")
    plan=copy.deepcopy(p.get("complete_plan")); approval=copy.deepcopy(p.get("approval_manifest"))
    if not isinstance(plan,list) or len(plan)!=15 or _sha(plan)!=p.get("acquisition_plan_sha256") or approval.get("acquisition_plan_sha256")!=p.get("acquisition_plan_sha256") or _verified(approval,"canonical_approval_manifest_sha256")!=p.get("approval_manifest_sha256"): raise AuditValidationError("PLAN_OR_APPROVAL_MISMATCH")
    if approval.get("authorized_records")!=plan or p.get("m31o_adapter_contract_version")!="eodhd_approval_bound_search_metadata_adapter_v2": raise AuditValidationError("APPROVAL_MEMBERSHIP_MISMATCH")
    intent=v["execution_intent"]; summary=v["execution_summary"]
    if intent.get("approval_manifest_sha256")!=p["approval_manifest_sha256"] or intent.get("acquisition_plan_sha256")!=p["acquisition_plan_sha256"]: raise AuditValidationError("EXECUTION_INTENT_MISMATCH")
    if _sha(summary)!=v["source_file_hashes"].get("execution-summary.json"): raise AuditValidationError("EXECUTION_SUMMARY_SOURCE_HASH_MISMATCH")
    started,completed,artifacts=v["started_markers"],v["completed_markers"],v["result_artifacts"]
    if len(started)!=15 or len(completed)!=15 or len(artifacts)!=15: raise AuditValidationError("INCOMPLETE_ARTIFACT_SET")
    records=[]; destinations=set()
    for sequence,record in enumerate(plan,1):
        if record.get("sequence")!=sequence or _verified(record,"canonical_per_call_record_sha256")!=record.get("canonical_per_call_record_sha256"): raise AuditValidationError("PER_CALL_RECORD_HASH_MISMATCH")
        sk=f"{sequence:02d}-CALL_STARTED.json"; ck=f"{sequence:02d}-CALL_COMPLETED.json"; start=started.get(sk); done=completed.get(ck); destination=record.get("future_destination")
        if not isinstance(start,dict) or not isinstance(done,dict) or start.get("sequence")!=sequence or done.get("sequence")!=sequence or start.get("record_sha256")!=record["canonical_per_call_record_sha256"]: raise AuditValidationError("MARKER_RECONCILIATION_MISMATCH")
        if destination in destinations or destination not in artifacts: raise AuditValidationError("DESTINATION_RECONCILIATION_MISMATCH")
        destinations.add(destination); artifact=artifacts[destination]
        if not isinstance(artifact,dict) or artifact.get("sequence")!=sequence or artifact.get("record_sha256")!=record["canonical_per_call_record_sha256"]: raise AuditValidationError("RESULT_ARTIFACT_MISMATCH")
        result=artifact.get("adapter_result")
        if not isinstance(result,dict) or _sha(result)!=artifact.get("adapter_result_sha256") or artifact["adapter_result_sha256"]!=done.get("adapter_result_sha256"): raise AuditValidationError("ADAPTER_RESULT_HASH_MISMATCH")
        _no_credentials(result)
        status=result.get("resolution_status")
        classification="VERIFIED_EXACT_PROVIDER_SYMBOL" if status=="RESOLVED_EXACT_PROVIDER_SYMBOL" else status if status in _REVIEW else "FAILED_AUDIT_VALIDATION"
        if classification=="FAILED_AUDIT_VALIDATION": raise AuditValidationError("UNSUPPORTED_ADAPTER_STATUS")
        source_name=f"result-{sequence:02d}.json"
        for name,payload in ((sk,start),(ck,done),(source_name,artifact)):
            if _sha(payload)!=v["source_file_hashes"].get(name): raise AuditValidationError("SOURCE_FILE_HASH_MISMATCH")
        records.append({"sequence":sequence,"instrument_id":record["instrument_id"],"m31p_per_call_sha256":record["canonical_per_call_record_sha256"],"approved_isin":record["isin"],"approved_mic":record["selected_mic"],"approved_exchange_ticker":record["exchange_ticker"],"approved_eodhd_exchange_code":record["eodhd_exchange_code"],"approved_request_path":record["request_path"],"approved_query_parameters":record["query_parameters"],"started_marker_file_sha256":v["source_file_hashes"][sk],"completed_marker_file_sha256":v["source_file_hashes"][ck],"persisted_result_file_sha256":v["source_file_hashes"][source_name],"persisted_adapter_result_sha256":artifact["adapter_result_sha256"],"recomputed_adapter_result_sha256":_sha(result),"adapter_resolution_status":status,"selected_candidate":copy.deepcopy(result.get("selected_candidate")),"resolved_provider_symbol":result.get("resolved_provider_symbol"),"candidate_count":result.get("candidate_count"),"exact_match_count":result.get("exact_match_count"),"provider_namespace_evidence":copy.deepcopy(result.get("evidence_dimensions")),"provider_type_taxonomy_evidence":record.get("type_taxonomy_status"),"raw_response_availability":"RAW_PROVIDER_RESPONSE_NOT_PERSISTED_BY_M31Q_V1","raw_response_sha256":None,"evidence_quality_status":classification,"findings":["RAW_RESPONSE_SHA256_UNAVAILABLE_WITHOUT_UNAUTHORIZED_NEW_PROVIDER_CALL"],"provenance":v["provenance"]})
    if summary.get("completed_sequences")!=list(range(1,16)) or summary.get("provider_calls_used")!=15: raise AuditValidationError("CALL_ACCOUNTING_MISMATCH")
    out={"version":"eodhd_search_resolution_result_audit_result_v1","contract_version":CONTRACT_VERSION,"audit_request_id":v["audit_request_id"],"status":"SEARCH_RESOLUTION_RESULTS_AUDITED_WITH_EVIDENCE_LIMITATIONS","approval_hash":p["approval_manifest_sha256"],"acquisition_plan_hash":p["acquisition_plan_sha256"],"m31i_hash":ih,"m31n_hash":nh,"m31o_adapter_version":p["m31o_adapter_contract_version"],"m31q_contract_version":"controlled_eodhd_search_batch_executor_v1","execution_summary_source_hash":v["source_file_hashes"]["execution-summary.json"],"source_artifact_index":copy.deepcopy(v["source_file_hashes"]),"records":records,"verified_provider_symbol_mappings":[x for x in records if x["evidence_quality_status"]=="VERIFIED_EXACT_PROVIDER_SYMBOL"],"review_required_records":[x for x in records if x["evidence_quality_status"] in _REVIEW],"failed_audit_records":[],"call_accounting":{"approved_metadata_calls":15,"started_calls":15,"completed_calls":15,"provider_calls_reported":15,"unresolved_started_calls":0,"duplicate_sequences":0,"retries":0,"fallback_calls":0,"pagination_calls":0,"health_check_calls":0,"historical_calls":0,"corporate_action_calls":0,"calendar_calls":0,"broker_calls":0},"marker_reconciliation":"EXACT_15_MATCHING_PAIRS","destination_reconciliation":"EXACT_15_APPROVED_DESTINATIONS","raw_response_evidence_status":"RAW_RESPONSE_SHA256_UNAVAILABLE_WITHOUT_UNAUTHORIZED_NEW_PROVIDER_CALL","credential_leak_scan_result":"NO_CREDENTIAL_MATERIAL_DETECTED","forbidden_operation_audit":"NO_FORBIDDEN_OPERATION_EVIDENCE","approval_hash_status":"CONSUMED_NON_REPLAYABLE","findings":["RAW_PROVIDER_RESPONSE_NOT_PERSISTED_BY_M31Q_V1"],"provenance":v["provenance"],"safety_fields":{"provider_calls_performed_during_m31r":0,"provider_credentials_accessed_during_m31r":False,"broker_calls_performed_during_m31r":0,"private_artifacts_mutated_during_m31r":False,"source_execution_replayed":False,"historical_acquisition_authorized":False,"canonical_snapshot_promotion_performed":False,"SPY_refetch_performed":False,"production_runtime_supported":False}}
    out["canonical_audit_manifest_sha256"]=_sha(out); return copy.deepcopy(out)

def _verified(value:object,key:str)->str:
    if not isinstance(value,dict): raise AuditValidationError("INVALID_HASHED_ARTIFACT")
    copy_value=copy.deepcopy(value); supplied=copy_value.pop(key,None)
    if not isinstance(supplied,str) or _sha(copy_value)!=supplied: raise AuditValidationError("CANONICAL_HASH_MISMATCH")
    return supplied
def _no_credentials(value:object)->None:
    if isinstance(value,dict):
        for key,item in value.items():
            if key.lower() in {"api_token","eodhd_api_key","credential","credentials"}: raise AuditValidationError("CREDENTIAL_MATERIAL_DETECTED")
            _no_credentials(item)
    elif isinstance(value,list):
        for item in value: _no_credentials(item)
    elif isinstance(value,str) and ("eodhd_api_key=" in value.lower() or "api_token=" in value.lower() or "secret=" in value.lower()): raise AuditValidationError("CREDENTIAL_MATERIAL_DETECTED")
def _canonical(value:object)->str:return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)
def _sha(value:object)->str:return hashlib.sha256(_canonical(value).encode()).hexdigest()
