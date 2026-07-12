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


MODULE_PATH = Path("research_lab/execution/ecb_sdmx_readonly_adapter_v1.py")


def _request(classification: str = "release_date_only") -> dict[str, object]:
    return {
        "version": "ecb_sdmx_readonly_adapter_request_v1",
        "provider": "ECB_SDMX",
        "flow_ref": "EXR",
        "series_key": "D.USD.EUR.SP00.A",
        "frequency": "daily",
        "units": "fx_rate",
        "approved_host": "data-api.ecb.europa.eu",
        "point_in_time": {
            "classification": classification,
            "available_date": "2024-03-01",
        },
        "timeout_seconds": 5,
        "max_response_bytes": 100_000,
        "max_observations": 10,
        "live_access": True,
        "provenance": {"source": "unit_test"},
    }


def _payload() -> dict[str, Any]:
    return {
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
                    {
                        "id": "TIME_PERIOD",
                        "values": [
                            {"id": "2024-01-01"},
                            {"id": "2024-01-02"},
                        ],
                    }
                ]
            }
        },
    }


def _response(payload: dict[str, Any], *, final_url: str = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A") -> dict[str, Any]:
    body_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "status_code": 200,
        "final_url": final_url,
        "body_bytes": body_text.encode("utf-8"),
    }


def _fake_get(response: dict[str, Any]):
    def _getter(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        assert url.startswith("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?")
        assert timeout_seconds == 5
        assert max_response_bytes == 100_000
        assert headers["User-Agent"] == "research-lab/0.1 macro-readonly"
        return response

    return _getter


def _run(request: dict[str, object], response: dict[str, Any]) -> dict[str, object]:
    return build_ecb_sdmx_readonly_adapter(copy.deepcopy(request), http_get=_fake_get(response))


def test_sdmx_payload_maps_to_macro_series_contract():
    result = _run(_request(), _response(_payload()))

    assert result["status"] == "SUCCESS"
    assert result["provider"] == "ECB_SDMX"
    assert result["flow_ref"] == "EXR"
    assert result["series_key"] == "D.USD.EUR.SP00.A"
    assert result["macro_series_contract"]["point_in_time_summary"] == {
        "classification_counts": {"release_date_only": 2},
        "has_revisions": False,
        "latest_available_date": "2024-03-01",
    }


def test_vintage_date_only_request_is_supported_without_timestamp():
    result = _run(_request("vintage_date_only"), _response(_payload()))
    assert result["macro_series_contract"]["point_in_time_summary"]["classification_counts"] == {
        "vintage_date_only": 2
    }
    assert result["production_runtime_supported"] is False


def test_non_https_redirect_host_and_too_many_observations_fail():
    with pytest.raises(ValueError, match="HTTPS"):
        _run(_request(), _response(_payload(), final_url="http://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"))

    with pytest.raises(ValueError, match="approved host"):
        _run(_request(), _response(_payload(), final_url="https://evil.example/service/data/EXR/D.USD.EUR.SP00.A"))

    request = _request()
    request["max_observations"] = 1
    with pytest.raises(ValueError, match="max_observations"):
        _run(request, _response(_payload()))


def test_invalid_observations_and_unknown_fields_fail_closed():
    bad_payload = _payload()
    bad_payload["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = ["NaN"]
    with pytest.raises(ValueError, match="numeric"):
        _run(_request(), _response(bad_payload))

    bad_payload = _payload()
    bad_payload["dataSets"][0]["series"]["0:0:0:0:0"]["unexpected"] = {}
    with pytest.raises(ValueError, match="unknown field"):
        _run(_request(), _response(bad_payload))


def test_module_does_not_import_provider_sdks():
    forbidden_roots = (
        "requests",
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
