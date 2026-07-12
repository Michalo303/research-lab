from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from research_lab.execution.fred_alfred_readonly_adapter_v1 import (
    build_fred_alfred_readonly_adapter,
)


MODULE_PATH = Path("research_lab/execution/fred_alfred_readonly_adapter_v1.py")


def _request(provider: str = "FRED") -> dict[str, object]:
    return {
        "version": "fred_alfred_readonly_adapter_request_v1",
        "provider": provider,
        "series_id": "UNRATE",
        "frequency": "monthly",
        "units": "percent",
        "approved_host": "api.stlouisfed.org",
        "timeout_seconds": 5,
        "max_response_bytes": 100_000,
        "max_observations": 10,
        "live_access": True,
        "provenance": {"source": "unit_test"},
    }


def _response(payload: dict[str, Any], *, final_url: str = "https://api.stlouisfed.org/fred/series/observations") -> dict[str, Any]:
    body_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "status_code": 200,
        "final_url": final_url,
        "body_bytes": body_text.encode("utf-8"),
    }


def _fake_get(response: dict[str, Any]):
    def _getter(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        assert url.startswith("https://api.stlouisfed.org/fred/series/observations?")
        assert timeout_seconds == 5
        assert max_response_bytes == 100_000
        assert headers["User-Agent"] == "research-lab/0.1 macro-readonly"
        return response

    return _getter


def _run(request: dict[str, object], response: dict[str, Any]) -> dict[str, object]:
    return build_fred_alfred_readonly_adapter(copy.deepcopy(request), http_get=_fake_get(response))


def test_fred_response_maps_to_release_date_only_contract():
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"},
            {"date": "2024-02-01", "value": "3.9", "realtime_start": "2024-03-08", "realtime_end": "2024-03-08"},
        ]
    }

    result = _run(_request("FRED"), _response(payload))

    assert result["status"] == "SUCCESS"
    assert result["provider"] == "FRED"
    assert result["provider_calls_used"] == 1
    assert result["network_used"] is True
    assert result["macro_series_contract"]["point_in_time_summary"] == {
        "classification_counts": {"release_date_only": 2},
        "has_revisions": False,
        "latest_available_date": "2024-03-08",
    }
    assert result["macro_series_contract"]["observations"][0]["point_in_time"]["available_timestamp_utc"] is None


def test_alfred_response_is_vintage_aware_without_fabricated_timestamp():
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-01", "realtime_end": "2024-02-29"},
            {"date": "2024-01-01", "value": "3.8", "realtime_start": "2024-03-01", "realtime_end": "2024-03-31"},
        ]
    }

    result = _run(_request("ALFRED"), _response(payload))

    assert result["provider"] == "ALFRED"
    assert result["macro_series_contract"]["point_in_time_summary"] == {
        "classification_counts": {"vintage_date_only": 2},
        "has_revisions": True,
        "latest_available_date": "2024-03-01",
    }
    assert result["production_runtime_supported"] is False


def test_non_numeric_missing_value_and_too_many_observations_fail_closed():
    payload = {"observations": [{"date": "2024-01-01", "value": ".", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"}]}
    with pytest.raises(ValueError, match="numeric"):
        _run(_request("FRED"), _response(payload))

    request = _request("FRED")
    request["max_observations"] = 1
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"},
            {"date": "2024-02-01", "value": "3.9", "realtime_start": "2024-03-08", "realtime_end": "2024-03-08"},
        ]
    }
    with pytest.raises(ValueError, match="max_observations"):
        _run(request, _response(payload))


def test_redirected_host_non_https_and_unknown_fields_are_rejected():
    payload = {"observations": [{"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02", "realtime_end": "2024-02-02"}]}
    with pytest.raises(ValueError, match="approved host"):
        _run(_request("FRED"), _response(payload, final_url="https://evil.example/fred/series/observations"))

    with pytest.raises(ValueError, match="HTTPS"):
        _run(_request("FRED"), _response(payload, final_url="http://api.stlouisfed.org/fred/series/observations"))

    bad_payload = {
        "observations": [
            {
                "date": "2024-01-01",
                "value": "3.7",
                "realtime_start": "2024-02-02",
                "realtime_end": "2024-02-02",
                "unexpected": "x",
            }
        ]
    }
    with pytest.raises(ValueError, match="unknown field"):
        _run(_request("FRED"), _response(bad_payload))


def test_module_does_not_import_provider_sdks():
    forbidden_roots = (
        "requests",
        "fredapi",
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
