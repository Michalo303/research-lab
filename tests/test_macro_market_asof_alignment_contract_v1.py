from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

from research_lab.execution.macro_market_asof_alignment_contract_v1 import (
    build_macro_market_asof_alignment_contract,
)


MODULE_PATH = Path("research_lab/execution/macro_market_asof_alignment_contract_v1.py")


def _bars() -> list[dict[str, object]]:
    return [
        {"timestamp": "2024-03-07T21:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0},
        {"timestamp": "2024-03-08T21:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1100.0},
        {"timestamp": "2024-03-11T20:00:00Z", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 1200.0},
    ]


def _macro_series_result(
    *,
    provider: str = "FRED",
    series_id: str = "UNRATE",
    classification: str = "RELEASE_AWARE",
    observations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if observations is None:
        observations = [
            {
                "observation_date": "2024-02-01",
                "value": 3.9,
                "point_in_time": {
                    "classification": "exact_release_timestamp",
                    "available_date": "2024-03-08",
                    "available_timestamp_utc": "2024-03-08T13:30:00Z",
                },
            }
        ]
    contract = {
        "version": "macro_series_contract_result_v1",
        "contract_version": "macro_series_contract_v1",
        "provider": provider,
        "series_id": series_id,
        "frequency": "monthly",
        "units": "percent",
        "observations": observations,
        "observation_count": len(observations),
        "first_observation_date": observations[0]["observation_date"],
        "last_observation_date": observations[-1]["observation_date"],
        "point_in_time_summary": {
            "classification_counts": {observations[0]["point_in_time"]["classification"]: len(observations)},
            "has_revisions": len({item["observation_date"] for item in observations}) != len(observations),
            "latest_available_date": max(str(item["point_in_time"]["available_date"]) for item in observations),
        },
        "safe_flags": {
            "provider_calls_used": 0,
            "network_used": False,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "hermes_state_touched": False,
            "production_runtime_supported": False,
        },
        "provenance": {"source": "unit_test"},
        "input_sha256": "1" * 64,
        "output_payload_sha256": "2" * 64,
    }
    return {
        "version": "fred_alfred_readonly_adapter_result_v1",
        "adapter_version": "fred_alfred_readonly_adapter_v1",
        "status": "SUCCESS",
        "provider": provider,
        "series_id": series_id,
        "response_sha256": "3" * 64,
        "macro_series_contract": contract,
        "network_used": True,
        "provider_calls_used": 1,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "provenance": {"source": "unit_test"},
        "input_sha256": "4" * 64,
        "output_payload_sha256": "5" * 64,
        "point_in_time_classification": classification,
    }


def _request() -> dict[str, object]:
    return {
        "version": "macro_market_asof_alignment_contract_request_v1",
        "market_bars": _bars(),
        "macro_series_results": [_macro_series_result()],
        "market_timezone": "America/New_York",
        "decision_timestamp_convention": "LOCAL_TIME_ON_BAR_DATE",
        "decision_time_local": "09:30:00",
        "macro_availability_convention": "AT_START_OF_DAY",
        "minimum_release_lag_minutes": 0,
        "maximum_staleness_days": 40,
        "missing_data_policy": "MARK_MISSING",
        "unsafe_series_policy": "REJECT",
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_macro_market_asof_alignment_contract(copy.deepcopy(request))


def test_release_boundary_and_before_open_visibility_are_deterministic():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["status"] == "SUCCESS"
    assert first["aligned_bars"][0]["macro_values"]["FRED:UNRATE"] is None
    assert first["aligned_bars"][1]["macro_values"]["FRED:UNRATE"] == 3.9
    assert first["aligned_bars"][2]["macro_values"]["FRED:UNRATE"] == 3.9
    assert first["aligned_bars"][1]["point_in_time_classifications"]["FRED:UNRATE"] == "RELEASE_AWARE"


def test_publication_after_close_waits_until_next_trading_bar():
    request = _request()
    request["macro_series_results"] = [
        _macro_series_result(
            observations=[
                {
                    "observation_date": "2024-02-01",
                    "value": 3.9,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2024-03-08",
                        "available_timestamp_utc": "2024-03-08T22:30:00Z",
                    },
                }
            ]
        )
    ]

    result = _run(request)

    assert result["aligned_bars"][1]["macro_values"]["FRED:UNRATE"] is None
    assert result["aligned_bars"][2]["macro_values"]["FRED:UNRATE"] == 3.9


def test_vintage_revisions_do_not_leak_future_values():
    request = _request()
    request["maximum_staleness_days"] = 120
    request["macro_series_results"] = [
        _macro_series_result(
            provider="ALFRED",
            series_id="GDP",
            classification="VINTAGE_AWARE",
            observations=[
                {
                    "observation_date": "2023-12-01",
                    "value": 2.0,
                    "point_in_time": {
                        "classification": "vintage_date_only",
                        "available_date": "2024-03-01",
                        "available_timestamp_utc": None,
                    },
                },
                {
                    "observation_date": "2023-12-01",
                    "value": 2.5,
                    "point_in_time": {
                        "classification": "vintage_date_only",
                        "available_date": "2024-03-10",
                        "available_timestamp_utc": None,
                    },
                },
            ],
        )
    ]

    result = _run(request)

    assert result["aligned_bars"][0]["macro_values"]["ALFRED:GDP"] == 2.0
    assert result["aligned_bars"][1]["macro_values"]["ALFRED:GDP"] == 2.0
    assert result["aligned_bars"][2]["macro_values"]["ALFRED:GDP"] == 2.5


def test_stale_missing_and_unsafe_series_behave_fail_closed():
    request = _request()
    request["maximum_staleness_days"] = 1
    result = _run(request)
    assert result["aligned_bars"][2]["missing_indicators"]["FRED:UNRATE"] is True
    assert result["aligned_bars"][2]["macro_values"]["FRED:UNRATE"] is None

    unsafe_request = _request()
    unsafe_request["macro_series_results"] = [_macro_series_result(classification="CURRENT_VALUE_ONLY")]
    with pytest.raises(ValueError, match="unsafe historical macro series"):
        _run(unsafe_request)

    allowed_unsafe = _request()
    allowed_unsafe["unsafe_series_policy"] = "ALLOW_RESEARCH_ONLY"
    allowed_unsafe["macro_series_results"] = [_macro_series_result(classification="CURRENT_VALUE_ONLY")]
    allowed_result = _run(allowed_unsafe)
    assert allowed_result["unsafe_series_warnings"] == ["FRED:UNRATE"]


def test_duplicate_or_unordered_bars_duplicate_macro_observations_and_no_sorting_fail():
    duplicate_bars = _request()
    duplicate_bars["market_bars"][1]["timestamp"] = duplicate_bars["market_bars"][0]["timestamp"]
    with pytest.raises(ValueError, match="strictly ordered"):
        _run(duplicate_bars)

    reversed_bars = _request()
    reversed_bars["market_bars"] = list(reversed(reversed_bars["market_bars"]))
    with pytest.raises(ValueError, match="strictly ordered"):
        _run(reversed_bars)

    duplicate_macro = _request()
    duplicate_macro["macro_series_results"][0]["macro_series_contract"]["observations"].append(
        copy.deepcopy(duplicate_macro["macro_series_results"][0]["macro_series_contract"]["observations"][0])
    )
    duplicate_macro["macro_series_results"][0]["macro_series_contract"]["observation_count"] = 2
    with pytest.raises(ValueError, match="duplicate macro observation"):
        _run(duplicate_macro)


def test_module_does_not_import_network_modules():
    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
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
