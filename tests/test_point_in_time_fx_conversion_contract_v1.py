from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "point_in_time_fx_conversion_contract_v1.py"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _run(request: dict[str, object]) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("point_in_time_fx_conversion_contract_v1", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_point_in_time_fx_conversion_contract(copy.deepcopy(request))


def _instrument(currency: str = "EUR", value: object = "100.00", **overrides: object) -> dict[str, object]:
    return {"instrument_id": "asset-eur", "currency": currency, "decision_timestamp": "2024-01-02T12:00:00Z", "value": value,
            "source_identity": "synthetic-instrument-v1", "source_sha256": SHA_A, "provenance": {"source": "test"}, **overrides}


def _observation(observation_id: str = "eur-usd-1", base: str = "EUR", quote: str = "USD", rate: object = "1.250000", **overrides: object) -> dict[str, object]:
    return {"observation_id": observation_id, "pair_id": f"{base}-{quote}-synthetic", "base_currency": base, "quote_currency": quote,
            "observation_timestamp": "2024-01-02T11:59:00Z", "availability_timestamp": "2024-01-02T12:00:00Z", "rate": rate,
            "source_identity": "synthetic-fx-v1", "source_sha256": SHA_B, "point_in_time_status": "POINT_IN_TIME_VERIFIED", "provenance": {"source": "test"}, **overrides}


def _request(*, instruments: list[dict[str, object]] | None = None, observations: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    instruments = instruments or [_instrument()]
    observations = observations if observations is not None else [_observation()]
    return {"version": "point_in_time_fx_conversion_contract_request_v1", "conversion_id": "fx-review-001", "base_currency": "USD",
            "instrument_values": instruments, "fx_observations": observations,
            "decision_timestamps": {item["instrument_id"]: item["decision_timestamp"] for item in instruments}, "maximum_staleness_seconds": 60,
            "direct_rate_policy": "REQUIRE_EXPLICIT_DIRECT_PAIR", "inverse_rate_policy": "REJECT_INVERSE", "cross_rate_policy": "REJECT_CROSS_RATE",
            "declared_cross_paths": [], "expected_source_hashes": {"instrument_values": {item["instrument_id"]: item["source_sha256"] for item in instruments}, "fx_observations": {item["observation_id"]: item["source_sha256"] for item in observations}},
            "precision_policy": {"decimal_places": 6, "rounding_mode": "ROUND_HALF_EVEN"}, "provenance": {"source": "test"}, **overrides}


def test_same_currency_noop_is_exact_one_and_preserves_signed_value():
    result = _run(_request(instruments=[_instrument(currency="USD", value="-12.500000")], observations=[]))
    converted = result["converted_values"][0]
    assert result["conversion_status"] == "SUCCESS"
    assert converted["path_type"] == "SAME_CURRENCY"
    assert converted["effective_rate"] == "1.000000"
    assert converted["converted_value"] == "-12.500000"
    assert converted["selected_observation_ids"] == []


def test_direct_inverse_and_declared_cross_arithmetic_and_lineage():
    direct = _run(_request())
    assert direct["converted_values"][0]["converted_value"] == "125.000000"
    assert direct["converted_values"][0]["arithmetic_formula"] == "source_value * direct_rate"
    inverse = _run(_request(observations=[_observation(base="USD", quote="EUR", rate="0.800000")], inverse_rate_policy="ALLOW_EXPLICIT_INVERSE"))
    assert inverse["converted_values"][0]["path_type"] == "INVERSE"
    assert inverse["converted_values"][0]["converted_value"] == "125.000000"
    cross_observations = [_observation("eur-gbp", "EUR", "GBP", "0.800000"), _observation("gbp-usd", "GBP", "USD", "1.500000", source_sha256=SHA_C)]
    cross = _run(_request(observations=cross_observations, cross_rate_policy="ALLOW_DECLARED_CROSS_PATHS", declared_cross_paths=[{"path_id": "eur-gbp-usd", "source_currency": "EUR", "intermediary_currency": "GBP", "target_currency": "USD", "first_pair_id": "EUR-GBP-synthetic", "first_arithmetic_orientation": "MULTIPLY", "second_pair_id": "GBP-USD-synthetic", "second_arithmetic_orientation": "MULTIPLY", "maximum_combined_staleness_seconds": 120, "provenance": {"source": "test"}}]))
    assert cross["converted_values"][0]["path_type"] == "CROSS"
    assert cross["converted_values"][0]["converted_value"] == "120.000000"
    assert cross["converted_values"][0]["selected_observation_ids"] == ["eur-gbp", "gbp-usd"]


@pytest.mark.parametrize("overrides,match", [
    ({"inverse_rate_policy": "REJECT_INVERSE", "observations": [_observation(base="USD", quote="EUR")]}, "missing required conversion"),
    ({"cross_rate_policy": "REJECT_CROSS_RATE", "observations": [_observation("eur-gbp", "EUR", "GBP")]}, "missing required conversion"),
    ({"cross_rate_policy": "ALLOW_DECLARED_CROSS_PATHS", "observations": [_observation("eur-gbp", "EUR", "GBP")], "declared_cross_paths": []}, "undeclared cross"),
])
def test_disabled_or_undeclared_routes_fail_closed(overrides, match):
    request = _request(**overrides)
    with pytest.raises(ValueError, match=match): _run(request)


@pytest.mark.parametrize("mutator,match", [
    (lambda r: r.update(fx_observations=[_observation(observation_timestamp="2024-01-02T12:01:00Z", availability_timestamp="2024-01-02T12:01:00Z")]), "newer than decision"),
    (lambda r: r.update(fx_observations=[_observation(availability_timestamp="2024-01-02T12:00:01Z")]), "missing required conversion"),
    (lambda r: r.update(fx_observations=[_observation(observation_timestamp="2024-01-02T11:58:59Z")]), "stale"),
    (lambda r: r.update(fx_observations=[_observation(rate="0")]), "positive finite"),
    (lambda r: r.update(fx_observations=[_observation(rate="-1")]), "positive finite"),
    (lambda r: r.update(fx_observations=[_observation(rate=float("nan"))]), "positive finite"),
    (lambda r: r.update(instrument_values=[_instrument(value=float("inf"))]), "finite"),
    (lambda r: r.update(fx_observations=[_observation(observation_id="x"), _observation(observation_id="x")]), "duplicate observation_id"),
    (lambda r: r.update(fx_observations=[_observation(), _observation("other")]), "duplicate semantic observation"),
    (lambda r: r.update(fx_observations=[_observation("late", observation_timestamp="2024-01-02T11:59:30Z"), _observation("early", observation_timestamp="2024-01-02T11:59:00Z")]), "deterministic chronological"),
])
def test_invalid_or_non_point_in_time_observations_fail_closed(mutator, match):
    request = _request(); mutator(request)
    with pytest.raises(ValueError, match=match): _run(request)


def test_boundary_latest_selection_tie_and_precision_are_deterministic():
    boundary = _run(_request(observations=[_observation(observation_timestamp="2024-01-02T11:59:00Z")]))
    assert boundary["rate_ages_seconds"] == {"asset-eur": 60}
    latest = _run(_request(observations=[_observation("old", observation_timestamp="2024-01-02T11:59:00Z"), _observation("new", observation_timestamp="2024-01-02T11:59:30Z")]))
    assert latest["converted_values"][0]["selected_observation_ids"] == ["new"]
    rounded = _run(_request(instruments=[_instrument(value="1")], observations=[_observation(rate="1.23456789")]))
    assert rounded["converted_values"][0]["converted_value"] == "1.234568"
    with pytest.raises(ValueError, match="tied non-identical"):
        _run(_request(observations=[_observation("a"), _observation("b", source_sha256=SHA_C)]))


@pytest.mark.parametrize("mutator,match", [
    (lambda r: r.update(base_currency="usd"), "uppercase"),
    (lambda r: r.update(instrument_values=[_instrument(currency="eur")]), "uppercase"),
    (lambda r: r.update(fx_observations=[_observation(pair_id="")]), "pair_id"),
    (lambda r: r.update(expected_source_hashes={"instrument_values": {}, "fx_observations": {}}), "expected source hash"),
    (lambda r: r.update(instrument_values=[_instrument(source_sha256="bad")]), "SHA-256"),
    (lambda r: r.update(unexpected=True), "unknown field"),
])
def test_identity_hash_and_schema_validation(mutator, match):
    request = _request(); mutator(request)
    with pytest.raises(ValueError, match=match): _run(request)


def test_hashes_repeated_output_immutability_and_safety_flags():
    request = _request(); before = copy.deepcopy(request)
    first, second = _run(request), _run(request)
    assert request == before and first == second
    assert len(first["input_sha256"]) == len(first["output_payload_sha256"]) == 64
    assert first["provider_calls_used"] == first["broker_actions_used"] == 0
    assert first["network_used"] is False and first["filesystem_reads_performed"] is False
    assert first["filesystem_writes_performed"] is False and first["production_runtime_supported"] is False
    payload = dict(first); output_hash = payload.pop("output_payload_sha256")
    assert output_hash == hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _cross_request(*, observations: list[dict[str, object]] | None = None, paths: list[dict[str, object]] | None = None) -> dict[str, object]:
    observations = observations or [_observation("eur-gbp", "EUR", "GBP", "0.8"), _observation("gbp-usd", "GBP", "USD", "1.5", source_sha256=SHA_C)]
    path = {"path_id": "eur-gbp-usd", "source_currency": "EUR", "intermediary_currency": "GBP", "target_currency": "USD", "first_pair_id": "EUR-GBP-synthetic", "first_arithmetic_orientation": "MULTIPLY", "second_pair_id": "GBP-USD-synthetic", "second_arithmetic_orientation": "MULTIPLY", "maximum_combined_staleness_seconds": 120, "provenance": {"source": "test"}}
    return _request(observations=observations, cross_rate_policy="ALLOW_DECLARED_CROSS_PATHS", declared_cross_paths=paths if paths is not None else [path])


@pytest.mark.parametrize("case", [
    "same_currency_no_observation", "same_currency_rate_one", "direct_path", "inverse_disabled", "cross_disabled",
    "ambiguous_paths", "cyclic_path", "missing_first_leg", "missing_second_leg", "availability_after_boundary",
    "fresh_rate", "staleness_plus_one", "missing_pair", "wrong_direct_orientation", "wrong_inverse_orientation",
    "base_currency_mismatch", "positive_infinity_rate", "negative_infinity_rate", "nan_value", "hash_fx_mismatch",
    "missing_expected_fx_hash", "unknown_instrument_field", "unknown_observation_field", "duplicate_instrument_id",
    "unknown_cross_path_field", "cross_path_currency_mismatch", "selected_lineage", "cross_lineage",
    "paper_trading_false", "registry_write_false", "deployment_false", "no_clock_or_io_flags", "explicit_pair_id_is_not_orientation",
], ids=lambda case: case)
def test_explicit_contract_matrix(case: str):
    if case == "same_currency_no_observation":
        assert _run(_request(instruments=[_instrument(currency="USD")], observations=[]))["converted_values"][0]["selected_observation_ids"] == []
    elif case == "same_currency_rate_one":
        assert _run(_request(instruments=[_instrument(currency="USD")], observations=[]))["converted_values"][0]["effective_rate"] == "1.000000"
    elif case == "direct_path":
        assert _run(_request())["converted_values"][0]["path_type"] == "DIRECT"
    elif case == "inverse_disabled":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[_observation(base="USD", quote="EUR")]))
    elif case == "cross_disabled":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[_observation("eur-gbp", "EUR", "GBP")]))
    elif case == "ambiguous_paths":
        request = _cross_request(); request["declared_cross_paths"].append({**request["declared_cross_paths"][0], "path_id": "other"})
        with pytest.raises(ValueError, match="ambiguous cross paths"): _run(request)
    elif case == "cyclic_path":
        request = _cross_request(); request["declared_cross_paths"][0]["intermediary_currency"] = "EUR"
        with pytest.raises(ValueError, match="cyclic cross path"): _run(request)
    elif case == "missing_first_leg":
        with pytest.raises(ValueError, match="missing cross leg"): _run(_cross_request(observations=[_observation("gbp-usd", "GBP", "USD", "1.5")]))
    elif case == "missing_second_leg":
        with pytest.raises(ValueError, match="missing cross leg"): _run(_cross_request(observations=[_observation("eur-gbp", "EUR", "GBP", "0.8")]))
    elif case == "availability_after_boundary":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[_observation(availability_timestamp="2024-01-02T12:00:01Z")]))
    elif case == "fresh_rate":
        assert _run(_request(observations=[_observation(observation_timestamp="2024-01-02T11:59:01Z")]))["rate_ages_seconds"] == {"asset-eur": 59}
    elif case == "staleness_plus_one":
        with pytest.raises(ValueError, match="stale rate"): _run(_request(observations=[_observation(observation_timestamp="2024-01-02T11:58:59Z")]))
    elif case == "missing_pair":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[]))
    elif case == "wrong_direct_orientation":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[_observation(base="USD", quote="EUR")]))
    elif case == "wrong_inverse_orientation":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(observations=[_observation(base="GBP", quote="EUR")], inverse_rate_policy="ALLOW_EXPLICIT_INVERSE"))
    elif case == "base_currency_mismatch":
        with pytest.raises(ValueError, match="missing required conversion"): _run(_request(base_currency="GBP"))
    elif case == "positive_infinity_rate":
        with pytest.raises(ValueError, match="positive finite"): _run(_request(observations=[_observation(rate=float("inf"))]))
    elif case == "negative_infinity_rate":
        with pytest.raises(ValueError, match="positive finite"): _run(_request(observations=[_observation(rate=float("-inf"))]))
    elif case == "nan_value":
        with pytest.raises(ValueError, match="finite"): _run(_request(instruments=[_instrument(value=float("nan"))]))
    elif case == "hash_fx_mismatch":
        request = _request(); request["expected_source_hashes"]["fx_observations"]["eur-usd-1"] = SHA_C
        with pytest.raises(ValueError, match="expected source hash"): _run(request)
    elif case == "missing_expected_fx_hash":
        request = _request(); request["expected_source_hashes"]["fx_observations"] = {}
        with pytest.raises(ValueError, match="expected source hash"): _run(request)
    elif case == "unknown_instrument_field":
        request = _request(); request["instrument_values"][0]["unexpected"] = True
        with pytest.raises(ValueError, match="unknown field"): _run(request)
    elif case == "unknown_observation_field":
        request = _request(); request["fx_observations"][0]["unexpected"] = True
        with pytest.raises(ValueError, match="unknown field"): _run(request)
    elif case == "duplicate_instrument_id":
        items = [_instrument(), _instrument(source_sha256=SHA_C)]; request = _request(instruments=items)
        with pytest.raises(ValueError, match="duplicate instrument_id"): _run(request)
    elif case == "unknown_cross_path_field":
        request = _cross_request(); request["declared_cross_paths"][0]["unexpected"] = True
        with pytest.raises(ValueError, match="unknown field"): _run(request)
    elif case == "cross_path_currency_mismatch":
        request = _cross_request(observations=[_observation("first", "EUR", "CHF", pair_id="EUR-GBP-synthetic"), _observation("gbp-usd", "GBP", "USD", "1.5")])
        with pytest.raises(ValueError, match="currency mismatch"): _run(request)
    elif case == "selected_lineage":
        selected = _run(_request())["selected_observations"][0]
        assert set(selected) == {"observation_id", "pair_id", "source_sha256", "age_seconds"}
    elif case == "cross_lineage":
        value = _run(_cross_request())["converted_values"][0]
        assert value["selected_observation_ids"] == ["eur-gbp", "gbp-usd"] and value["path_id"] == "eur-gbp-usd"
    elif case == "paper_trading_false":
        assert _run(_request())["paper_trading_performed"] is False
    elif case == "registry_write_false":
        assert _run(_request())["registry_write_performed"] is False
    elif case == "deployment_false":
        assert _run(_request())["deployment_performed"] is False
    elif case == "no_clock_or_io_flags":
        result = _run(_request())
        assert result["network_used"] is False and result["filesystem_reads_performed"] is False and result["filesystem_writes_performed"] is False
    elif case == "explicit_pair_id_is_not_orientation":
        result = _run(_request(observations=[_observation(pair_id="provider-row-8731")]))
        assert result["converted_values"][0]["path_type"] == "DIRECT"
