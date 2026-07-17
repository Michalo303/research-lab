import copy
import hashlib
import json

import pytest

from research_lab.execution.eodhd_exact_identity_capability_v2 import build_eodhd_exact_identity_capability_v2
from research_lab.execution.eodhd_exact_symbol_resolution_readiness_v3 import build_eodhd_exact_symbol_resolution_readiness_v3
from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _request():
    m31i = build_official_instrument_identity_manifest(); m31n = build_eodhd_exact_identity_capability_v2(m31i)
    m31p = build_eodhd_exact_symbol_resolution_readiness_v3({"version":"eodhd_exact_symbol_resolution_readiness_request_v3","readiness_request_id":"M31P:V3","m31i_manifest":m31i,"expected_m31i_canonical_manifest_sha256":m31i["canonical_manifest_sha256"],"m31n_capability_manifest":m31n,"expected_m31n_canonical_capability_sha256":m31n["canonical_capability_manifest_sha256"],"m31o_adapter_contract_version":"eodhd_approval_bound_search_metadata_adapter_v2","provider_call_policy":"BOUNDED_EODHD_SEARCH_ONLY","destination_policy":"PENDING_EXACT_SYMBOL_RESOLUTION_V3","approval_policy":"EXTERNAL_HUMAN_APPROVAL_REQUIRED","provenance":"M31I_M31N_M31O_ONLY"})
    started={}; completed={}; results={}; hashes={}
    outcomes=[]
    for record in m31p["complete_plan"]:
        seq=record["sequence"]; status="RESOLVED_EXACT_PROVIDER_SYMBOL" if seq not in {2,4,7} else ({2:"REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY",4:"REVIEW_REQUIRED_NO_EXACT_MATCH",7:"REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY"}[seq])
        result={"version":"eodhd_approval_bound_search_metadata_result_v2","contract_version":"eodhd_approval_bound_search_metadata_adapter_v2","selected_sequence":seq,"instrument_id":record["instrument_id"],"resolution_status":status,"candidate_count":1,"exact_match_count":1 if status.startswith("RESOLVED") else 0,"selected_candidate":None,"resolved_provider_symbol":f"{record['exchange_ticker']}.{record['eodhd_exchange_code']}" if status.startswith("RESOLVED") else None,"safety_fields":{"provider_calls_used":1,"provider_credentials_accessed":True,"retries_used":0,"fallback_used":False,"pagination_calls":0,"health_check_calls":0,"historical_data_requested":False,"corporate_actions_requested":False,"filesystem_writes_performed":False,"production_runtime_supported":False}}
        result["output_payload_sha256"]=_sha(result)
        artifact={"sequence":seq,"record_sha256":record["canonical_per_call_record_sha256"],"adapter_result":result,"adapter_result_sha256":_sha(result)}
        started[f"{seq:02d}-CALL_STARTED.json"]={"sequence":seq,"record_sha256":record["canonical_per_call_record_sha256"]}
        completed[f"{seq:02d}-CALL_COMPLETED.json"]={"sequence":seq,"adapter_result_sha256":artifact["adapter_result_sha256"]}
        results[record["future_destination"]]=artifact; outcomes.append({"sequence":seq,"resolution_status":status,"adapter_result_sha256":artifact["adapter_result_sha256"]})
    intent={"version":"m31q_execution_intent_v1","approval_manifest_sha256":m31p["approval_manifest_sha256"],"acquisition_plan_sha256":m31p["acquisition_plan_sha256"],"schedule_sha256":m31p["acquisition_plan_sha256"]}
    summary={"completed_sequences":list(range(1,16)),"outcomes":outcomes,"provider_calls_used":15}
    sources={"execution-intent.json":intent,"execution-summary.json":summary,**started,**completed}
    sources.update({f"result-{artifact['sequence']:02d}.json": artifact for artifact in results.values()})
    hashes={name:_sha(value) for name,value in sources.items()}
    return {"version":"eodhd_search_resolution_result_audit_request_v1","audit_request_id":"M31R:FIXED","m31i_manifest":m31i,"m31n_capability_manifest":m31n,"m31p_readiness_result":m31p,"execution_intent":intent,"started_markers":started,"completed_markers":completed,"result_artifacts":results,"execution_summary":summary,"source_file_hashes":hashes,"provenance":"M31Q_COMPLETED_PRIVATE_ARTIFACTS_READ_ONLY"}


def test_audits_exact_15_sequence_evidence_with_explicit_missing_raw_response_limitation():
    from research_lab.execution.eodhd_search_resolution_result_audit_v1 import audit_eodhd_search_resolution_results_v1
    request=_request(); before=copy.deepcopy(request)
    output=audit_eodhd_search_resolution_results_v1(request)
    assert request==before
    assert output["status"] == "SEARCH_RESOLUTION_RESULTS_AUDITED_WITH_EVIDENCE_LIMITATIONS"
    assert len(output["records"]) == 15
    assert output["call_accounting"]["completed_calls"] == 15
    assert all(x["raw_response_availability"] == "RAW_PROVIDER_RESPONSE_NOT_PERSISTED_BY_M31Q_V1" for x in output["records"])
    assert output["approval_hash_status"] == "CONSUMED_NON_REPLAYABLE"
    assert output["safety_fields"]["provider_calls_performed_during_m31r"] == 0


def test_rejects_tampered_adapter_result_or_credential_material():
    from research_lab.execution.eodhd_search_resolution_result_audit_v1 import AuditValidationError, audit_eodhd_search_resolution_results_v1
    request=_request(); next(iter(request["result_artifacts"].values()))["adapter_result"]["instrument_id"]="tampered"
    with pytest.raises(AuditValidationError): audit_eodhd_search_resolution_results_v1(request)


def test_exports_audit_builder_from_execution_package():
    from research_lab.execution import audit_eodhd_search_resolution_results_v1
    from research_lab.execution.eodhd_search_resolution_result_audit_v1 import AuditValidationError
    assert callable(audit_eodhd_search_resolution_results_v1)
    request=_request(); request["execution_summary"]["note"]="EODHD_API_KEY=secret"
    with pytest.raises(AuditValidationError): audit_eodhd_search_resolution_results_v1(request)
