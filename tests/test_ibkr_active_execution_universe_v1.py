import copy

import pytest

from research_lab.execution.instrument_identity_execution_routing_v1 import (
    build_instrument_identity_execution_routing,
)
from research_lab.execution.ibkr_active_execution_universe_v1 import (
    build_ibkr_active_execution_universe,
)


def _child(ticker="MSFT", instrument_type="COMMON_STOCK", security_type="COMMON_STOCK", domicile="US", currency="USD", kid="NOT_REQUIRED_COMMON_STOCK"):
    instrument = {"instrument_id": f"ibkr-{ticker.lower()}", "legal_name": ticker + " legal name", "instrument_type": instrument_type, "security_type": security_type, "isin": "US5949181045" if ticker == "MSFT" else "IE00BK5BQT80", "primary_exchange": "XNAS" if currency == "USD" else "XETR", "selected_exchange": "XNAS" if currency == "USD" else "XETR", "exchange_ticker": ticker, "provider_symbol": None, "trading_currency": currency, "issuer": "test issuer", "domicile": domicile, "share_class_identity": "ORDINARY", "distribution_policy": "ACCUMULATING", "currency_hedging": "UNHEDGED", "legal_product_classification": instrument_type, "kid_status": kid, "point_in_time_metadata_status": "REVIEWED_POINT_IN_TIME", "metadata_as_of_date": "2026-07-15", "provenance": {"source": "test"}}
    route = {"route": "IBKR_REVIEW_REQUIRED", "automation_allowed": False, "manual_only": False, "risk_inclusion_required": True, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False, "expected_holding_horizon": "SWING", "eligibility_evidence": "reviewed exact listing", "eligibility_as_of_date": "2026-07-15"}
    return build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": instrument, "execution_route": route, "provenance": {"source": "test"}})


def _candidate(ticker="MSFT", instrument_type="COMMON_STOCK", **overrides):
    child = _child(ticker, instrument_type, "ETC" if instrument_type == "PHYSICAL_GOLD_ETC" else "ETF" if instrument_type == "UCITS_ETF" else "COMMON_STOCK", "DE" if instrument_type != "COMMON_STOCK" else "US", "EUR" if instrument_type != "COMMON_STOCK" else "USD", "REVIEWED_KID_AVAILABLE" if instrument_type != "COMMON_STOCK" else "NOT_REQUIRED_COMMON_STOCK")
    item = {"candidate_id": ticker.lower(), "identity_routing_result": child, "exchange": child["instrument"]["selected_exchange"], "ticker": ticker, "trading_currency": child["instrument"]["trading_currency"], "proposed_ibkr_execution_route": "IBKR_REVIEW_ONLY", "instrument_type": instrument_type, "security_type": child["instrument"]["security_type"], "trading_permission_category": "RETAIL", "trading_permission_evidence": {"status": "EXPLICIT_CURRENT", "as_of_date": "2026-07-15", "source": "offline reviewed record"}, "kid_or_retail_documentation_status": "NOT_REQUIRED_COMMON_STOCK" if instrument_type == "COMMON_STOCK" else "CURRENT_AVAILABLE", "documentation_evidence": None if instrument_type == "COMMON_STOCK" else {"status": "EXPLICIT_CURRENT", "as_of_date": "2026-07-15", "source": "official KID"}, "offline_price_observation": {"value": "100.00", "timestamp": "2026-07-15T10:00:00Z"}, "offline_median_volume_observation": {"value": "1000000", "timestamp": "2026-07-15T10:00:00Z"}, "offline_spread_observation": {"value_bps": "5.0", "timestamp": "2026-07-15T10:00:00Z"}, "corporate_action_policy": "REVIEW_CORPORATE_ACTIONS", "delisting_policy": "BLOCK_ON_DELISTING", "settlement_currency_policy": "MATCH_TRADING_CURRENCY", "allowed_order_types": ["LIMIT"], "regular_session_policy": "REGULAR_SESSION_ONLY", "provenance": {"source": "test"}}
    item.update(overrides)
    return item


def _request(candidates=None):
    return {"version": "ibkr_active_execution_universe_request_v1", "universe_id": "ibkr-swing-20260715", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "EUR", "candidates": candidates if candidates is not None else [_candidate()], "universe_policy": {"long_only": True, "leverage_allowed": False, "margin_assumed": False, "shorting_allowed": False, "derivatives_allowed": False, "fractional_shares_assumed": False, "extended_hours_assumed": False}, "liquidity_policy": {"minimum_price": "5", "minimum_median_volume": "100000", "maximum_spread_bps": "20"}, "eligibility_evidence_policy": {"maximum_evidence_age_days": 0, "require_explicit_retail_evidence": True}, "provenance": {"source": "test"}}


def test_explicit_stock_ucits_and_physical_gold_etc_are_eligible_and_deterministic():
    gold = _candidate("4GLD", "PHYSICAL_GOLD_ETC")
    first = build_ibkr_active_execution_universe(_request([_candidate(), _candidate("VWCE", "UCITS_ETF"), gold]))
    second = build_ibkr_active_execution_universe(_request([_candidate(), _candidate("VWCE", "UCITS_ETF"), gold]))
    assert first["status"] == "PASS"
    assert [x["eligibility_status"] for x in first["accepted_instruments"]] == ["ELIGIBLE"] * 3
    assert next(x for x in first["accepted_instruments"] if x["ticker"] == "4GLD")["instrument_type"] == "PHYSICAL_GOLD_ETC"
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    assert first["safety_flags"]["broker_calls_used"] == 0
    assert first["safety_flags"]["production_runtime_supported"] is False


@pytest.mark.parametrize("mutation, expected", [
    (lambda c: c.update({"instrument_type": "NON_UCITS_ETF"}), "BLOCKED"),
    (lambda c: c["trading_permission_evidence"].update({"as_of_date": "2026-07-14"}), "REVIEW_REQUIRED"),
    (lambda c: c.update({"documentation_evidence": None}), "REVIEW_REQUIRED"),
    (lambda c: c["offline_price_observation"].update({"value": "1"}), "REVIEW_REQUIRED"),
    (lambda c: c["offline_median_volume_observation"].update({"value": "1"}), "REVIEW_REQUIRED"),
    (lambda c: c["offline_spread_observation"].update({"value_bps": "21"}), "REVIEW_REQUIRED"),
])
def test_evidence_and_liquidity_fail_closed(mutation, expected):
    candidate = _candidate("USO", "UCITS_ETF") if expected != "BLOCKED" else _candidate("SPY", "NON_UCITS_ETF")
    mutation(candidate)
    result = build_ibkr_active_execution_universe(_request([candidate]))
    assert result["instrument_results"][0]["eligibility_status"] == expected


def test_duplicate_child_tampering_unknown_fields_and_unsafe_policy_are_rejected():
    candidate = _candidate()
    with pytest.raises(ValueError, match="duplicate"):
        build_ibkr_active_execution_universe(_request([candidate, copy.deepcopy(candidate)]))
    tampered = _candidate(); tampered["identity_routing_result"]["instrument"]["exchange_ticker"] = "EVIL"
    with pytest.raises(ValueError, match="child"):
        build_ibkr_active_execution_universe(_request([tampered]))
    request = _request(); request["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        build_ibkr_active_execution_universe(request)
    request = _request(); request["universe_policy"]["shorting_allowed"] = True
    with pytest.raises(ValueError, match="policy"):
        build_ibkr_active_execution_universe(request)


def test_derivative_and_leveraged_products_are_rejected():
    derivative = _candidate(); derivative["security_type"] = "DERIVATIVE"
    with pytest.raises(ValueError, match="derivative"):
        build_ibkr_active_execution_universe(_request([derivative]))
    leveraged = _candidate(); leveraged["identity_routing_result"] = _child("TQQQ", "COMMON_STOCK", "COMMON_STOCK")
    leveraged["identity_routing_result"]["instrument"]["legal_product_classification"] = "LEVERAGED_ETF"
    # Rebuild a valid M31A child carrying the explicit leveraged classification.
    instrument = leveraged["identity_routing_result"]["instrument"]
    leveraged["identity_routing_result"] = build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": instrument, "execution_route": leveraged["identity_routing_result"]["execution_route"], "provenance": {"source": "test"}})
    leveraged.update({"exchange": "XNAS", "ticker": "TQQQ", "trading_currency": "USD"})
    with pytest.raises(ValueError, match="leveraged"):
        build_ibkr_active_execution_universe(_request([leveraged]))
