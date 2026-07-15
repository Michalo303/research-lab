import copy
import hashlib
import json

import pytest

from research_lab.execution.dual_broker_exposure_risk_v1 import build_dual_broker_exposure_risk
from research_lab.execution.fio_manual_long_term_inventory_v1 import build_fio_manual_long_term_inventory
from research_lab.execution.ibkr_active_execution_universe_v1 import build_ibkr_active_execution_universe
from research_lab.execution.instrument_identity_execution_routing_v1 import build_instrument_identity_execution_routing
from research_lab.execution.point_in_time_fx_conversion_contract_v1 import build_point_in_time_fx_conversion_contract
from research_lab.execution.research_execution_instrument_mapping_v1 import build_research_execution_instrument_mapping


def _identity(ticker, route, typ="COMMON_STOCK", currency="USD"):
    isin = "US0000000001" if ticker == "MSFT" else "US0000000002" if ticker == "USO" else "US0000000003"
    return build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": {"instrument_id": f"id-{ticker}", "legal_name": ticker, "instrument_type": typ, "security_type": "ETF" if typ != "COMMON_STOCK" else "COMMON_STOCK", "isin": isin, "primary_exchange": "XNAS", "selected_exchange": "XNAS", "exchange_ticker": ticker, "provider_symbol": None, "trading_currency": currency, "issuer": ticker + " issuer", "domicile": "US", "share_class_identity": "ORDINARY", "distribution_policy": "ACCUMULATING", "currency_hedging": "UNHEDGED", "legal_product_classification": typ, "kid_status": "NOT_REQUIRED_COMMON_STOCK", "point_in_time_metadata_status": "REVIEWED_POINT_IN_TIME", "metadata_as_of_date": "2026-07-15", "provenance": {"source": "test"}}, "execution_route": {"route": route, "automation_allowed": False, "manual_only": route == "FIO_MANUAL_LONG_TERM", "risk_inclusion_required": True, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False, "expected_holding_horizon": "THREE_YEARS_OR_LONGER" if route == "FIO_MANUAL_LONG_TERM" else "SWING", "eligibility_evidence": "test", "eligibility_as_of_date": "2026-07-15"}, "provenance": {"source": "test"}})


def _fio(tmp_path, ticker="USO", value="600.00", theme="ENERGY", currency="USD"):
    child = _identity(ticker, "FIO_MANUAL_LONG_TERM", "NON_UCITS_ETF")
    source = {"version": "fio_manual_long_term_inventory_source_v1", "inventory_id": "fio-1", "account_id_redacted": "fio-***", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": currency, "provenance": {"source": "test"}, "positions": [{"position_id": "fio-" + ticker, "identity_routing_result": child, "quantity": "10", "currency": currency, "average_cost": "1", "reference_price": "1", "reference_price_timestamp": "2026-07-15T12:00:00Z", "market_value": value, "acquisition_or_earliest_lot_date": "2024-01-01", "expected_holding_horizon": "THREE_YEARS_OR_LONGER", "provenance": {"source": "test"}}]}
    path = tmp_path / "fio.json"; path.write_text(json.dumps(source), encoding="utf-8")
    return build_fio_manual_long_term_inventory({"version": "fio_manual_long_term_inventory_request_v1", "inventory_id": "fio-1", "account_id_redacted": "fio-***", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": currency, "source_file_path": str(path), "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "maximum_bytes": 100000, "maximum_positions": 10, "provenance": {"source": "test"}})


def _universe(ticker="MSFT"):
    child = _identity(ticker, "IBKR_REVIEW_REQUIRED")
    candidate = {"candidate_id": ticker.lower(), "identity_routing_result": child, "exchange": "XNAS", "ticker": ticker, "trading_currency": "USD", "proposed_ibkr_execution_route": "IBKR_REVIEW_ONLY", "instrument_type": "COMMON_STOCK", "security_type": "COMMON_STOCK", "trading_permission_category": "RETAIL", "trading_permission_evidence": {"status": "EXPLICIT_CURRENT", "as_of_date": "2026-07-15", "source": "test"}, "kid_or_retail_documentation_status": "NOT_REQUIRED_COMMON_STOCK", "documentation_evidence": None, "offline_price_observation": {"value": "100", "timestamp": "2026-07-15T10:00:00Z"}, "offline_median_volume_observation": {"value": "1000000", "timestamp": "2026-07-15T10:00:00Z"}, "offline_spread_observation": {"value_bps": "1", "timestamp": "2026-07-15T10:00:00Z"}, "corporate_action_policy": "REVIEW", "delisting_policy": "BLOCK", "settlement_currency_policy": "MATCH", "allowed_order_types": ["LIMIT"], "regular_session_policy": "REGULAR_SESSION_ONLY", "provenance": {"source": "test"}}
    return build_ibkr_active_execution_universe({"version": "ibkr_active_execution_universe_request_v1", "universe_id": "u-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "candidates": [candidate], "universe_policy": {"long_only": True, "leverage_allowed": False, "margin_assumed": False, "shorting_allowed": False, "derivatives_allowed": False, "fractional_shares_assumed": False, "extended_hours_assumed": False}, "liquidity_policy": {"minimum_price": "5", "minimum_median_volume": "100", "maximum_spread_bps": "20"}, "eligibility_evidence_policy": {"maximum_evidence_age_days": 0, "require_explicit_retail_evidence": True}, "provenance": {"source": "test"}})


def _request(tmp_path, maximum="0.80", fio_value="600.00"):
    value = {"version": "dual_broker_exposure_risk_request_v1", "risk_request_id": "risk-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "fio_inventory_result": _fio(tmp_path, value=fio_value), "ibkr_universe_result": _universe(), "existing_ibkr_positions": [], "proposed_ibkr_intents": [{"intent_id": "intent-msft", "candidate_id": "msft", "quantity": "5", "currency": "USD", "valuation_evidence": {"market_value": "500.00", "timestamp": "2026-07-15T12:00:00Z"}, "issuer": "Microsoft", "sector": "TECHNOLOGY", "theme": "SOFTWARE", "asset_class": "EQUITY", "product_type": "COMMON_STOCK", "related_mapping_id": None, "provenance": {"source": "test"}}], "research_execution_mappings": [], "fx_conversion_results": [], "valuation_evidence": {"as_of_timestamp": "2026-07-15T12:00:00Z", "policy": "SUPPLIED_EXACT"}, "concentration_classifications": {"fio-USO": {"issuer": "USO issuer", "sector": "ENERGY", "theme": "OIL", "asset_class": "FUND", "product_type": "NON_UCITS_ETF"}}, "risk_limits": {"maximum_single_instrument_percentage": "1", "maximum_issuer_percentage": "1", "maximum_sector_percentage": "1", "maximum_theme_percentage": "1", "maximum_asset_class_percentage": "1", "maximum_currency_percentage": "1", "maximum_broker_percentage": "1", "maximum_product_type_percentage": "1", "maximum_combined_gross_exposure": maximum, "maximum_proposed_intent_percentage": "1", "maximum_unvalued_exposure_count": 0, "mapping_overlap_review_policy": "REQUIRE_REVIEW_WHEN_UNAVAILABLE"}, "provenance": {"source": "test"}}
    return _strictify(value)


def _strictify(value):
    """Attach actual same-currency FX and exact M31B evidence to a fixture."""
    value.pop("research_execution_mappings")
    value.pop("fx_conversion_results")
    intent = value["proposed_ibkr_intents"][0]
    intent.pop("related_mapping_id")
    intent.update({"research_source_policy": "DERIVED_FROM_RESEARCH", "research_execution_mapping_id": "map-msft"})
    identity = value["ibkr_universe_result"]["accepted_instruments"][0]["identity_routing_result"]
    value["research_execution_mapping_results"] = [_mapping(identity)]
    fio_position = value["fio_inventory_result"]["validated_positions"][0]
    value["point_in_time_fx_conversion_results"] = [_same_currency_fx([
        (fio_position["position_id"], fio_position["currency"], fio_position["market_value"], "a" * 64),
        (intent["intent_id"], intent["currency"], intent["valuation_evidence"]["market_value"], "b" * 64),
    ])]
    return value


def _mapping(identity):
    return build_research_execution_instrument_mapping({"version": "research_execution_instrument_mapping_request_v1", "mapping_id": "map-msft", "research_instrument_identity_result": identity, "execution_instrument_identity_result": identity, "mapping_type": "SAME_INSTRUMENT_SAME_LISTING", "economic_exposure": "MSFT", "benchmark_relationship": "EXACT", "currency_difference": "NONE", "underlying_economic_currency_difference": "NONE", "exchange_calendar_difference": "NONE", "fee_difference": "NONE", "legal_structure_difference": "NONE", "collateral_structure_difference": "NONE", "contango_backwardation_difference": "NOT_APPLICABLE", "distribution_difference": "NONE", "hedging_difference": "NONE", "corporate_action_difference": "NONE", "listing_identity_difference": "NONE", "benchmark_methodology_difference": "NONE", "futures_roll_difference": "NOT_APPLICABLE", "tracking_validation_policy": "EXACT_IDENTITY", "maximum_allowed_tracking_error": 0.0, "minimum_required_correlation": 1.0, "minimum_history_overlap": 1, "mapping_as_of_date": "2026-07-15", "provenance": {"source": "test"}})


def _same_currency_fx(values):
    instruments = [{"instrument_id": item_id, "currency": currency, "decision_timestamp": "2026-07-15T12:00:00Z", "value": amount, "source_identity": item_id, "source_sha256": source_hash, "provenance": {"source": "test"}} for item_id, currency, amount, source_hash in values]
    return build_point_in_time_fx_conversion_contract({"version": "point_in_time_fx_conversion_contract_request_v1", "conversion_id": "fx-m31e", "base_currency": "USD", "instrument_values": instruments, "fx_observations": [], "decision_timestamps": {item["instrument_id"]: item["decision_timestamp"] for item in instruments}, "maximum_staleness_seconds": 0, "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR", "inverse_rate_policy": "REJECT_INVERSE", "cross_rate_policy": "REJECT_CROSS_RATE", "declared_cross_paths": [], "expected_source_hashes": {"instrument_values": {item["instrument_id"]: item["source_sha256"] for item in instruments}, "fx_observations": {}}, "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"}, "provenance": {"source": "test"}})


def test_fio_locked_exposure_resizes_review_only_eligible_intent_deterministically(tmp_path):
    request = _request(tmp_path)
    first = build_dual_broker_exposure_risk(request)
    second = build_dual_broker_exposure_risk(request)
    assert first["status"] == "ACCEPTED_REVIEW_ONLY"
    assert first["intent_decisions"] == [{"intent_id": "intent-msft", "decision": "RESIZE_REVIEW_ONLY", "requested_notional": "500.00", "maximum_permitted_notional": "280.00", "binding_limit": "maximum_combined_gross_exposure", "reason": "FIO_LOCKED_EXPOSURE_REDUCES_CAPACITY"}]
    assert first["account_exposures"]["fio_locked_exposure"] == "600.00"
    assert first["safety_flags"]["broker_calls_used"] == 0
    assert first["safety_flags"]["production_runtime_supported"] is False
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    assert request["proposed_ibkr_intents"][0]["quantity"] == "5"


def test_fio_can_block_and_child_hashes_unknown_fields_and_unvalued_exposure_fail_closed(tmp_path):
    request_blocked = _request(tmp_path, fio_value="1000.00"); request_blocked["risk_limits"]["maximum_combined_gross_exposure"] = "0.50"
    blocked = build_dual_broker_exposure_risk(request_blocked)
    assert blocked["intent_decisions"][0]["decision"] == "BLOCK_REVIEW_ONLY"
    request = _request(tmp_path); request["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        build_dual_broker_exposure_risk(request)
    request = _request(tmp_path); request["fio_inventory_result"]["output_payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        build_dual_broker_exposure_risk(request)


def test_overlap_concentration_missing_valuation_and_immutability_are_fail_closed(tmp_path):
    request = _request(tmp_path)
    request["fio_inventory_result"] = _fio(tmp_path, ticker="SMH", theme="SEMICONDUCTORS")
    request["concentration_classifications"] = {"fio-SMH": {"issuer": "VanEck", "sector": "TECHNOLOGY", "theme": "SEMICONDUCTORS", "asset_class": "FUND", "product_type": "ETF"}}
    request["proposed_ibkr_intents"][0].update({"sector": "TECHNOLOGY", "theme": "SEMICONDUCTORS"})
    request["point_in_time_fx_conversion_results"] = [_same_currency_fx([("fio-SMH", "USD", "600.00", "a" * 64), ("intent-msft", "USD", "500.00", "b" * 64)])]
    reviewed = build_dual_broker_exposure_risk(request)
    assert reviewed["status"] == "REVIEW_REQUIRED"
    assert reviewed["intent_decisions"][0]["decision"] == "REQUIRE_OVERLAP_REVIEW"
    request = _request(tmp_path); request["risk_limits"]["maximum_issuer_percentage"] = "0.10"
    assert build_dual_broker_exposure_risk(request)["status"] == "FAILED_VALIDATION"
    request = _request(tmp_path); request["proposed_ibkr_intents"][0]["valuation_evidence"] = {}
    with pytest.raises(ValueError, match="valuation"):
        build_dual_broker_exposure_risk(request)
    request = _request(tmp_path); before = copy.deepcopy(request)
    build_dual_broker_exposure_risk(request)
    assert request == before


def test_fx_and_mapping_children_are_composed_before_portfolio_aggregation(tmp_path):
    """M31E must consume, hash-verify, and expose actual M31B and FX children."""
    request = _request(tmp_path)
    """The fixture already supplies exact same-currency FX and mapping results."""
    candidate_identity = request["ibkr_universe_result"]["accepted_instruments"][0]["identity_routing_result"]
    mapping = build_research_execution_instrument_mapping({
        "version": "research_execution_instrument_mapping_request_v1",
        "mapping_id": "map-msft",
        "research_instrument_identity_result": candidate_identity,
        "execution_instrument_identity_result": candidate_identity,
        "mapping_type": "SAME_INSTRUMENT_SAME_LISTING",
        "economic_exposure": "MSFT",
        "benchmark_relationship": "EXACT",
        "currency_difference": "NONE",
        "underlying_economic_currency_difference": "NONE",
        "exchange_calendar_difference": "NONE",
        "fee_difference": "NONE",
        "legal_structure_difference": "NONE",
        "collateral_structure_difference": "NONE",
        "contango_backwardation_difference": "NOT_APPLICABLE",
        "distribution_difference": "NONE",
        "hedging_difference": "NONE",
        "corporate_action_difference": "NONE",
        "listing_identity_difference": "NONE",
        "benchmark_methodology_difference": "NONE",
        "futures_roll_difference": "NOT_APPLICABLE",
        "tracking_validation_policy": "EXACT_IDENTITY",
        "maximum_allowed_tracking_error": 0.0,
        "minimum_required_correlation": 1.0,
        "minimum_history_overlap": 1,
        "mapping_as_of_date": "2026-07-15",
        "provenance": {"source": "test"},
    })
    fx = build_point_in_time_fx_conversion_contract({
        "version": "point_in_time_fx_conversion_contract_request_v1",
        "conversion_id": "fx-m31e",
        "base_currency": "USD",
        "instrument_values": [
            {"instrument_id": "fio-USO", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "600.00", "source_identity": "fio", "source_sha256": "a" * 64, "provenance": {"source": "test"}},
            {"instrument_id": "intent-msft", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "500.00", "source_identity": "intent", "source_sha256": "b" * 64, "provenance": {"source": "test"}},
        ],
        "fx_observations": [],
        "decision_timestamps": {"fio-USO": "2026-07-15T12:00:00Z", "intent-msft": "2026-07-15T12:00:00Z"},
        "maximum_staleness_seconds": 0,
        "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR",
        "inverse_rate_policy": "REJECT_INVERSE",
        "cross_rate_policy": "REJECT_CROSS_RATE",
        "declared_cross_paths": [],
        "expected_source_hashes": {"instrument_values": {"fio-USO": "a" * 64, "intent-msft": "b" * 64}, "fx_observations": {}},
        "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"},
        "provenance": {"source": "test"},
    })
    request["research_execution_mapping_results"] = [mapping]
    request["point_in_time_fx_conversion_results"] = [fx]

    result = build_dual_broker_exposure_risk(request)

    assert result["portfolio_totals"]["total_valued_portfolio_amount"] == "1100.00"
    assert result["fx_conversion_lineage"][0]["conversion_method"] == "SAME_CURRENCY"
    assert result["mapping_lineage"][0]["mapping_id"] == "map-msft"


def _rehash(child):
    payload = copy.deepcopy(child)
    payload.pop("output_payload_sha256")
    child["output_payload_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    return child


@pytest.mark.parametrize("mode, observations, inverse, cross, expected", [
    ("DIRECT", [("eur-usd", "EUR", "USD", "1.200000")], "REJECT_INVERSE", [], "1220.00"),
    ("INVERSE", [("usd-eur", "USD", "EUR", "0.800000")], "ALLOW_EXPLICIT_INVERSE", [], "1250.00"),
    ("CROSS", [("eur-gbp", "EUR", "GBP", "0.800000"), ("gbp-usd", "GBP", "USD", "1.500000")], "REJECT_INVERSE", [{"path_id": "eur-gbp-usd", "source_currency": "EUR", "intermediary_currency": "GBP", "target_currency": "USD", "first_pair_id": "EUR-GBP", "first_arithmetic_orientation": "MULTIPLY", "second_pair_id": "GBP-USD", "second_arithmetic_orientation": "MULTIPLY", "maximum_combined_staleness_seconds": 0, "provenance": {"source": "test"}}], "1220.00"),
])
def test_actual_fx_direct_inverse_and_cross_children_convert_before_aggregation(tmp_path, mode, observations, inverse, cross, expected):
    request = _request(tmp_path)
    request["fio_inventory_result"] = _fio(tmp_path, currency="EUR")
    fio = request["fio_inventory_result"]["validated_positions"][0]
    instruments = [
        {"instrument_id": fio["position_id"], "currency": "EUR", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "600.00", "source_identity": "fio", "source_sha256": "a" * 64, "provenance": {"source": "test"}},
        {"instrument_id": "intent-msft", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "500.00", "source_identity": "intent", "source_sha256": "b" * 64, "provenance": {"source": "test"}},
    ]
    fx_observations = [{"observation_id": oid, "pair_id": f"{base}-{quote}", "base_currency": base, "quote_currency": quote, "observation_timestamp": "2026-07-15T12:00:00Z", "availability_timestamp": "2026-07-15T12:00:00Z", "rate": rate, "source_identity": "test", "source_sha256": "c" * 64, "point_in_time_status": "POINT_IN_TIME_VERIFIED", "provenance": {"source": "test"}} for oid, base, quote, rate in observations]
    request["point_in_time_fx_conversion_results"] = [build_point_in_time_fx_conversion_contract({"version": "point_in_time_fx_conversion_contract_request_v1", "conversion_id": f"fx-{mode}", "base_currency": "USD", "instrument_values": instruments, "fx_observations": fx_observations, "decision_timestamps": {x["instrument_id"]: x["decision_timestamp"] for x in instruments}, "maximum_staleness_seconds": 0, "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR", "inverse_rate_policy": inverse, "cross_rate_policy": "ALLOW_DECLARED_CROSS_PATHS" if cross else "REJECT_CROSS_RATE", "declared_cross_paths": cross, "expected_source_hashes": {"instrument_values": {x["instrument_id"]: x["source_sha256"] for x in instruments}, "fx_observations": {x["observation_id"]: x["source_sha256"] for x in fx_observations}}, "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"}, "provenance": {"source": "test"}})]
    result = build_dual_broker_exposure_risk(request)
    assert result["portfolio_totals"]["total_valued_portfolio_amount"] == expected
    assert next(x for x in result["fx_conversion_lineage"] if x["exposure_id"] == fio["position_id"])["conversion_method"] == mode


@pytest.mark.parametrize("mutation, match", [
    (lambda r: r["point_in_time_fx_conversion_results"][0]["converted_values"].pop(), "missing required FX"),
    (lambda r: r["point_in_time_fx_conversion_results"].append(copy.deepcopy(r["point_in_time_fx_conversion_results"][0])), "duplicate or ambiguous"),
    (lambda r: r["point_in_time_fx_conversion_results"][0]["converted_values"][0].update({"source_currency": "EUR"}), "source currency"),
    (lambda r: r["point_in_time_fx_conversion_results"][0]["converted_values"][0].update({"target_currency": "EUR"}), "target currency"),
    (lambda r: r["point_in_time_fx_conversion_results"][0]["converted_values"][0].update({"decision_timestamp": "2026-07-15T12:00:01Z"}), "timestamp"),
    (lambda r: r["point_in_time_fx_conversion_results"][0].update({"conversion_status": "FAILED"}), "failed or stale"),
    (lambda r: r["point_in_time_fx_conversion_results"][0].update({"unexpected": True}), "malformed FX child fields"),
])
def test_fx_child_failures_and_ambiguity_fail_closed(tmp_path, mutation, match):
    request = _request(tmp_path); mutation(request)
    for child in request["point_in_time_fx_conversion_results"]:
        if "unexpected" not in child: _rehash(child)
    with pytest.raises(ValueError, match=match): build_dual_broker_exposure_risk(request)


def test_fx_hash_mismatch_mapping_hash_mismatch_and_missing_mapping_fail_closed(tmp_path):
    request = _request(tmp_path); request["point_in_time_fx_conversion_results"][0]["output_payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"): build_dual_broker_exposure_risk(request)
    request = _request(tmp_path); request["research_execution_mapping_results"][0]["output_payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"): build_dual_broker_exposure_risk(request)
    request = _request(tmp_path); request["research_execution_mapping_results"] = []
    with pytest.raises(ValueError, match="missing required M31B mapping"): build_dual_broker_exposure_risk(request)


def test_related_m31b_mapping_links_overlap_to_the_held_research_identity(tmp_path):
    request = _request(tmp_path)
    execution = request["ibkr_universe_result"]["accepted_instruments"][0]["identity_routing_result"]
    research = _identity("USO", "FIO_MANUAL_LONG_TERM", "NON_UCITS_ETF")
    mapping_request = {"version": "research_execution_instrument_mapping_request_v1", "mapping_id": "map-uso-msft", "research_instrument_identity_result": research, "execution_instrument_identity_result": execution, "mapping_type": "RELATED_EXPOSURE_NOT_IDENTICAL", "economic_exposure": "ENERGY_RELATED", "benchmark_relationship": "RELATED", "currency_difference": "NONE", "underlying_economic_currency_difference": "NONE", "exchange_calendar_difference": "NONE", "fee_difference": "NONE", "legal_structure_difference": "EXPLICIT", "collateral_structure_difference": "NOT_APPLICABLE", "contango_backwardation_difference": "EXPLICIT", "distribution_difference": "NONE", "hedging_difference": "NONE", "corporate_action_difference": "NONE", "listing_identity_difference": "EXPLICIT", "benchmark_methodology_difference": "NONE", "futures_roll_difference": "EXPLICIT", "tracking_validation_policy": "REQUIRE_HISTORY", "maximum_allowed_tracking_error": 0.05, "minimum_required_correlation": 0.5, "minimum_history_overlap": 1, "mapping_as_of_date": "2026-07-15", "provenance": {"source": "test"}}
    request["research_execution_mapping_results"] = [build_research_execution_instrument_mapping(mapping_request)]
    request["proposed_ibkr_intents"][0]["research_execution_mapping_id"] = "map-uso-msft"
    result = build_dual_broker_exposure_risk(request)
    assert result["intent_decisions"][0]["decision"] == "REQUIRE_OVERLAP_REVIEW"
    assert result["overlap_findings"] == ["RELATED_EXPOSURE_NOT_IDENTICAL_OVERLAP:fio-USO:intent-msft:MAPPING:map-uso-msft"]
    assert result["related_exposure_groups"]["map-uso-msft"] == "500.00"
