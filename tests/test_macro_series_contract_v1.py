from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from research_lab.execution.macro_series_contract_v1 import (
    build_macro_series_contract,
)


MODULE_PATH = Path("research_lab/execution/macro_series_contract_v1.py")


def _request() -> dict[str, object]:
    return {
        "version": "macro_series_contract_request_v1",
        "provider": "FRED",
        "series_id": "UNRATE",
        "frequency": "monthly",
        "units": "percent",
        "observations": [
            {
                "observation_date": "2024-01-01",
                "value": 3.7,
                "point_in_time": {
                    "classification": "exact_release_timestamp",
                    "available_date": "2024-02-02",
                    "available_timestamp_utc": "2024-02-02T13:30:00Z",
                },
            },
            {
                "observation_date": "2024-02-01",
                "value": 3.9,
                "point_in_time": {
                    "classification": "exact_release_timestamp",
                    "available_date": "2024-03-08",
                    "available_timestamp_utc": "2024-03-08T13:30:00Z",
                },
            },
        ],
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_macro_series_contract(copy.deepcopy(request))


def test_exact_release_timestamp_series_is_deterministic_and_fail_closed():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["version"] == "macro_series_contract_result_v1"
    assert first["contract_version"] == "macro_series_contract_v1"
    assert first["provider"] == "FRED"
    assert first["series_id"] == "UNRATE"
    assert first["observation_count"] == 2
    assert first["point_in_time_summary"] == {
        "classification_counts": {"exact_release_timestamp": 2},
        "has_revisions": False,
        "latest_available_date": "2024-03-08",
    }
    assert first["safe_flags"] == {
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "hermes_state_touched": False,
        "production_runtime_supported": False,
    }


def test_vintage_date_only_revisions_are_supported_without_fabricated_timestamp():
    request = _request()
    request["provider"] = "ALFRED"
    request["observations"] = [
        {
            "observation_date": "2024-01-01",
            "value": 3.7,
            "point_in_time": {
                "classification": "vintage_date_only",
                "available_date": "2024-02-01",
            },
        },
        {
            "observation_date": "2024-01-01",
            "value": 3.8,
            "point_in_time": {
                "classification": "vintage_date_only",
                "available_date": "2024-03-01",
            },
        },
    ]

    result = _run(request)

    assert result["provider"] == "ALFRED"
    assert result["point_in_time_summary"] == {
        "classification_counts": {"vintage_date_only": 2},
        "has_revisions": True,
        "latest_available_date": "2024-03-01",
    }
    assert result["observations"][0]["point_in_time"]["available_timestamp_utc"] is None
    assert result["observations"][1]["value"] == 3.8


def test_exact_release_timestamp_requires_timestamp_and_date_only_rejects_fabricated_timestamp():
    missing_timestamp = _request()
    missing_timestamp["observations"][0]["point_in_time"].pop("available_timestamp_utc")
    with pytest.raises(ValueError, match="available_timestamp_utc is required"):
        _run(missing_timestamp)

    fabricated_timestamp = _request()
    fabricated_timestamp["observations"][0]["point_in_time"] = {
        "classification": "release_date_only",
        "available_date": "2024-02-02",
        "available_timestamp_utc": "2024-02-02T13:30:00Z",
    }
    with pytest.raises(ValueError, match="must not include available_timestamp_utc"):
        _run(fabricated_timestamp)


def test_mismatched_available_date_duplicate_identity_and_unordered_series_fail():
    mismatched = _request()
    mismatched["observations"][0]["point_in_time"]["available_date"] = "2024-02-03"
    with pytest.raises(ValueError, match="must match the UTC date"):
        _run(mismatched)

    duplicate = _request()
    duplicate["observations"][1]["observation_date"] = "2024-01-01"
    duplicate["observations"][1]["point_in_time"]["available_date"] = "2024-02-02"
    duplicate["observations"][1]["point_in_time"]["available_timestamp_utc"] = "2024-02-02T13:30:00Z"
    with pytest.raises(ValueError, match="duplicate observation identity"):
        _run(duplicate)

    unordered = _request()
    unordered["observations"] = list(reversed(unordered["observations"]))
    with pytest.raises(ValueError, match="must be strictly ordered"):
        _run(unordered)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "", "provider must be non-empty text"),
        ("series_id", "", "series_id must be non-empty text"),
        ("frequency", "", "frequency must be non-empty text"),
        ("units", "", "units must be non-empty text"),
    ],
)
def test_required_top_level_text_fields_fail(field, value, message):
    request = _request()
    request[field] = value
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_non_finite_values_and_unknown_fields_fail():
    non_finite = _request()
    non_finite["observations"][0]["value"] = float("nan")
    with pytest.raises(ValueError, match="value must be finite"):
        _run(non_finite)

    unknown = _request()
    unknown["observations"][0]["unexpected"] = "x"
    with pytest.raises(ValueError, match="unknown field"):
        _run(unknown)


def test_module_does_not_import_network_or_provider_clients():
    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "fredapi",
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
