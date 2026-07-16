import copy
import hashlib
import json

import pytest

import research_lab.execution.e2e_dual_broker_foundation_acceptance_v1 as acceptance_module
from research_lab.execution.dual_broker_exposure_risk_v1 import build_dual_broker_exposure_risk
from research_lab.execution.e2e_dual_broker_foundation_acceptance_v1 import (
    build_e2e_dual_broker_foundation_acceptance,
)
from research_lab.execution.fio_manual_long_term_inventory_v1 import build_fio_manual_long_term_inventory
from research_lab.execution.ibkr_active_execution_universe_v1 import build_ibkr_active_execution_universe
from research_lab.execution.instrument_identity_execution_routing_v1 import build_instrument_identity_execution_routing
from research_lab.execution.point_in_time_fx_conversion_contract_v1 import build_point_in_time_fx_conversion_contract
from research_lab.execution.research_execution_instrument_mapping_v1 import build_research_execution_instrument_mapping


def _rehash(value):
    payload = copy.deepcopy(value)
    payload.pop("output_payload_sha256", None)
    value["output_payload_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False, default=str).encode("utf-8")).hexdigest()


def _identity(ticker, route, instrument_type="COMMON_STOCK", currency="USD"):
    return build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": {"instrument_id": f"id-{ticker}", "legal_name": ticker, "instrument_type": instrument_type, "security_type": "ETF" if instrument_type != "COMMON_STOCK" else "COMMON_STOCK", "isin": "US0000000001" if ticker == "MSFT" else "US0000000002", "primary_exchange": "XNAS", "selected_exchange": "XNAS", "exchange_ticker": ticker, "provider_symbol": None, "trading_currency": currency, "issuer": f"{ticker} issuer", "domicile": "US", "share_class_identity": "ORDINARY", "distribution_policy": "ACCUMULATING", "currency_hedging": "UNHEDGED", "legal_product_classification": instrument_type, "kid_status": "NOT_REQUIRED_COMMON_STOCK", "point_in_time_metadata_status": "REVIEWED_POINT_IN_TIME", "metadata_as_of_date": "2026-07-15", "provenance": {"source": "test"}}, "execution_route": {"route": route, "automation_allowed": False, "manual_only": route == "FIO_MANUAL_LONG_TERM", "risk_inclusion_required": True, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False, "expected_holding_horizon": "THREE_YEARS_OR_LONGER" if route == "FIO_MANUAL_LONG_TERM" else "SWING", "eligibility_evidence": "test", "eligibility_as_of_date": "2026-07-15"}, "provenance": {"source": "test"}})


def _request(tmp_path):
    fio_identity, ibkr_identity = _identity("USO", "FIO_MANUAL_LONG_TERM", "NON_UCITS_ETF"), _identity("MSFT", "IBKR_REVIEW_REQUIRED")
    source = {"version": "fio_manual_long_term_inventory_source_v1", "inventory_id": "fio-1", "account_id_redacted": "fio-***", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "provenance": {"source": "test"}, "positions": [{"position_id": "fio-USO", "identity_routing_result": fio_identity, "quantity": "10", "currency": "USD", "average_cost": "1", "reference_price": "1", "reference_price_timestamp": "2026-07-15T12:00:00Z", "market_value": "600.00", "acquisition_or_earliest_lot_date": "2024-01-01", "expected_holding_horizon": "THREE_YEARS_OR_LONGER", "provenance": {"source": "test"}}]}
    path = tmp_path / "fio.json"; path.write_text(json.dumps(source), encoding="utf-8")
    fio = build_fio_manual_long_term_inventory({"version": "fio_manual_long_term_inventory_request_v1", "inventory_id": "fio-1", "account_id_redacted": "fio-***", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "source_file_path": str(path), "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "maximum_bytes": 100000, "maximum_positions": 10, "provenance": {"source": "test"}})
    universe = build_ibkr_active_execution_universe({"version": "ibkr_active_execution_universe_request_v1", "universe_id": "u-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "candidates": [{"candidate_id": "msft", "identity_routing_result": ibkr_identity, "exchange": "XNAS", "ticker": "MSFT", "trading_currency": "USD", "proposed_ibkr_execution_route": "IBKR_REVIEW_ONLY", "instrument_type": "COMMON_STOCK", "security_type": "COMMON_STOCK", "trading_permission_category": "RETAIL", "trading_permission_evidence": {"status": "EXPLICIT_CURRENT", "as_of_date": "2026-07-15", "source": "test"}, "kid_or_retail_documentation_status": "NOT_REQUIRED_COMMON_STOCK", "documentation_evidence": None, "offline_price_observation": {"value": "100", "timestamp": "2026-07-15T10:00:00Z"}, "offline_median_volume_observation": {"value": "1000000", "timestamp": "2026-07-15T10:00:00Z"}, "offline_spread_observation": {"value_bps": "1", "timestamp": "2026-07-15T10:00:00Z"}, "corporate_action_policy": "REVIEW", "delisting_policy": "BLOCK", "settlement_currency_policy": "MATCH", "allowed_order_types": ["LIMIT"], "regular_session_policy": "REGULAR_SESSION_ONLY", "provenance": {"source": "test"}}], "universe_policy": {"long_only": True, "leverage_allowed": False, "margin_assumed": False, "shorting_allowed": False, "derivatives_allowed": False, "fractional_shares_assumed": False, "extended_hours_assumed": False}, "liquidity_policy": {"minimum_price": "5", "minimum_median_volume": "100", "maximum_spread_bps": "20"}, "eligibility_evidence_policy": {"maximum_evidence_age_days": 0, "require_explicit_retail_evidence": True}, "provenance": {"source": "test"}})
    mapping = build_research_execution_instrument_mapping({"version": "research_execution_instrument_mapping_request_v1", "mapping_id": "map-msft", "research_instrument_identity_result": ibkr_identity, "execution_instrument_identity_result": ibkr_identity, "mapping_type": "SAME_INSTRUMENT_SAME_LISTING", "economic_exposure": "MSFT", "benchmark_relationship": "EXACT", "currency_difference": "NONE", "underlying_economic_currency_difference": "NONE", "exchange_calendar_difference": "NONE", "fee_difference": "NONE", "legal_structure_difference": "NONE", "collateral_structure_difference": "NONE", "contango_backwardation_difference": "NOT_APPLICABLE", "distribution_difference": "NONE", "hedging_difference": "NONE", "corporate_action_difference": "NONE", "listing_identity_difference": "NONE", "benchmark_methodology_difference": "NONE", "futures_roll_difference": "NOT_APPLICABLE", "tracking_validation_policy": "EXACT_IDENTITY", "maximum_allowed_tracking_error": 0.0, "minimum_required_correlation": 1.0, "minimum_history_overlap": 1, "mapping_as_of_date": "2026-07-15", "provenance": {"source": "test"}})
    fx = build_point_in_time_fx_conversion_contract({"version": "point_in_time_fx_conversion_contract_request_v1", "conversion_id": "fx-1", "base_currency": "USD", "instrument_values": [{"instrument_id": "fio-USO", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "600.00", "source_identity": "fio", "source_sha256": "a" * 64, "provenance": {"source": "test"}}, {"instrument_id": "intent-msft", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "500.00", "source_identity": "intent", "source_sha256": "b" * 64, "provenance": {"source": "test"}}], "fx_observations": [], "decision_timestamps": {"fio-USO": "2026-07-15T12:00:00Z", "intent-msft": "2026-07-15T12:00:00Z"}, "maximum_staleness_seconds": 0, "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR", "inverse_rate_policy": "REJECT_INVERSE", "cross_rate_policy": "REJECT_CROSS_RATE", "declared_cross_paths": [], "expected_source_hashes": {"instrument_values": {"fio-USO": "a" * 64, "intent-msft": "b" * 64}, "fx_observations": {}}, "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"}, "provenance": {"source": "test"}})
    risk = {"version": "dual_broker_exposure_risk_request_v1", "risk_request_id": "risk-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "fio_inventory_result": fio, "ibkr_universe_result": universe, "existing_ibkr_positions": [], "proposed_ibkr_intents": [{"intent_id": "intent-msft", "candidate_id": "msft", "quantity": "5", "currency": "USD", "valuation_evidence": {"market_value": "500.00", "timestamp": "2026-07-15T12:00:00Z"}, "issuer": "Microsoft", "sector": "TECHNOLOGY", "theme": "SOFTWARE", "asset_class": "EQUITY", "product_type": "COMMON_STOCK", "research_source_policy": "DERIVED_FROM_RESEARCH", "research_execution_mapping_id": "map-msft", "provenance": {"source": "test"}}], "research_execution_mapping_results": [mapping], "point_in_time_fx_conversion_results": [fx], "valuation_evidence": {"as_of_timestamp": "2026-07-15T12:00:00Z", "policy": "SUPPLIED_EXACT"}, "concentration_classifications": {"fio-USO": {"issuer": "USO issuer", "sector": "ENERGY", "theme": "OIL", "asset_class": "FUND", "product_type": "NON_UCITS_ETF"}}, "risk_limits": {key: "1" for key in ("maximum_single_instrument_percentage", "maximum_issuer_percentage", "maximum_sector_percentage", "maximum_theme_percentage", "maximum_asset_class_percentage", "maximum_currency_percentage", "maximum_broker_percentage", "maximum_product_type_percentage", "maximum_combined_gross_exposure", "maximum_proposed_intent_percentage")} | {"maximum_unvalued_exposure_count": 0, "mapping_overlap_review_policy": "REQUIRE_REVIEW_WHEN_UNAVAILABLE"}, "provenance": {"source": "test"}}
    risk_result = build_dual_broker_exposure_risk(risk)
    lineage = {"m31a_output_sha256": [fio_identity["output_payload_sha256"], ibkr_identity["output_payload_sha256"]], "m31b_output_sha256": [mapping["output_payload_sha256"]], "m31c_source_sha256": fio["source_sha256"], "m31c_output_sha256": fio["output_payload_sha256"], "m31d_output_sha256": universe["output_payload_sha256"], "fx_output_sha256": [fx["output_payload_sha256"]], "m31e_input_sha256": risk_result["input_sha256"], "m31e_output_sha256": risk_result["output_payload_sha256"], "stage_order": ["INSTRUMENT_IDENTITY", "EXECUTION_ROUTING", "RESEARCH_EXECUTION_MAPPING", "FIO_INVENTORY", "IBKR_UNIVERSE", "FX", "DUAL_BROKER_RISK", "ACCEPTANCE_VALIDATION"]}
    return {"version": "e2e_dual_broker_foundation_acceptance_request_v1", "acceptance_request_id": "accept-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "identity_routing_results": [fio_identity, ibkr_identity], "research_execution_mapping_results": [mapping], "fio_inventory_result": fio, "ibkr_universe_result": universe, "point_in_time_fx_conversion_results": [fx], "dual_broker_risk_request": risk, "expected_child_lineage": lineage, "replay_policy": {"mode": "VERIFY_DETERMINISTIC"}, "provenance": {"source": "test"}}


def _request_v2(tmp_path):
    legacy = _request(tmp_path)
    identities = legacy["identity_routing_results"]
    mapping = legacy["research_execution_mapping_results"][0]
    mapping_request = {"version": "research_execution_instrument_mapping_request_v1", "mapping_id": mapping["mapping_id"], "mapping_type": mapping["mapping_type"], "economic_exposure": "MSFT", "benchmark_relationship": "EXACT", **mapping["material_differences"], **mapping["tracking_requirements"], "mapping_as_of_date": "2026-07-15", "provenance": mapping["provenance"]}
    candidate = copy.deepcopy(legacy["ibkr_universe_result"]["instrument_results"][0])
    for key in ("eligibility_status", "identity_key", "findings", "automatic_execution_allowed", "automatic_order_generation_allowed"):
        candidate.pop(key)
    universe = legacy["ibkr_universe_result"]
    risk = copy.deepcopy(legacy["dual_broker_risk_request"])
    for key in ("version", "fio_inventory_result", "ibkr_universe_result", "research_execution_mapping_results", "point_in_time_fx_conversion_results"):
        risk.pop(key)
    fx_request = {"version": "point_in_time_fx_conversion_contract_request_v1", "conversion_id": "fx-1", "base_currency": "USD", "instrument_values": [{"instrument_id": "fio-USO", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "600.00", "source_identity": "fio", "source_sha256": "a" * 64, "provenance": {"source": "test"}}, {"instrument_id": "intent-msft", "currency": "USD", "decision_timestamp": "2026-07-15T12:00:00Z", "value": "500.00", "source_identity": "intent", "source_sha256": "b" * 64, "provenance": {"source": "test"}}], "fx_observations": [], "decision_timestamps": {"fio-USO": "2026-07-15T12:00:00Z", "intent-msft": "2026-07-15T12:00:00Z"}, "maximum_staleness_seconds": 0, "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR", "inverse_rate_policy": "REJECT_INVERSE", "cross_rate_policy": "REJECT_CROSS_RATE", "declared_cross_paths": [], "expected_source_hashes": {"instrument_values": {"fio-USO": "a" * 64, "intent-msft": "b" * 64}, "fx_observations": {}}, "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"}, "provenance": {"source": "test"}}
    return {"version": "e2e_dual_broker_foundation_acceptance_request_v1", "acceptance_request_id": "accept-1", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "instrument_identity_requests": [{"version": "instrument_identity_execution_routing_request_v1", "instrument": x["instrument"], "execution_route": x["execution_route"], "provenance": x["provenance"]} for x in identities], "research_execution_mapping_requests": [{"mapping_request": mapping_request, "research_instrument_id": "id-MSFT", "execution_instrument_id": "id-MSFT"}], "fio_inventory_request": {"version": "fio_manual_long_term_inventory_request_v1", "inventory_id": "fio-1", "account_id_redacted": "fio-***", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "source_file_path": str(tmp_path / "fio.json"), "expected_source_sha256": hashlib.sha256((tmp_path / "fio.json").read_bytes()).hexdigest(), "maximum_bytes": 100000, "maximum_positions": 10, "provenance": {"source": "test"}}, "ibkr_universe_request": {"version": "ibkr_active_execution_universe_request_v1", "universe_id": universe["universe_id"], "as_of_timestamp": universe["as_of_timestamp"], "base_currency": universe["base_currency"], "candidates": [candidate], "universe_policy": universe["universe_policy"], "liquidity_policy": universe["liquidity_policy"], "eligibility_evidence_policy": universe["eligibility_evidence_policy"], "provenance": universe["provenance"]}, "fx_conversion_requests": [fx_request], "risk_request_inputs": risk, "expected_lineage": {}, "replay_policy": {"mode": "VERIFY_DETERMINISTIC"}, "provenance": {"source": "test"}}


def test_composes_actual_child_requests_and_replays_deterministically(tmp_path):
    request = _request_v2(tmp_path)
    before = copy.deepcopy(request)
    result = build_e2e_dual_broker_foundation_acceptance(request)
    assert result["acceptance_status"] == "ACCEPTED_REVIEW_ONLY"
    assert result["replay_status"] == "REPLAY_MATCH"
    assert result["child_lineage"]["m31e"]["output_payload_sha256"] == result["m31e_decision_evidence"]["output_payload_sha256"]
    assert request == before


def test_rejects_the_obsolete_supplied_child_results_contract(tmp_path):
    with pytest.raises(ValueError, match="obsolete supplied-child-results"):
        build_e2e_dual_broker_foundation_acceptance(_request(tmp_path))


def test_malformed_expected_replay_hash_returns_replay_failed_validation(tmp_path):
    request = _request_v2(tmp_path)
    request["replay_policy"] = {
        "mode": "VERIFY_DETERMINISTIC",
        "expected_replay_hash": "not-a-sha256",
    }

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["replay_status"] == "REPLAY_FAILED_VALIDATION"
    assert result["failed_stage"] == "REPLAY_VALIDATION"


@pytest.mark.parametrize(
    "field",
    [
        "fio_inventory_result",
        "research_execution_mapping_results",
        "ibkr_universe_result",
        "point_in_time_fx_conversion_results",
        "dual_broker_exposure_risk_result",
        "child_results",
    ],
)
def test_rejects_all_authoritative_child_result_injection_fields(tmp_path, field):
    request = _request_v2(tmp_path)
    request[field] = {"fabricated": "success"}

    with pytest.raises(ValueError):
        build_e2e_dual_broker_foundation_acceptance(request)


def test_rejects_nested_risk_child_result_injection(tmp_path):
    request = _request_v2(tmp_path)
    request["risk_request_inputs"]["fio_inventory_result"] = {"forged": "success"}

    with pytest.raises(ValueError):
        build_e2e_dual_broker_foundation_acceptance(request)


def test_fio_review_required_is_never_promoted_to_accepted(tmp_path):
    request = _request_v2(tmp_path)
    source_path = tmp_path / "fio.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["positions"][0]["market_value"] = None
    source_path.write_text(json.dumps(source), encoding="utf-8")
    request["fio_inventory_request"]["expected_source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    request["risk_request_inputs"]["risk_limits"]["maximum_unvalued_exposure_count"] = 1

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["acceptance_status"] == "REVIEW_REQUIRED"


def test_rebuilds_all_mandatory_children_and_preserves_same_run_lineage(tmp_path, monkeypatch):
    request = _request_v2(tmp_path)
    calls = {name: 0 for name in ("identity", "mapping", "fio", "ibkr", "fx", "risk")}
    originals = {
        "identity": acceptance_module.build_instrument_identity_execution_routing,
        "mapping": acceptance_module.build_research_execution_instrument_mapping,
        "fio": acceptance_module.build_fio_manual_long_term_inventory,
        "ibkr": acceptance_module.build_ibkr_active_execution_universe,
        "fx": acceptance_module.build_point_in_time_fx_conversion_contract,
        "risk": acceptance_module.build_dual_broker_exposure_risk,
    }
    for name, original in originals.items():
        def spy(value, *, _name=name, _original=original):
            calls[_name] += 1
            return _original(value)
        monkeypatch.setattr(acceptance_module, f"build_{'instrument_identity_execution_routing' if name == 'identity' else 'research_execution_instrument_mapping' if name == 'mapping' else 'fio_manual_long_term_inventory' if name == 'fio' else 'ibkr_active_execution_universe' if name == 'ibkr' else 'point_in_time_fx_conversion_contract' if name == 'fx' else 'dual_broker_exposure_risk'}", spy)

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["replay_status"] == "REPLAY_MATCH"
    assert all(count >= 2 for count in calls.values())
    risk = result["m31e_decision_evidence"]
    assert result["child_lineage"]["m31c"]["output_payload_sha256"] == risk["recomputed_child_hashes"]["fio_inventory_result"]
    assert result["child_lineage"]["m31d"]["output_payload_sha256"] == risk["recomputed_child_hashes"]["ibkr_universe_result"]
    assert result["child_lineage"]["m31e"]["output_payload_sha256"] == risk["output_payload_sha256"]
    assert result["safety_fields"]["provider_calls_used"] == 0
    assert result["safety_fields"]["broker_calls_used"] == 0
    assert result["safety_fields"]["executable_orders_generated"] is False
    assert result["safety_fields"]["production_runtime_supported"] is False


def test_expected_lineage_detects_changed_mapping_request(tmp_path):
    request = _request_v2(tmp_path)
    baseline = build_e2e_dual_broker_foundation_acceptance(request)
    request["expected_lineage"] = baseline["child_lineage"]
    request["research_execution_mapping_requests"][0]["mapping_request"]["economic_exposure"] = "CHANGED"

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == "ACCEPTANCE_VALIDATION"


@pytest.mark.parametrize(
    ("mutation", "stage"),
    [
        (lambda request: request["research_execution_mapping_requests"][0].update({"research_instrument_id": "missing"}), "RESEARCH_EXECUTION_MAPPING"),
        (lambda request: request["fio_inventory_request"].update({"expected_source_sha256": "0" * 64}), "FIO_INVENTORY"),
        (lambda request: request["ibkr_universe_request"]["candidates"][0]["trading_permission_evidence"].update({"status": "STALE"}), "IBKR_UNIVERSE"),
        (lambda request: request["fx_conversion_requests"][0].update({"maximum_staleness_seconds": -1}), "FX"),
        (lambda request: request["risk_request_inputs"]["risk_limits"].update({"maximum_single_instrument_percentage": "0"}), "DUAL_BROKER_RISK"),
    ],
)
def test_attributes_representative_child_failures_to_the_required_stage(tmp_path, mutation, stage):
    request = _request_v2(tmp_path)
    mutation(request)

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == stage


def test_attributes_route_validation_failure_to_execution_routing(tmp_path):
    request = _request_v2(tmp_path)
    request["instrument_identity_requests"][0]["execution_route"]["automatic_liquidation_allowed"] = True

    result = build_e2e_dual_broker_foundation_acceptance(request)

    assert result["acceptance_status"] == "FAILED_VALIDATION"
    assert result["failed_stage"] == "EXECUTION_ROUTING"


def test_failed_stage_records_completed_prerequisites_and_skips_later_stages(tmp_path):
    request = _request_v2(tmp_path)
    request["research_execution_mapping_requests"][0]["research_instrument_id"] = "missing"

    result = build_e2e_dual_broker_foundation_acceptance(request)
    stages = {item["stage"]: item["status"] for item in result["stage_results"]}

    assert stages["INSTRUMENT_IDENTITY"] == "PASS"
    assert stages["EXECUTION_ROUTING"] == "PASS"
    assert stages["RESEARCH_EXECUTION_MAPPING"] == "FAILED"
    assert stages["FIO_INVENTORY"] == "NOT_RUN"
