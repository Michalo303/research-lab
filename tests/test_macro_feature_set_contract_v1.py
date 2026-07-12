from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from research_lab.execution.macro_feature_set_contract_v1 import (
    build_macro_feature_set_contract,
)


MODULE_PATH = Path("research_lab/execution/macro_feature_set_contract_v1.py")


def _aligned_result() -> dict[str, object]:
    return {
        "version": "macro_market_asof_alignment_contract_result_v1",
        "contract_version": "macro_market_asof_alignment_contract_v1",
        "status": "SUCCESS",
        "aligned_bars": [
            {
                "timestamp": "2024-03-01T14:30:00Z",
                "decision_timestamp_utc": "2024-03-01T14:30:00Z",
                "macro_values": {"FRED:UNRATE": 4.0, "ECB_SDMX:EXR_USD": 1.10},
                "availability_timestamps_utc": {"FRED:UNRATE": "2024-03-01T13:30:00Z", "ECB_SDMX:EXR_USD": "2024-03-01T00:00:00Z"},
                "age_staleness_days": {"FRED:UNRATE": 0, "ECB_SDMX:EXR_USD": 0},
                "missing_indicators": {"FRED:UNRATE": False, "ECB_SDMX:EXR_USD": False},
                "point_in_time_classifications": {"FRED:UNRATE": "RELEASE_AWARE", "ECB_SDMX:EXR_USD": "RELEASE_AWARE"},
            },
            {
                "timestamp": "2024-03-04T14:30:00Z",
                "decision_timestamp_utc": "2024-03-04T14:30:00Z",
                "macro_values": {"FRED:UNRATE": 5.0, "ECB_SDMX:EXR_USD": 1.20},
                "availability_timestamps_utc": {"FRED:UNRATE": "2024-03-04T13:30:00Z", "ECB_SDMX:EXR_USD": "2024-03-04T00:00:00Z"},
                "age_staleness_days": {"FRED:UNRATE": 0, "ECB_SDMX:EXR_USD": 0},
                "missing_indicators": {"FRED:UNRATE": False, "ECB_SDMX:EXR_USD": False},
                "point_in_time_classifications": {"FRED:UNRATE": "RELEASE_AWARE", "ECB_SDMX:EXR_USD": "RELEASE_AWARE"},
            },
            {
                "timestamp": "2024-03-05T14:30:00Z",
                "decision_timestamp_utc": "2024-03-05T14:30:00Z",
                "macro_values": {"FRED:UNRATE": 7.0, "ECB_SDMX:EXR_USD": 1.30},
                "availability_timestamps_utc": {"FRED:UNRATE": "2024-03-05T13:30:00Z", "ECB_SDMX:EXR_USD": "2024-03-05T00:00:00Z"},
                "age_staleness_days": {"FRED:UNRATE": 0, "ECB_SDMX:EXR_USD": 0},
                "missing_indicators": {"FRED:UNRATE": False, "ECB_SDMX:EXR_USD": False},
                "point_in_time_classifications": {"FRED:UNRATE": "RELEASE_AWARE", "ECB_SDMX:EXR_USD": "RELEASE_AWARE"},
            },
        ],
        "source_series_identities": ["ECB_SDMX:EXR_USD", "FRED:UNRATE"],
        "unsafe_series_warnings": [],
        "safety_flags": {
            "network_used": False,
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "production_runtime_supported": False,
        },
        "provenance": {"source": "unit_test"},
        "input_sha256": "1" * 64,
        "output_payload_sha256": "2" * 64,
    }


def _request() -> dict[str, object]:
    return {
        "version": "macro_feature_set_contract_request_v1",
        "aligned_macro_result": _aligned_result(),
        "feature_definitions": [
            {"feature_id": "unrate_level", "operation": "level", "source_series_id": "FRED:UNRATE", "minimum_observations": 1},
            {"feature_id": "unrate_diff", "operation": "first_difference", "source_series_id": "FRED:UNRATE", "minimum_observations": 2},
            {"feature_id": "unrate_pct", "operation": "percentage_change", "source_series_id": "FRED:UNRATE", "minimum_observations": 2},
            {"feature_id": "unrate_mean2", "operation": "rolling_mean", "source_series_id": "FRED:UNRATE", "lookback_window": 2, "minimum_observations": 2},
            {"feature_id": "unrate_std2", "operation": "rolling_stddev", "source_series_id": "FRED:UNRATE", "lookback_window": 2, "minimum_observations": 2},
            {"feature_id": "unrate_z2", "operation": "z_score", "source_series_id": "FRED:UNRATE", "lookback_window": 2, "minimum_observations": 2},
            {"feature_id": "unrate_slope2", "operation": "slope", "source_series_id": "FRED:UNRATE", "lookback_window": 2, "minimum_observations": 2},
            {"feature_id": "macro_spread", "operation": "spread", "left_source_series_id": "FRED:UNRATE", "right_source_series_id": "ECB_SDMX:EXR_USD", "minimum_observations": 1},
            {"feature_id": "unrate_high", "operation": "threshold_state", "source_series_id": "FRED:UNRATE", "threshold": 4.5, "minimum_observations": 1},
            {
                "feature_id": "unrate_bucket",
                "operation": "bounded_categorical_state",
                "source_series_id": "FRED:UNRATE",
                "bounds": [4.5, 6.0],
                "labels": ["LOW", "MID", "HIGH"],
                "minimum_observations": 1,
            },
        ],
        "missing_data_policy": "MARK_MISSING",
        "clipping_policy": {"mode": "NONE"},
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_macro_feature_set_contract(copy.deepcopy(request))


def test_feature_builder_is_deterministic_and_uses_past_only():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    third = first["feature_observations"][2]["feature_values"]
    assert third["unrate_level"] == 7.0
    assert third["unrate_diff"] == 2.0
    assert round(third["unrate_pct"], 6) == 0.4
    assert third["unrate_mean2"] == 6.0
    assert round(third["unrate_std2"], 6) == 1.0
    assert third["unrate_z2"] == 1.0
    assert third["unrate_slope2"] == 2.0
    assert round(third["macro_spread"], 6) == 5.7
    assert third["unrate_high"] == 1.0
    assert third["unrate_bucket"] == "HIGH"


def test_duplicate_ids_unknown_ops_zero_variance_and_identity_mismatch_fail_closed():
    duplicate = _request()
    duplicate["feature_definitions"].append(copy.deepcopy(duplicate["feature_definitions"][0]))
    with pytest.raises(ValueError, match="duplicate feature_id"):
        _run(duplicate)

    unknown = _request()
    unknown["feature_definitions"][0]["operation"] = "unsupported"
    with pytest.raises(ValueError, match="unknown operation"):
        _run(unknown)

    zero_variance = _request()
    zero_variance["aligned_macro_result"]["aligned_bars"][1]["macro_values"]["FRED:UNRATE"] = 4.0
    zero_variance["aligned_macro_result"]["aligned_bars"][2]["macro_values"]["FRED:UNRATE"] = 4.0
    with pytest.raises(ValueError, match="zero variance"):
        _run(zero_variance)

    mismatch = _request()
    mismatch["feature_definitions"][0]["source_series_id"] = "FRED:OTHER"
    with pytest.raises(ValueError, match="identity mismatch"):
        _run(mismatch)


def test_insufficient_history_marks_missing_under_policy():
    result = _run(_request())
    first_row = result["feature_observations"][0]
    assert first_row["missing_indicators"]["unrate_diff"] is True
    assert first_row["feature_values"]["unrate_diff"] is None


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
