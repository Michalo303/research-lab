from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from research_lab.execution.ecb_sdmx_readonly_adapter_v1 import (
    build_ecb_sdmx_readonly_adapter,
)
from research_lab.execution.fred_alfred_readonly_adapter_v1 import (
    build_fred_alfred_readonly_adapter,
)
from research_lab.execution.immutable_macro_snapshot_contract_v1 import (
    build_immutable_macro_snapshot_contract,
)


MODULE_PATH = Path("research_lab/execution/immutable_macro_snapshot_contract_v1.py")


def _fake_fred_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"},
            {"date": "2024-02-01", "value": "3.9", "realtime_start": "2024-03-08", "realtime_end": "2024-03-08"},
        ]
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status_code": 200,
        "final_url": "https://api.stlouisfed.org/fred/series/observations",
        "body_bytes": body,
    }


def _fake_ecb_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "dataSets": [
            {
                "series": {
                    "0:0:0:0:0": {
                        "observations": {
                            "0": [1.0812],
                            "1": [1.0825],
                        }
                    }
                }
            }
        ],
        "structure": {
            "dimensions": {
                "observation": [
                    {"id": "TIME_PERIOD", "values": [{"id": "2024-01-01"}, {"id": "2024-01-02"}]}
                ]
            }
        },
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status_code": 200,
        "final_url": "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        "body_bytes": body,
    }


def _fred_result() -> dict[str, object]:
    return build_fred_alfred_readonly_adapter(
        {
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
        http_get=_fake_fred_get,
    )


def _ecb_result() -> dict[str, object]:
    return build_ecb_sdmx_readonly_adapter(
        {
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
        http_get=_fake_ecb_get,
    )


def _request() -> dict[str, object]:
    return {
        "version": "immutable_macro_snapshot_contract_request_v1",
        "snapshot_id": "macro-snapshot-2024-03-15-v1",
        "snapshot_date": "2024-03-15",
        "series_adapter_results": [_fred_result(), _ecb_result()],
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_immutable_macro_snapshot_contract(copy.deepcopy(request))


def test_snapshot_contract_is_deterministic_and_binds_series_hashes():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["version"] == "immutable_macro_snapshot_contract_result_v1"
    assert first["snapshot_version"] == "immutable_macro_snapshot_contract_v1"
    assert first["snapshot_id"] == "macro-snapshot-2024-03-15-v1"
    assert first["series_count"] == 2
    assert first["series_manifest"][0]["provider"] == "ECB_SDMX"
    assert first["series_manifest"][1]["provider"] == "FRED"
    assert first["safe_flags"]["production_runtime_supported"] is False


def test_duplicate_series_identity_and_early_snapshot_date_fail():
    request = _request()
    request["series_adapter_results"] = [_fred_result(), _fred_result()]
    with pytest.raises(ValueError, match="duplicate series identity"):
        _run(request)

    request = _request()
    request["snapshot_date"] = "2024-03-01"
    with pytest.raises(ValueError, match="snapshot_date must be on or after"):
        _run(request)


def test_mutated_adapter_result_and_unknown_fields_fail_closed():
    request = _request()
    request["series_adapter_results"][0]["production_runtime_supported"] = True
    with pytest.raises(ValueError, match="production_runtime_supported"):
        _run(request)

    request = _request()
    request["series_adapter_results"][0]["unexpected"] = "x"
    with pytest.raises(ValueError, match="unknown field"):
        _run(request)


def test_module_does_not_import_network_or_provider_clients():
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
