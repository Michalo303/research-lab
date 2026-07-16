"""Offline M31M plan for subsequently human-approved bounded EODHD Search calls."""
from __future__ import annotations
import copy, hashlib, json
from typing import Any

CONTRACT_VERSION = "eodhd_exact_symbol_resolution_readiness_v2"
_FIELDS={"version","readiness_request_id","m31i_manifest","expected_m31i_canonical_manifest_sha256","m31k_capability_manifest","expected_m31k_canonical_manifest_sha256","m31l_adapter_contract_version","provider_call_policy","destination_policy","approval_policy","provenance"}
_SAFETY={"provider_calls_used":0,"provider_credentials_accessed":False,"broker_calls_used":0,"broker_credentials_accessed":False,"filesystem_writes_performed":False,"private_snapshot_mutations_performed":False,"SPY_refetch_performed":False,"historical_data_requested":False,"corporate_actions_requested":False,"calendar_data_requested":False,"provider_acquisition_authorized":False,"production_runtime_supported":False}
_ROOT="/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v2/"
_OLD="c2cad14d2c41a718fe8c5095ee0342f4bdd85127418a53ad20347089d8d77ef9"
def build_eodhd_exact_symbol_resolution_readiness(raw:dict[str,object])->dict[str,object]:
 if not isinstance(raw,dict) or set(raw)!=( _FIELDS): raise ValueError("unknown or missing readiness request fields")
 v=copy.deepcopy(raw); i=v["m31i_manifest"]; k=v["m31k_capability_manifest"]
 if v["m31l_adapter_contract_version"]!="eodhd_bounded_search_metadata_adapter_v1": raise ValueError("unsupported M31L contract")
 for obj,key,expected in ((i,"canonical_manifest_sha256",v["expected_m31i_canonical_manifest_sha256"]),(k,"canonical_capability_manifest_sha256",v["expected_m31k_canonical_manifest_sha256"])):
  h=copy.deepcopy(obj); supplied=h.pop(key,None)
  if supplied!=_sha(h) or supplied!=expected: raise ValueError("canonical manifest hash mismatch")
 if i.get("manifest_status")!="VERIFIED" or k.get("capability_status")!="BOUNDED_EXACT_IDENTITY_CAPABILITY_AVAILABLE": raise ValueError("unavailable composed contract")
 maps={x["instrument_id"]:x for x in k["exchange_code_mappings"]}; plan=[]; blocked=[]
 for n,item in enumerate(i["preferred_universe"],1):
  m=maps.get(item["instrument_id"]); base={"sequence":n,"instrument_id":item["instrument_id"],"ticker_label":item["ticker"],"legal_name":item["legal_name"],"isin":item["isin"],"selected_mic":item["mic"],"official_exchange":item["official_exchange"],"exchange_ticker":item["exchange_ticker"],"currency":item["trading_currency"],"product_type":item["instrument_type"],"eodhd_exchange_code":m.get("eodhd_exchange_code") if m else None,"adapter_contract_version":v["m31l_adapter_contract_version"],"endpoint_class":"EODHD_SEARCH_BY_ISIN_EXCHANGE_BOUNDED_V1","query_parameters":{"exchange":m.get("eodhd_exchange_code") if m else None,"type":m.get("provider_type") if m else None,"limit":10,"fmt":"json"},"response_limit":10,"retry_count":0,"fallback_policy":"NO_FALLBACK","provenance":"M31I_M31K_M31L_ONLY"}
  if m and m["mapping_status"]=="VERIFIED_PROVIDER_EXCHANGE_CODE":
   rec={**base,"authorization_status":"AUTHORIZABLE_BOUNDED_SEARCH","call_count":1,"future_destination":f"{_ROOT}{item['instrument_id'].replace(':','_')}/search-response.json","findings":["EXACT_ISIN_BOUNDED_SEARCH"]};plan.append(rec)
  else: blocked.append({**base,"authorization_status":"BLOCKED_EXCHANGE_CODE_UNRESOLVED","call_count":0,"future_destination":None,"findings":["EXCHANGE_MAPPING_NOT_VERIFIED"]})
 budget={"metadata_calls_max":len(plan),"historical_calls_max":0,"corporate_action_calls_max":0,"calendar_calls_max":0,"total_calls_max":len(plan),"retries":0,"sequential_only":True,"stop_on_first_failure":True,"fallback_provider_allowed":False,"health_check_calls":0,"hidden_calls":0,"pagination_calls":0}
 approval={"version":"eodhd_bounded_search_approval_manifest_v2","purpose":"EODHD_BOUNDED_SEARCH_IDENTITY_RESOLUTION_ONLY","official_identity_manifest_sha256":i["canonical_manifest_sha256"],"provider_capability_manifest_sha256":k["canonical_capability_manifest_sha256"],"adapter_contract_version":v["m31l_adapter_contract_version"],"authorized_instruments":plan,"blocked_instruments":blocked,"call_budgets":budget,"SPY_REFETCH_NOT_AUTHORIZED":True,"superseded_approval_hashes":[_OLD],"safety_fields":copy.deepcopy(_SAFETY)};approval["canonical_approval_manifest_sha256"]=_sha(approval)
 out={"version":"eodhd_exact_symbol_resolution_readiness_result_v2","contract_version":CONTRACT_VERSION,"readiness_request_id":v["readiness_request_id"],"status":"HUMAN_APPROVAL_REQUIRED_FOR_CONTROLLED_EODHD_SEARCH_RESOLUTION" if plan else "REVIEW_REQUIRED","m31i_canonical_hash":i["canonical_manifest_sha256"],"m31k_canonical_hash":k["canonical_capability_manifest_sha256"],"m31l_contract_version":v["m31l_adapter_contract_version"],"complete_plan":plan+blocked,"authorized_records":plan,"blocked_records":blocked,"call_budgets":budget,"future_destinations":[x["future_destination"] for x in plan],"superseded_hashes":[_OLD],"spy_unchanged_evidence":{"status":"SPY_REFETCH_NOT_AUTHORIZED"},"approval_manifest":approval,"approval_manifest_sha256":approval["canonical_approval_manifest_sha256"],"findings":["NO_PROVIDER_CALLS_EXECUTED","HUMAN_APPROVAL_REQUIRED"],"provenance":v["provenance"],"safety_fields":copy.deepcopy(_SAFETY)};out["acquisition_plan_sha256"]=_sha(out["complete_plan"]);return copy.deepcopy(out)
def _sha(x:object)->str:return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
