from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.run_massive_fundamental_acquisition_v1 as cli_module


def _request_file(tmp_path: Path) -> tuple[Path, str, dict[str, object]]:
    manifest = tmp_path / "manifest.json"
    manifest_payload = {
        "version": "eodhd_qlib_dataset_manifest_v1",
        "dataset_id": "fixture",
        "created_utc": "2026-08-01T00:00:00Z",
        "instruments": [
            {
                "instrument_id": "I-AAA", "symbol": "AAA.US", "qlib_instrument": "AAA",
                "instrument_type": "COMMON_STOCK", "exchange_mic": "XNAS",
                "listing_start": "2009-01-01", "listing_end": None,
                "ohlcv_path": "ohlcv/aaa.csv", "ohlcv_sha256": "a" * 64,
            }
        ],
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    request = {
        "version": "massive_fundamental_acquisition_request_v1",
        "acquisition_id": "MASSIVE-FUNDAMENTALS-2009-2022-V1",
        "output_dir": str((tmp_path / "output").resolve()),
        "source_manifest_path": str(manifest.resolve()),
        "expected_source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "filing_start": "2009-01-01", "filing_end": "2022-12-31",
        "maximum_call_units": 10_000, "minimum_request_interval_seconds": 12.5,
        "maximum_pages_per_ticker": 3, "maximum_attempts_per_request": 3,
        "timeout_seconds": 30, "maximum_response_bytes": 10_000_000,
        "provenance": {"source": "operator_approved_massive_fundamental_acquisition_v1"},
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    return request_path.resolve(), hashlib.sha256(request_path.read_bytes()).hexdigest(), request


def test_cli_dry_run_uses_zero_provider_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    path, expected, request = _request_file(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "run_massive_fundamental_acquisition_v1",
        lambda _: pytest.fail("execute called during dry run"),
    )

    exit_code = cli_module.main(["--request", str(path), "--expected-request-sha256", expected])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "DRY_RUN"
    assert result["subject_count"] == 1
    assert result["provider_calls_used"] == 0
    assert not Path(str(request["output_dir"])).exists()


def test_cli_rejects_wrong_request_hash_before_execution(tmp_path: Path, monkeypatch, capsys) -> None:
    path, _, _ = _request_file(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "run_massive_fundamental_acquisition_v1",
        lambda _: pytest.fail("execute called with wrong hash"),
    )
    assert cli_module.main(["--request", str(path), "--expected-request-sha256", "0" * 64, "--execute"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "REQUEST_VALIDATION_FAILED"
