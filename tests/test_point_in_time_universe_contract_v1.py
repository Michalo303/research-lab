from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path("research_lab/execution/point_in_time_universe_contract_v1.py")


def _instrument(
    instrument_id: str,
    *,
    provider_symbol: str,
    membership_status: str = "POINT_IN_TIME_VERIFIED",
    active_to: str | None = None,
    membership_to: str | None = None,
    currency: str = "USD",
    calendar_id: str = "US_EQUITY_DAY",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "instrument_id": instrument_id,
        "provider": "EODHD",
        "provider_symbol": provider_symbol,
        "display_symbol": provider_symbol,
        "instrument_type": "ETF",
        "currency": currency,
        "market_venue_group": "US_EQUITY",
        "calendar_id": calendar_id,
        "active_from": "2015-01-01T00:00:00Z",
        "membership_from": "2015-01-01T00:00:00Z",
        "point_in_time_membership_status": membership_status,
        "lot_size": 1,
        "price_precision": 4,
        "corporate_action_policy_id": "raw_prices_only_v1",
        "source_sha256": "a" * 64 if instrument_id.endswith("a") else "b" * 64,
        "provenance": {"source": "unit_test", "instrument_id": instrument_id},
    }
    if active_to is not None:
        payload["active_to"] = active_to
    if membership_to is not None:
        payload["membership_to"] = membership_to
    return payload


def _request(*, as_of_timestamp: str = "2024-06-15T00:00:00Z") -> dict[str, object]:
    return {
        "version": "point_in_time_universe_contract_request_v1",
        "universe_id": "liquid_us_listed_etf_research_universe_v1",
        "universe_version": "v1",
        "as_of_timestamp": as_of_timestamp,
        "membership_policy": {
            "allow_unsafe_current_membership": False,
            "allow_not_point_in_time_safe": False,
            "unsafe_policy_label": "FAIL_CLOSED",
        },
        "base_currency": "USD",
        "instruments": [
            _instrument("instrument-b", provider_symbol="QQQ.US"),
            _instrument("instrument-a", provider_symbol="SPY.US"),
        ],
        "provenance": {"request_source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("point_in_time_universe_contract_v1", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_point_in_time_universe_contract(copy.deepcopy(request))


def test_valid_static_research_universe_is_deterministic_and_sorted():
    request = _request()
    request["instruments"] = [
        _instrument(
            "instrument-b",
            provider_symbol="QQQ.US",
            membership_status="EXPLICIT_STATIC_RESEARCH_UNIVERSE",
        ),
        _instrument(
            "instrument-a",
            provider_symbol="SPY.US",
            membership_status="EXPLICIT_STATIC_RESEARCH_UNIVERSE",
        ),
    ]

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["version"] == "point_in_time_universe_contract_result_v1"
    assert first["contract_version"] == "point_in_time_universe_contract_v1"
    assert first["production_runtime_supported"] is False
    assert first["validated_universe"]["base_currency"] == "USD"
    assert [item["instrument_id"] for item in first["included_instruments"]] == [
        "instrument-a",
        "instrument-b",
    ]
    assert first["excluded_instruments"] == []
    assert first["survivorship_warnings"] == [
        "Universe membership is an explicit static research universe and is not survivorship-bias-free."
    ]
    assert first["point_in_time_classifications"] == {
        "EXPLICIT_STATIC_RESEARCH_UNIVERSE": ["instrument-a", "instrument-b"]
    }
    assert first["safety_flags"] == {
        "network_used": False,
        "filesystem_writes_performed": False,
        "unsafe_membership_policy_used": False,
        "production_runtime_supported": False,
    }


def test_valid_point_in_time_membership_excludes_removed_instruments():
    request = _request(as_of_timestamp="2024-04-15T00:00:00Z")
    request["instruments"] = [
        _instrument("instrument-a", provider_symbol="SPY.US"),
        _instrument(
            "instrument-b",
            provider_symbol="QQQ.US",
            membership_to="2024-03-31T00:00:00Z",
            active_to="2024-03-31T00:00:00Z",
        ),
    ]

    result = _run(request)

    assert [item["instrument_id"] for item in result["included_instruments"]] == ["instrument-a"]
    assert result["excluded_instruments"] == [
        {
            "instrument_id": "instrument-b",
            "reason": "instrument membership ended before as_of_timestamp",
        }
    ]
    assert result["membership_intervals"] == [
        {
            "instrument_id": "instrument-a",
            "active_from": "2015-01-01T00:00:00Z",
            "active_to": None,
            "membership_from": "2015-01-01T00:00:00Z",
            "membership_to": None,
            "point_in_time_membership_status": "POINT_IN_TIME_VERIFIED",
        }
    ]


def test_removed_but_still_active_instrument_is_excluded_without_invented_active_to():
    request = _request(as_of_timestamp="2024-04-15T00:00:00Z")
    request["instruments"] = [
        _instrument("instrument-a", provider_symbol="SPY.US"),
        _instrument(
            "instrument-b",
            provider_symbol="QQQ.US",
            membership_to="2024-03-31T00:00:00Z",
        ),
    ]

    result = _run(request)

    assert [item["instrument_id"] for item in result["included_instruments"]] == ["instrument-a"]
    assert result["excluded_instruments"] == [
        {
            "instrument_id": "instrument-b",
            "reason": "instrument membership ended before as_of_timestamp",
        }
    ]


def test_membership_before_entry_fails_and_inactive_instrument_is_excluded():
    before_entry = _request(as_of_timestamp="2014-12-31T00:00:00Z")
    with pytest.raises(ValueError, match="membership cannot start after as_of_timestamp"):
        _run(before_entry)

    inactive = _request()
    inactive["instruments"] = [
        _instrument(
            "instrument-a",
            provider_symbol="SPY.US",
            active_to="2024-03-01T00:00:00Z",
        )
    ]
    result = _run(inactive)

    assert result["included_instruments"] == []
    assert result["excluded_instruments"] == [
        {
            "instrument_id": "instrument-a",
            "reason": "instrument is inactive at as_of_timestamp",
        }
    ]


def test_duplicate_ids_duplicate_provider_symbols_and_invalid_metadata_fail():
    duplicate_ids = _request()
    duplicate_ids["instruments"] = [
        _instrument("instrument-a", provider_symbol="SPY.US"),
        _instrument("instrument-a", provider_symbol="QQQ.US"),
    ]
    with pytest.raises(ValueError, match="duplicate instrument_id"):
        _run(duplicate_ids)

    duplicate_provider_symbols = _request()
    duplicate_provider_symbols["instruments"] = [
        _instrument("instrument-a", provider_symbol="SPY.US"),
        _instrument("instrument-b", provider_symbol="SPY.US"),
    ]
    with pytest.raises(ValueError, match="duplicate provider identity"):
        _run(duplicate_provider_symbols)

    invalid_currency = _request()
    invalid_currency["instruments"] = [_instrument("instrument-a", provider_symbol="SPY.US", currency="usd")]
    with pytest.raises(ValueError, match="currency must be uppercase ISO-like text"):
        _run(invalid_currency)

    invalid_calendar = _request()
    invalid_calendar["instruments"] = [_instrument("instrument-a", provider_symbol="SPY.US", calendar_id="")]
    with pytest.raises(ValueError, match="calendar_id must be non-empty text"):
        _run(invalid_calendar)


def test_current_membership_only_is_rejected_unless_explicit_unsafe_mode_is_supplied():
    request = _request()
    request["instruments"] = [
        _instrument(
            "instrument-a",
            provider_symbol="SPY.US",
            membership_status="CURRENT_MEMBERSHIP_ONLY",
        )
    ]
    with pytest.raises(ValueError, match="CURRENT_MEMBERSHIP_ONLY is not point-in-time safe"):
        _run(request)

    unsafe = _request()
    unsafe["membership_policy"] = {
        "allow_unsafe_current_membership": True,
        "allow_not_point_in_time_safe": False,
        "unsafe_policy_label": "UNSAFE_RESEARCH_ONLY_ALLOW_CURRENT_MEMBERSHIP",
    }
    unsafe["instruments"] = [
        _instrument(
            "instrument-a",
            provider_symbol="SPY.US",
            membership_status="CURRENT_MEMBERSHIP_ONLY",
        )
    ]

    result = _run(unsafe)

    assert result["safety_flags"]["unsafe_membership_policy_used"] is True
    assert result["survivorship_warnings"] == [
        "Unsafe research-only membership policy includes current-membership-only instruments; survivorship bias may be present."
    ]


def test_hashes_are_deterministic_and_input_is_not_mutated():
    request = _request()
    original = copy.deepcopy(request)

    first = _run(request)
    second = _run(request)

    assert request == original
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_module_does_not_import_network_clients():
    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "aiohttp",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
