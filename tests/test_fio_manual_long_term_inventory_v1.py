import copy
import hashlib
import json

import pytest

from research_lab.execution.instrument_identity_execution_routing_v1 import (
    build_instrument_identity_execution_routing,
)
from research_lab.execution.fio_manual_long_term_inventory_v1 import (
    build_fio_manual_long_term_inventory,
)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _identity(ticker="USO"):
    instrument = {
        "instrument_id": f"fio-{ticker.lower()}-us-arcx", "legal_name": "United States Oil Fund, LP" if ticker == "USO" else "VanEck Semiconductor ETF",
        "instrument_type": "NON_UCITS_ETF", "security_type": "COMMODITY_FUTURES_FUND" if ticker == "USO" else "ETF",
        "isin": "US91288V1035" if ticker == "USO" else "US92189F6768", "primary_exchange": "ARCX", "selected_exchange": "ARCX",
        "exchange_ticker": ticker, "provider_symbol": f"{ticker}.US", "trading_currency": "USD", "issuer": "test issuer", "domicile": "US",
        "share_class_identity": "LIMITED_PARTNERSHIP_UNIT", "distribution_policy": "NONE_DECLARED", "currency_hedging": "UNHEDGED",
        "legal_product_classification": "COMMODITY_FUTURES_FUND" if ticker == "USO" else "NON_UCITS_ETF", "kid_status": "NOT_APPLICABLE_FIO_MANUAL",
        "point_in_time_metadata_status": "REVIEWED_POINT_IN_TIME", "metadata_as_of_date": "2026-07-15", "provenance": {"source": "test"},
    }
    return build_instrument_identity_execution_routing({"version": "instrument_identity_execution_routing_request_v1", "instrument": instrument, "execution_route": {"route": "FIO_MANUAL_LONG_TERM", "automation_allowed": False, "manual_only": True, "risk_inclusion_required": True, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False, "expected_holding_horizon": "THREE_YEARS_OR_LONGER", "eligibility_evidence": "user_supplied_manual_holding", "eligibility_as_of_date": "2026-07-15"}, "provenance": {"source": "test"}})


def _source(positions=None):
    return {"version": "fio_manual_long_term_inventory_source_v1", "inventory_id": "fio-inventory-20260715", "account_id_redacted": "fio-***123", "as_of_timestamp": "2026-07-15T12:00:00Z", "base_currency": "USD", "provenance": {"source": "manual-test"}, "positions": positions if positions is not None else [_position()]}


def _position(ticker="USO", **overrides):
    item = {"position_id": f"pos-{ticker.lower()}", "identity_routing_result": _identity(ticker), "quantity": "10", "currency": "USD", "average_cost": "20.00", "reference_price": "21.50", "reference_price_timestamp": "2026-07-15T12:00:00Z", "market_value": "215.00", "acquisition_or_earliest_lot_date": "2024-01-02", "expected_holding_horizon": "THREE_YEARS_OR_LONGER", "provenance": {"source": "manual-test"}}
    item.update(overrides)
    return item


def _request(tmp_path, source, **overrides):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    value = {"version": "fio_manual_long_term_inventory_request_v1", "inventory_id": source["inventory_id"], "account_id_redacted": source["account_id_redacted"], "as_of_timestamp": source["as_of_timestamp"], "base_currency": "USD", "source_file_path": str(path), "expected_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "maximum_bytes": 100000, "maximum_positions": 10, "provenance": {"source": "test"}}
    value.update(overrides)
    return value


def test_valid_inventory_is_deterministic_manual_and_preserves_uso_and_smh_classification(tmp_path):
    request = _request(tmp_path, _source([_position("USO"), _position("SMH", market_value=None, reference_price=None, reference_price_timestamp=None)]))
    first = build_fio_manual_long_term_inventory(request)
    second = build_fio_manual_long_term_inventory(request)
    assert first["validation_status"] == "REVIEW_REQUIRED"
    assert first["valued_position_count"] == 1 and first["unvalued_position_count"] == 1
    assert first["totals"]["market_value"] == "215.00"
    positions = {item["position_id"]: item for item in first["validated_positions"]}
    assert positions["pos-uso"]["identity_routing_result"]["instrument"]["security_type"] == "COMMODITY_FUTURES_FUND"
    assert positions["pos-smh"]["identity_routing_result"]["instrument"]["instrument_type"] == "NON_UCITS_ETF"
    assert positions["pos-uso"]["execution_route"] == "FIO_MANUAL_LONG_TERM"
    assert positions["pos-uso"]["manual_action_only"] is True
    assert positions["pos-uso"]["automatic_order_generation_allowed"] is False
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    assert first["safety_flags"] == {"filesystem_writes_performed": False, "network_used": False, "provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "automatic_orders_generated": False, "automatic_liquidation_allowed": False, "production_runtime_supported": False}


@pytest.mark.parametrize("mutation, match", [
    (lambda source, request: source["positions"][0].update({"quantity": "0"}), "quantity must be positive"),
    (lambda source, request: source["positions"][0].update({"quantity": "-1"}), "quantity must be positive"),
    (lambda source, request: source["positions"][0].update({"currency": "EUR"}), "currency"),
    (lambda source, request: source["positions"][0].update({"automatic_order_generation_allowed": True}), "unknown"),
    (lambda source, request: source["positions"].append(copy.deepcopy(source["positions"][0])), "duplicate"),
    (lambda source, request: source["positions"][0]["identity_routing_result"]["execution_route"].update({"route": "IBKR_REVIEW_REQUIRED"}), "child"),
])
def test_invalid_position_contracts_fail_closed(tmp_path, mutation, match):
    source = _source()
    request = _request(tmp_path, source)
    mutation(source, request)
    request = _request(tmp_path, source)
    with pytest.raises(ValueError, match=match):
        build_fio_manual_long_term_inventory(request)


def test_hash_path_unknown_fields_and_deep_immutability_fail_closed(tmp_path):
    source = _source()
    request = _request(tmp_path, source)
    before = copy.deepcopy(request)
    request["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        build_fio_manual_long_term_inventory(request)
    request = _request(tmp_path, source, expected_source_sha256="0" * 64)
    with pytest.raises(ValueError, match="hash"):
        build_fio_manual_long_term_inventory(request)
    request = _request(tmp_path, source, source_file_path=str(tmp_path / ".." / "inventory.json"))
    with pytest.raises(ValueError, match="traversal"):
        build_fio_manual_long_term_inventory(request)
    assert before["inventory_id"] == source["inventory_id"]


def test_rehashed_but_fabricated_child_result_fails_closed(tmp_path):
    source = _source()
    child = source["positions"][0]["identity_routing_result"]
    child["instrument"]["security_type"] = "SPOT_OIL"
    child_without_hash = copy.deepcopy(child)
    child_without_hash.pop("output_payload_sha256")
    child["output_payload_sha256"] = _sha(child_without_hash)
    request = _request(tmp_path, source)
    with pytest.raises(ValueError, match="child"):
        build_fio_manual_long_term_inventory(request)
