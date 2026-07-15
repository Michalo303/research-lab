import copy
import pytest

from research_lab.execution.instrument_identity_execution_routing_v1 import build_instrument_identity_execution_routing
from research_lab.execution.research_execution_instrument_mapping_v1 import build_research_execution_instrument_mapping


def _identity(ticker, instrument_type="COMMON_STOCK", *, exchange="XNAS", isin="US0000000001", route="RESEARCH_ONLY"):
    return build_instrument_identity_execution_routing({"version":"instrument_identity_execution_routing_request_v1","instrument":{"instrument_id":f"id-{ticker}-{exchange}","legal_name":ticker,"instrument_type":instrument_type,"security_type":"ETC" if instrument_type=="PHYSICAL_GOLD_ETC" else "ETF" if "ETF" in instrument_type else "COMMON_STOCK","isin":isin,"primary_exchange":exchange,"selected_exchange":exchange,"exchange_ticker":ticker,"provider_symbol":None,"trading_currency":"EUR" if exchange=="XETR" else "USD","issuer":"issuer","domicile":"DE" if exchange=="XETR" else "US","share_class_identity":"ordinary","distribution_policy":"accumulating","currency_hedging":"unhedged","legal_product_classification":instrument_type,"kid_status":"REVIEWED_KID_AVAILABLE" if instrument_type in {"UCITS_ETF","PHYSICAL_GOLD_ETC"} else "NOT_APPLICABLE","point_in_time_metadata_status":"REVIEWED","metadata_as_of_date":"2026-07-15","provenance":{}},"execution_route":{"route":route,"automation_allowed":False,"manual_only":route=="FIO_MANUAL_LONG_TERM","risk_inclusion_required":True,"automatic_liquidation_allowed":False,"automatic_order_generation_allowed":False,"expected_holding_horizon":"REVIEW","eligibility_evidence":"reviewed","eligibility_as_of_date":"2026-07-15"},"provenance":{}})


def _request(mapping_type="ECONOMIC_PROXY", research=None, execution=None, **overrides):
    value={"version":"research_execution_instrument_mapping_request_v1","mapping_id":"map-1","research_instrument_identity_result":research or _identity("QQQ","NON_UCITS_ETF"),"execution_instrument_identity_result":execution or _identity("EQQQ","UCITS_ETF",exchange="XETR",isin="IE00BFZXGZ54"),"mapping_type":mapping_type,"economic_exposure":"NASDAQ_100","benchmark_relationship":"RELATED","currency_difference":"USD_TO_EUR","underlying_economic_currency_difference":"USD_EXPOSURE","exchange_calendar_difference":"US_TO_XETR","fee_difference":"EXPLICIT","legal_structure_difference":"EXPLICIT","collateral_structure_difference":"EXPLICIT","contango_backwardation_difference":"NOT_APPLICABLE","distribution_difference":"EXPLICIT","hedging_difference":"NONE","corporate_action_difference":"EXPLICIT","listing_identity_difference":"EXPLICIT","benchmark_methodology_difference":"EXPLICIT","futures_roll_difference":"NOT_APPLICABLE","tracking_validation_policy":"REQUIRE_HISTORY","maximum_allowed_tracking_error":0.05,"minimum_required_correlation":0.9,"minimum_history_overlap":252,"mapping_as_of_date":"2026-07-15","provenance":{}}
    value.update(overrides); return value


@pytest.mark.parametrize("kind,research,execution",[("ECONOMIC_PROXY",_identity("QQQ","NON_UCITS_ETF"),_identity("EQQQ","UCITS_ETF",exchange="XETR",isin="IE00BFZXGZ54")),("RELATED_EXPOSURE_NOT_IDENTICAL",_identity("GLD","NON_UCITS_ETF"),_identity("4GLD","PHYSICAL_GOLD_ETC",exchange="XETR",isin="DE000A0S9GB0")),("SAME_INSTRUMENT_SAME_LISTING",_identity("SMH","NON_UCITS_ETF",route="FIO_MANUAL_LONG_TERM"),_identity("SMH","NON_UCITS_ETF",route="FIO_MANUAL_LONG_TERM")),("SAME_SECURITY_DIFFERENT_LISTING",_identity("ABC",isin="US0000000002"),_identity("ABC","COMMON_STOCK",exchange="XLON",isin="US0000000002"))])
def test_supported_mappings_are_deterministic_and_review_only(kind,research,execution):
    request=_request(kind,research,execution); before=copy.deepcopy(request); result=build_research_execution_instrument_mapping(request)
    assert result["mapping_status"] == "PASS" and result["automation_allowed"] is False and request == before
    assert result["safety_flags"]["broker_calls_used"] == 0


def test_no_execution_and_benchmark_only_are_explicit():
    assert build_research_execution_instrument_mapping(_request("NO_EXECUTION_MAPPING",execution_instrument_identity_result=None))["mapping_status"] == "REVIEW_REQUIRED"
    assert build_research_execution_instrument_mapping(_request("BENCHMARK_ONLY",execution_instrument_identity_result=None))["mapping_status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("change,match",[(lambda x:x.update(mapping_type="SAME_INSTRUMENT_SAME_LISTING"),"exact"),(lambda x:x.update(tracking_validation_policy=""),"tracking"),(lambda x:x.update(minimum_history_overlap=0),"history"),(lambda x:x.update(minimum_required_correlation=1.1),"correlation"),(lambda x:x.update(mapping_as_of_date="2020-01-01"),"stale")])
def test_invalid_identity_and_tracking_inputs_fail_closed(change,match):
    request=_request(); change(request)
    with pytest.raises(ValueError,match=match): build_research_execution_instrument_mapping(request)


def test_unknown_fields_hashes_and_child_immutability():
    request=_request(); first=build_research_execution_instrument_mapping(request); second=build_research_execution_instrument_mapping(request)
    assert first["output_payload_sha256"]==second["output_payload_sha256"]
    request["unexpected"]=True
    with pytest.raises(ValueError,match="unknown"): build_research_execution_instrument_mapping(request)


def test_blocked_child_route_stays_review_only_and_oil_is_never_exact():
    blocked=_identity("OIL","COMMODITY_ETC",exchange="XETR",isin="DE0000000003",route="IBKR_RETAIL_BLOCKED")
    result=build_research_execution_instrument_mapping(_request("RELATED_EXPOSURE_NOT_IDENTICAL",_identity("USO","NON_UCITS_ETF"),blocked))
    assert result["mapping_status"] == "REVIEW_REQUIRED"
    assert result["automation_allowed"] is False
    with pytest.raises(ValueError,match="exact"):
        build_research_execution_instrument_mapping(_request("SAME_INSTRUMENT_SAME_LISTING",_identity("USO","NON_UCITS_ETF"),blocked))
