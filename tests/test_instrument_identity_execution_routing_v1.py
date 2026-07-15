import copy

import pytest

from research_lab.execution.instrument_identity_execution_routing_v1 import (
    build_instrument_identity_execution_routing,
)


def _request(**overrides):
    value = {
        "version": "instrument_identity_execution_routing_request_v1",
        "instrument": {
            "instrument_id": "fio-uso-us-arcx",
            "legal_name": "United States Oil Fund, LP",
            "instrument_type": "NON_UCITS_ETF",
            "security_type": "COMMODITY_FUTURES_FUND",
            "isin": "US91288V1035",
            "primary_exchange": "ARCX",
            "selected_exchange": "ARCX",
            "exchange_ticker": "USO",
            "provider_symbol": "USO.US",
            "trading_currency": "USD",
            "issuer": "United States Commodity Funds LLC",
            "domicile": "US",
            "share_class_identity": "LIMITED_PARTNERSHIP_UNIT",
            "distribution_policy": "NONE_DECLARED",
            "currency_hedging": "UNHEDGED",
            "legal_product_classification": "COMMODITY_FUTURES_FUND",
            "kid_status": "NOT_APPLICABLE_FIO_MANUAL",
            "point_in_time_metadata_status": "REVIEWED_POINT_IN_TIME",
            "metadata_as_of_date": "2026-07-15",
            "provenance": {"source": "official-test"},
        },
        "execution_route": {
            "route": "FIO_MANUAL_LONG_TERM",
            "automation_allowed": False,
            "manual_only": True,
            "risk_inclusion_required": True,
            "automatic_liquidation_allowed": False,
            "automatic_order_generation_allowed": False,
            "expected_holding_horizon": "THREE_YEARS_OR_LONGER",
            "eligibility_evidence": "user_supplied_manual_holding",
            "eligibility_as_of_date": "2026-07-15",
        },
        "provenance": {"source": "test"},
    }
    for key, item in overrides.items():
        if key in {"instrument", "execution_route"}:
            value[key].update(item)
        else:
            value[key] = item
    return value


def test_valid_fio_uso_is_not_spot_oil_and_is_manual_only():
    result = build_instrument_identity_execution_routing(_request())
    assert result["validation_status"] == "PASS"
    assert result["instrument"]["instrument_type"] == "NON_UCITS_ETF"
    assert result["instrument"]["security_type"] == "COMMODITY_FUTURES_FUND"
    assert result["execution_route"]["automation_allowed"] is False
    assert result["safety_flags"]["production_runtime_supported"] is False


def test_valid_fio_smh_is_etf_not_common_stock():
    request = _request(instrument={"instrument_id": "fio-smh-us-arcx", "legal_name": "VanEck Semiconductor ETF", "instrument_type": "NON_UCITS_ETF", "security_type": "ETF", "isin": "US92189F6768", "exchange_ticker": "SMH", "provider_symbol": "SMH.US", "issuer": "VanEck"})
    assert build_instrument_identity_execution_routing(request)["validation_status"] == "PASS"


@pytest.mark.parametrize(
    ("instrument", "route"),
    [
        ({"instrument_id": "ibkr-msft-us-xnas", "legal_name": "Microsoft Corporation", "instrument_type": "COMMON_STOCK", "security_type": "COMMON_STOCK", "isin": "US5949181045", "selected_exchange": "XNAS", "primary_exchange": "XNAS", "exchange_ticker": "MSFT", "provider_symbol": "MSFT.US", "issuer": "Microsoft Corporation", "legal_product_classification": "COMMON_STOCK", "kid_status": "NOT_APPLICABLE_COMMON_STOCK"}, {"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": "reviewed_exact_listing_and_permission"}),
        ({"instrument_id": "ibkr-vwce-de-xetr", "legal_name": "Vanguard FTSE All-World UCITS ETF", "instrument_type": "UCITS_ETF", "security_type": "ETF", "isin": "IE00BK5BQT80", "selected_exchange": "XETR", "primary_exchange": "XLON", "exchange_ticker": "VWCE", "provider_symbol": None, "trading_currency": "EUR", "issuer": "Vanguard", "domicile": "IE", "legal_product_classification": "UCITS_ETF", "kid_status": "REVIEWED_KID_AVAILABLE"}, {"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": "reviewed_exact_listing_and_kid"}),
        ({"instrument_id": "ibkr-4gld-de-xetr", "legal_name": "Xetra-Gold", "instrument_type": "PHYSICAL_GOLD_ETC", "security_type": "ETC", "isin": "DE000A0S9GB0", "selected_exchange": "XETR", "primary_exchange": "XETR", "exchange_ticker": "4GLD", "provider_symbol": None, "trading_currency": "EUR", "issuer": "Deutsche Borse Commodities GmbH", "domicile": "DE", "legal_product_classification": "PHYSICAL_GOLD_ETC", "kid_status": "REVIEWED_KID_AVAILABLE"}, {"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": "reviewed_exact_listing_and_kid"}),
    ],
)
def test_explicitly_evidenced_ibkr_instruments_can_be_eligible(instrument, route):
    result = build_instrument_identity_execution_routing(_request(instrument=instrument, execution_route=route))
    assert result["validation_status"] == "PASS"
    assert result["execution_route"]["route"] == "IBKR_AUTOMATED_ELIGIBLE"


def test_4gld_cannot_be_classified_as_ucits():
    request = _request(instrument={"instrument_id": "ibkr-4gld", "instrument_type": "UCITS_ETF", "security_type": "ETF", "exchange_ticker": "4GLD", "legal_product_classification": "UCITS_ETF"}, execution_route={"route": "IBKR_REVIEW_REQUIRED", "automation_allowed": False, "manual_only": False, "eligibility_evidence": "reviewed"})
    with pytest.raises(ValueError, match="4GLD"):
        build_instrument_identity_execution_routing(request)


def test_us_etf_is_blocked_without_explicit_reviewed_exception():
    request = _request(instrument={"instrument_type": "NON_UCITS_ETF", "security_type": "ETF", "legal_product_classification": "NON_UCITS_ETF"}, execution_route={"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": "ticker exists"})
    with pytest.raises(ValueError, match="US ETF"):
        build_instrument_identity_execution_routing(request)


def test_us_etf_requires_an_explicit_reviewed_retail_exception_not_professional_status():
    request = _request(
        instrument={"instrument_type": "NON_UCITS_ETF", "security_type": "ETF", "legal_product_classification": "NON_UCITS_ETF"},
        execution_route={"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": "reviewed_us_etf_retail_exception"},
    )
    assert build_instrument_identity_execution_routing(request)["validation_status"] == "PASS"


def test_conflicting_or_missing_identity_and_eligibility_are_rejected():
    with pytest.raises(ValueError, match="eligibility"):
        build_instrument_identity_execution_routing(_request(execution_route={"route": "IBKR_AUTOMATED_ELIGIBLE", "automation_allowed": True, "manual_only": False, "eligibility_evidence": ""}))
    with pytest.raises(ValueError, match="selected_exchange"):
        build_instrument_identity_execution_routing(_request(instrument={"selected_exchange": ""}))


def test_unknown_fields_stale_evidence_and_invalid_fio_flags_fail_closed():
    request = _request()
    request["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        build_instrument_identity_execution_routing(request)
    with pytest.raises(ValueError, match="stale"):
        build_instrument_identity_execution_routing(_request(execution_route={"eligibility_as_of_date": "2020-01-01"}))
    with pytest.raises(ValueError, match="FIO"):
        build_instrument_identity_execution_routing(_request(execution_route={"automation_allowed": True}))


def test_hashes_are_deterministic_and_inputs_and_results_are_deeply_independent():
    request = _request()
    before = copy.deepcopy(request)
    first = build_instrument_identity_execution_routing(request)
    second = build_instrument_identity_execution_routing(request)
    assert request == before
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    first["instrument"]["provenance"]["source"] = "mutated"
    assert build_instrument_identity_execution_routing(request)["instrument"]["provenance"]["source"] == "official-test"
    assert first["safety_flags"] == {"broker_calls_used": 0, "provider_calls_used": 0, "network_used": False, "automatic_orders_generated": False, "automatic_liquidation_allowed": False, "production_runtime_supported": False}
