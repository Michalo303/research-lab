from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from research_lab.execution.e2e_macro_data_layer_acceptance_v1 import (
    run_e2e_macro_data_layer_acceptance,
)


MODULE_PATH = Path("research_lab/execution/e2e_macro_data_layer_acceptance_v1.py")


def _fred_transport(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"},
            {"date": "2024-02-01", "value": "3.9", "realtime_start": "2024-03-08", "realtime_end": "2024-03-08"},
        ]
    }
    return {
        "status_code": 200,
        "final_url": "https://api.stlouisfed.org/fred/series/observations",
        "body_bytes": json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    }


def _ecb_transport(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "dataSets": [
            {"series": {"0:0:0:0:0": {"observations": {"0": [1.0812], "1": [1.0825]}}}}
        ],
        "structure": {
            "dimensions": {
                "observation": [
                    {"id": "TIME_PERIOD", "values": [{"id": "2024-01-01"}, {"id": "2024-01-02"}]}
                ]
            }
        },
    }
    return {
        "status_code": 200,
        "final_url": "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        "body_bytes": json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    }


def _request() -> dict[str, object]:
    return {
        "version": "e2e_macro_data_layer_acceptance_request_v1",
        "snapshot_id": "macro-snapshot-2024-03-15-v1",
        "snapshot_date": "2024-03-15",
        "fred_request": {
            "version": "fred_alfred_readonly_adapter_request_v1",
            "provider": "FRED",
            "series_id": "UNRATE",
            "frequency": "monthly",
            "units": "percent",
            "approved_host": "api.stlouisfed.org",
            "timeout_seconds": 5,
            "max_response_bytes": 100_000,
            "max_observations": 10,
            "live_access": True,
            "provenance": {"source": "unit_test"},
        },
        "ecb_request": {
            "version": "ecb_sdmx_readonly_adapter_request_v1",
            "provider": "ECB_SDMX",
            "flow_ref": "EXR",
            "series_key": "D.USD.EUR.SP00.A",
            "frequency": "daily",
            "units": "fx_rate",
            "approved_host": "data-api.ecb.europa.eu",
            "point_in_time": {
                "classification": "release_date_only",
                "available_date": "2024-03-01",
            },
            "timeout_seconds": 5,
            "max_response_bytes": 100_000,
            "max_observations": 10,
            "live_access": True,
            "provenance": {"source": "unit_test"},
        },
        "provenance": {"source": "unit_test"},
    }


def test_e2e_acceptance_is_deterministic_and_hermetic():
    first = run_e2e_macro_data_layer_acceptance(_request(), fred_http_get=_fred_transport, ecb_http_get=_ecb_transport)
    second = run_e2e_macro_data_layer_acceptance(_request(), fred_http_get=_fred_transport, ecb_http_get=_ecb_transport)

    assert first == second
    assert first["status"] == "ACCEPTED"
    assert first["adapter_statuses"] == {"fred": "SUCCESS", "ecb": "SUCCESS"}
    assert first["snapshot_status"] == "SUCCESS"
    assert first["snapshot_series_count"] == 2
    assert first["live_network_used"] is False
    assert first["production_runtime_supported"] is False


def test_provider_failure_bubbles_up_fail_closed():
    def broken_fred(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> dict[str, Any]:
        return {
            "status_code": 200,
            "final_url": "https://api.stlouisfed.org/fred/series/observations",
            "body_bytes": json.dumps({"observations": [{"date": "2024-01-01", "value": ".", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"}]}).encode("utf-8"),
        }

    with pytest.raises(ValueError, match="numeric"):
        run_e2e_macro_data_layer_acceptance(_request(), fred_http_get=broken_fred, ecb_http_get=_ecb_transport)


def test_module_does_not_import_live_provider_clients():
    forbidden_roots = (
        "requests",
        "urllib.request",
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
