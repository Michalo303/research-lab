from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "bounded_eodhd_ohlcv_snapshot_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "run_bounded_eodhd_ohlcv_snapshot.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bounded_eodhd_ohlcv_snapshot_v1", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict[str, object]]:
    return [
        {"date": "2015-01-02", "open": 205.0, "high": 206.5, "low": 204.0, "close": 205.8, "volume": 123456789},
        {"date": "2015-01-05", "open": 204.5, "high": 205.2, "low": 201.5, "close": 202.9, "volume": 130000000},
        {"date": "2026-06-30", "open": 610.0, "high": 612.2, "low": 608.0, "close": 611.5, "volume": 98000000},
    ]


def _request(output_dir: Path) -> dict[str, object]:
    return {
        "version": "bounded_eodhd_ohlcv_snapshot_request_v1",
        "provider": "EODHD",
        "symbol": "SPY.US",
        "interval": "daily",
        "start_date": "2015-01-01",
        "end_date": "2026-06-30",
        "output_dir": str(output_dir.resolve()),
        "approved_host": "eodhd.com",
        "timeout_seconds": 20,
        "max_response_bytes": 2_000_000,
        "live_access": True,
        "provenance": {"source": "unit_test"},
    }


def _http_result(rows: object, *, final_url: str = "https://eodhd.com/api/eod/SPY.US?api_token=secret&fmt=json&from=2015-01-01&period=d") -> tuple[object, dict[str, object]]:
    body = json.dumps(rows, separators=(",", ":"))
    return rows, {
        "http_status": 200,
        "content_type": "application/json",
        "body_length": len(body),
        "body_text": body,
        "final_url": final_url,
    }


def test_writes_bounded_snapshot_artifacts_and_normalized_file_is_adapter_compatible(tmp_path):
    module = _load_module()
    output_dir = tmp_path / "snapshot"

    def fake_http_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        assert "api_token=secret" in url
        assert timeout_seconds == 20
        assert max_response_bytes == 2_000_000
        assert headers["User-Agent"].startswith("research-lab/")
        return _http_result(_rows())

    result = module.acquire_bounded_eodhd_ohlcv_snapshot(
        copy.deepcopy(_request(output_dir)),
        api_key="secret",
        http_get=fake_http_get,
        retrieval_utc=datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "SUCCESS"
    assert result["provider"] == "EODHD"
    assert result["symbol"] == "SPY.US"
    assert result["interval"] == "daily"
    assert result["provider_calls_used"] == 1
    assert result["network_used"] is True
    assert result["production_runtime_supported"] is False
    assert set(path.name for path in output_dir.iterdir()) == {
        "COMPLETE",
        "acquisition_request.json",
        "checksums.json",
        "metadata.json",
        "normalized_ohlcv.json",
        "raw_response.json",
    }
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["sanitized_endpoint_identity"] == "https://eodhd.com/api/eod/SPY.US"
    assert metadata["http_status"] == 200
    assert metadata["response_byte_size"] > 0
    assert "secret" not in json.dumps(metadata, sort_keys=True)

    normalized_path = output_dir / "normalized_ohlcv.json"
    adapter_result = build_local_ohlcv_file_input_adapter(
        {
            "version": "local_ohlcv_file_input_adapter_request_v1",
            "file_path": str(normalized_path.resolve()),
            "format": "json",
            "dataset_id": "eodhd-spy-us-daily-2015-2026-v1",
            "symbol": "SPY.US",
            "max_bytes": 2_000_000,
            "max_rows": 5000,
            "provenance": {"source": "unit_test"},
        }
    )
    assert adapter_result["status"] == "SUCCESS"
    assert adapter_result["row_count"] == 3


def test_requires_live_access_and_explicit_output_dir(tmp_path):
    module = _load_module()
    request = _request(tmp_path / "snapshot")
    request["live_access"] = False

    with pytest.raises(ValueError, match="live_access"):
        module.acquire_bounded_eodhd_ohlcv_snapshot(request, api_key="secret", http_get=lambda **_: None)

    request = _request(tmp_path / "snapshot")
    request["output_dir"] = str(ROOT)
    with pytest.raises(ValueError, match="unsafe_output_dir"):
        module.acquire_bounded_eodhd_ohlcv_snapshot(request, api_key="secret", http_get=lambda **_: None)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{**_rows()[0]}, {**_rows()[0]}], "duplicate"),
        (list(reversed(_rows())), "ordered"),
        ([{**_rows()[0], "high": 200.0}], "high"),
        ([{**_rows()[0], "volume": 0}], "volume"),
        ([{**_rows()[0], "close": float("nan")}], "finite"),
        ([{**_rows()[0], "date": "2014-12-31"}], "approved range"),
        ({"error": "invalid", "message": "bad request"}, "API error"),
    ],
)
def test_rejects_invalid_provider_payloads(rows, message, tmp_path):
    module = _load_module()

    def fake_http_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        return _http_result(rows)

    with pytest.raises(ValueError, match=message):
        module.acquire_bounded_eodhd_ohlcv_snapshot(
            copy.deepcopy(_request(tmp_path / "snapshot")),
            api_key="secret",
            http_get=fake_http_get,
            retrieval_utc=datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc),
        )


def test_rejects_unapproved_host_redirect_and_response_size(tmp_path):
    module = _load_module()

    def redirecting_http_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        return _http_result(_rows(), final_url="https://evil.example/api/eod/SPY.US?api_token=secret")

    with pytest.raises(ValueError, match="approved host"):
        module.acquire_bounded_eodhd_ohlcv_snapshot(
            copy.deepcopy(_request(tmp_path / "redirect")),
            api_key="secret",
            http_get=redirecting_http_get,
            retrieval_utc=datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc),
        )

    def oversized_http_get(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]):
        payload, meta = _http_result(_rows())
        meta["body_length"] = max_response_bytes + 1
        return payload, meta

    with pytest.raises(ValueError, match="max_response_bytes"):
        module.acquire_bounded_eodhd_ohlcv_snapshot(
            copy.deepcopy(_request(tmp_path / "oversized")),
            api_key="secret",
            http_get=oversized_http_get,
            retrieval_utc=datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc),
        )


def test_cli_runs_with_fake_live_key_and_writes_snapshot(tmp_path, monkeypatch):
    output_dir = tmp_path / "snapshot"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request(output_dir), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    module = _load_module()
    monkeypatch.setattr(
        module,
        "_http_get_json",
        lambda url, *, timeout_seconds, max_response_bytes, headers: _http_result(_rows()),
    )
    monkeypatch.setattr(module, "_load_eodhd_api_key", lambda: "secret")

    payload = module.main(["--request", str(request_path.resolve())])
    assert payload == 0
    assert (output_dir / "COMPLETE").exists()


def test_module_and_cli_do_not_import_pandas_requests_or_runtime_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "research_lab.llm",
        "pandas",
        "requests",
        "aiohttp",
        "ibapi",
        "ib_insync",
    )
    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
            ), f"{path.name} imported forbidden module {import_name}"
