"""Offline M31P exact, approval-ready EODHD Search plan."""
from __future__ import annotations
import copy, hashlib, json
from typing import Any

CONTRACT_VERSION="eodhd_exact_symbol_resolution_readiness_v3"
ADAPTER="eodhd_approval_bound_search_metadata_adapter_v2"
ROOT="/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3/"
SUPERSEDED=["c2cad14d2c41a718fe8c5095ee0342f4bdd85127418a53ad20347089d8d77ef9","a16c28fcb98d1c890111954ce675328874e5658115797fa2ae031dbf9e599b2c"]
SAFETY={"provider_calls_used":0,"provider_credentials_accessed":False,"broker_calls_used":0,"broker_credentials_accessed":False,"filesystem_writes_performed":False,"private_snapshot_mutations_performed":False,"SPY_refetch_performed":False,"historical_data_requested":False,"corporate_actions_requested":False,"calendar_data_requested":False,"provider_acquisition_authorized":False,"paper_trading_performed":False,"live_trading_performed":False,"executable_orders_generated":False,"deployment_performed":False,"service_restart_performed":False,"registry_write_performed":False,"production_runtime_supported":False}
FIELDS={"version","readiness_request_id","m31i_manifest","expected_m31i_canonical_manifest_sha256","m31n_capability_manifest","expected_m31n_canonical_capability_sha256","m31o_adapter_contract_version","provider_call_policy","destination_policy","approval_policy","provenance"}

def build_eodhd_exact_symbol_resolution_readiness_v3(raw:dict[str,object])->dict[str,object]:
 if not isinstance(raw,dict) or set(raw)!=FIELDS: raise ValueError("unknown or missing readiness request fields")
 v=copy.deepcopy(raw)
 if v["version"]!="eodhd_exact_symbol_resolution_readiness_request_v3" or v["m31o_adapter_contract_version"]!=ADAPTER or v["provider_call_policy"]!="BOUNDED_EODHD_SEARCH_ONLY" or v["destination_policy"]!="PENDING_EXACT_SYMBOL_RESOLUTION_V3" or v["approval_policy"]!="EXTERNAL_HUMAN_APPROVAL_REQUIRED": raise ValueError("invalid readiness policy")
 i=_verified(v["m31i_manifest"],"canonical_manifest_sha256",v["expected_m31i_canonical_manifest_sha256"],"VERIFIED","manifest_status")
 n=_verified(v["m31n_capability_manifest"],"canonical_capability_manifest_sha256",v["expected_m31n_canonical_capability_sha256"],"BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE_V2","capability_status")
 maps={m["instrument_id"]:m for m in n["exchange_code_mappings"]}
 if len(maps)!=15 or len(i["preferred_universe"])!=15: raise ValueError("exact universe required")
 plan=[]
 for seq,x in enumerate(i["preferred_universe"],1):
  m=maps.get(x["instrument_id"])
  if not m or m.get("canonical_mapping_sha256")!=_sha({k:z for k,z in m.items() if k!="canonical_mapping_sha256"}): raise ValueError("M31N mapping hash mismatch")
  dest=f"{ROOT}{x['instrument_id'].replace(':','_')}/search-response.json"
  ok=m["mapping_status"]=="VERIFIED_PROVIDER_EXCHANGE_MAPPING" and m["selected_mic_membership_status"]=="SELECTED_MIC_CONTAINED_IN_PROVIDER_OPERATING_MICS" and m["search_type_parameter"] in {"all","stock","etf"} and bool(m["accepted_response_types"])
  rec={"sequence":seq,"instrument_id":x["instrument_id"],"ticker_label":x["ticker"],"legal_name":x["legal_name"],"legal_product_type":x["instrument_type"],"isin":x["isin"],"selected_mic":x["mic"],"official_exchange":x["official_exchange"],"exchange_ticker":x["exchange_ticker"],"currency":x["trading_currency"],"m31i_listing_evidence_sha256":x["official_evidence"][0]["evidence_sha256"],"m31i_canonical_manifest_sha256":i["canonical_manifest_sha256"],"eodhd_exchange_code":m["provider_exchange_code"],"provider_exchange_name":m["provider_exchange_name"],"provider_namespace_classification":m["provider_namespace_classification"],"provider_operating_mics":m["provider_operating_mics"],"selected_mic_membership_status":m["selected_mic_membership_status"],"official_exchange_evidence_sha256":m["official_exchange_evidence_sha256"],"m31n_mapping_sha256":m["canonical_mapping_sha256"],"m31n_canonical_capability_sha256":n["canonical_capability_manifest_sha256"],"m31o_adapter_contract_version":ADAPTER,"request_path":f"/api/search/{x['isin']}","query_parameters":{"exchange":m["provider_exchange_code"],"type":m["search_type_parameter"],"limit":10,"fmt":"json"},"search_type_parameter":m["search_type_parameter"],"accepted_response_types":m["accepted_response_types"],"type_taxonomy_status":m["type_taxonomy_status"],"response_limit":10,"validation_contract":"EXACT_ISIN_EXCHANGE_CURRENCY_TICKER_AND_TYPE_NO_FUZZY_MATCHING","future_destination":dest if ok else None,"call_count":1 if ok else 0,"retry_count":0,"fallback_policy":"NO_FALLBACK","pagination_policy":"NO_PAGINATION","health_check_policy":"NO_HEALTH_CHECK","authorization_status":"AUTHORIZABLE_BOUNDED_SEARCH_V2" if ok else "BLOCKED_EXCHANGE_MAPPING","findings":["REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY"] if m["type_taxonomy_status"]!="EXACT_PROVIDER_TYPE_TAXONOMY" else ["EXACT_REPLAYABLE_REQUEST"],"provenance":"M31I_M31N_M31O_ONLY"}
  rec["canonical_per_call_record_sha256"]=_sha(rec); plan.append(rec)
 if len({r["future_destination"] for r in plan if r["future_destination"]})!=sum(r["call_count"] for r in plan): raise ValueError("unsafe duplicate destination")
 auth=[r for r in plan if r["call_count"]]; blocked=[r for r in plan if not r["call_count"]]; budget={"metadata_calls_max":len(auth),"historical_calls_max":0,"corporate_action_calls_max":0,"calendar_calls_max":0,"total_calls_max":len(auth),"retries":0,"sequential_only":True,"stop_on_first_failure":True,"fallback_provider_allowed":False,"pagination_calls":0,"health_check_calls":0,"hidden_calls":0}
 planhash=_sha(plan); approval={"purpose":"EODHD_BOUNDED_SEARCH_IDENTITY_RESOLUTION_ONLY_V2","acquisition_plan_sha256":planhash,"adapter_contract_version":ADAPTER,"call_budgets":budget,"authorized_records":auth}; approval["canonical_approval_manifest_sha256"]=_sha(approval)
 out={"version":"eodhd_exact_symbol_resolution_readiness_result_v3","contract_version":CONTRACT_VERSION,"readiness_request_id":v["readiness_request_id"],"status":"HUMAN_APPROVAL_REQUIRED_FOR_CONTROLLED_EODHD_SEARCH_RESOLUTION_V2" if auth else "REVIEW_REQUIRED","m31i_canonical_manifest_sha256":i["canonical_manifest_sha256"],"m31n_canonical_capability_sha256":n["canonical_capability_manifest_sha256"],"m31o_adapter_contract_version":ADAPTER,"complete_plan":plan,"authorized_records":auth,"blocked_records":blocked,"call_budgets":budget,"future_destinations":[r["future_destination"] for r in auth],"acquisition_plan_sha256":planhash,"approval_manifest":approval,"approval_manifest_sha256":approval["canonical_approval_manifest_sha256"],"superseded_approval_hashes":SUPERSEDED,"spy_unchanged_evidence":{"status":"SPY_REFETCH_NOT_AUTHORIZED"},"findings":["NO_PROVIDER_CALLS_EXECUTED","EXTERNAL_HUMAN_APPROVAL_REQUIRED"],"provenance":v["provenance"],"safety_fields":copy.deepcopy(SAFETY)}
 return copy.deepcopy(out)

def _verified(obj,key,expected,status,statuskey):
 if not isinstance(obj,dict) or obj.get(statuskey)!=status: raise ValueError("upstream contract unavailable")
 z=copy.deepcopy(obj); got=z.pop(key,None)
 if got!=_sha(z) or got!=expected: raise ValueError("canonical hash mismatch")
 return obj
def _sha(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
