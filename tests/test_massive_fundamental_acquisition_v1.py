from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

import research_lab.research.massive_fundamental_acquisition_v1 as acquisition
from research_lab.research.massive_fundamental_acquisition_v1 import (
    build_massive_fundamental_acquisition_plan_v1,
    run_massive_fundamental_acquisition_v1,
    verify_massive_fundamental_bundle_v1,
)


def _write_manifest(path: Path, symbols: tuple[str, ...] = ("AAA", "BBB")) -> str:
    payload = {
        "version": "eodhd_qlib_dataset_manifest_v1",
        "dataset_id": "fixture-eodhd",
        "created_utc": "2026-08-01T00:00:00Z",
        "instruments": [
            {
                "instrument_id": f"EODHD-US-XNAS-{symbol}",
                "symbol": f"{symbol}.US",
                "qlib_instrument": symbol,
                "instrument_type": "COMMON_STOCK",
                "exchange_mic": "XNAS",
                "listing_start": "2009-01-01",
                "listing_end": None,
                "ohlcv_path": f"ohlcv/{symbol}.csv",
                "ohlcv_sha256": hashlib.sha256(symbol.encode()).hexdigest(),
            }
            for symbol in symbols
        ],
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    body = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _request(tmp_path: Path, symbols: tuple[str, ...] = ("AAA", "BBB")) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / "dataset_manifest.json"
    expected = _write_manifest(manifest, symbols)
    return {
        "version": "massive_fundamental_acquisition_request_v1",
        "acquisition_id": "MASSIVE-FUNDAMENTALS-2009-2022-V1",
        "output_dir": str((tmp_path / "out").resolve()),
        "source_manifest_path": str(manifest.resolve()),
        "expected_source_manifest_sha256": expected,
        "filing_start": "2009-01-01",
        "filing_end": "2022-12-31",
        "maximum_call_units": 10_000,
        "minimum_request_interval_seconds": 12.5,
        "maximum_pages_per_ticker": 3,
        "maximum_attempts_per_request": 3,
        "timeout_seconds": 30,
        "maximum_response_bytes": 10_000_000,
        "provenance": {"source": "operator_approved_massive_fundamental_acquisition_v1"},
    }


def _payload(symbol: str, cik: str, *, filing_date: str = "2019-05-01") -> bytes:
    return json.dumps(
        {
            "status": "OK",
            "results": [
                {
                    "cik": cik,
                    "tickers": [symbol],
                    "filing_date": filing_date,
                    "end_date": "2019-03-31",
                    "fiscal_year": 2019,
                    "fiscal_period": "Q1",
                    "timeframe": "quarterly",
                    "source_filing_url": "https://www.sec.gov/Archives/example",
                    "financials": {
                        "income_statement": {
                            "revenues": {"value": 100.0, "unit": "USD"},
                            "net_income_loss": {"value": 10.0, "unit": "USD"},
                        },
                        "balance_sheet": {
                            "assets": {"value": 250.0, "unit": "USD"},
                        },
                    },
                },
                {
                    "cik": cik,
                    "tickers": [symbol],
                    "filing_date": filing_date,
                    "end_date": "2019-03-31",
                    "timeframe": "ttm",
                    "financials": {"income_statement": {"revenues": {"value": 999.0}}},
                },
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_plan_is_hash_bound_closed_and_performs_no_provider_call(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(acquisition, "_download_http_response", lambda *args, **kwargs: pytest.fail("provider called"))

    plan = build_massive_fundamental_acquisition_plan_v1(request)

    assert plan["provider"] == "MASSIVE"
    assert plan["approved_host"] == "api.massive.com"
    assert plan["endpoint_path"] == "/vX/reference/financials"
    assert plan["subject_count"] == 2
    assert [item["lookup_ticker"] for item in plan["subjects"]] == ["AAA", "BBB"]
    assert plan["provider_calls_used"] == 0
    assert plan["sealed_oos_opened"] is False
    assert "apiKey" not in json.dumps(plan)
    assert len(plan["canonical_plan_sha256"]) == 64


def test_plan_rejects_manifest_hash_drift_and_unknown_request_field(tmp_path: Path) -> None:
    wrong_hash = _request(tmp_path)
    wrong_hash["expected_source_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest hash"):
        build_massive_fundamental_acquisition_plan_v1(wrong_hash)

    unknown = _request(tmp_path / "other")
    unknown["api_key"] = "secret"
    with pytest.raises(ValueError, match="fields"):
        build_massive_fundamental_acquisition_plan_v1(unknown)


def test_execute_writes_deterministic_secret_free_bundle_and_verify_passes(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    secret = "super-secret-massive-key"
    monkeypatch.setenv("MASSIVE_API_KEY", secret)
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)

    def fake_download(url: str, *, timeout_seconds: int, maximum_bytes: int):
        symbol = "AAA" if "ticker=AAA" in url else "BBB"
        assert f"apiKey={secret}" in url
        return _payload(symbol, "0000000001" if symbol == "AAA" else "0000000002"), {}

    monkeypatch.setattr(acquisition, "_download_http_response", fake_download)

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "COMPLETE"
    assert result["provider_http_requests_used"] == 2
    assert result["usable_ticker_count"] == 2
    assert result["usable_record_count"] == 2
    assert result["ambiguous_ticker_count"] == 0
    assert result["sealed_oos_opened"] is False
    root = Path(str(request["output_dir"]))
    assert (root / "COMPLETE").is_file()
    assert verify_massive_fundamental_bundle_v1(root)["status"] == "PASS"
    all_public = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
    assert secret.encode() not in all_public
    normalized_paths = sorted((root / "normalized").rglob("*.json.gz"))
    assert len(normalized_paths) == 2
    normalized = json.loads(gzip.decompress(normalized_paths[0].read_bytes()).decode())
    assert normalized["records"][0]["timeframe"] == "quarterly"
    assert len(normalized["records"]) == 1
    checksums_path = root / "checksums.json"
    checksums = json.loads(checksums_path.read_text())
    checksums["canonical_checksums_sha256"] = "0" * 64
    checksums_path.write_text(json.dumps(checksums), encoding="utf-8")
    assert verify_massive_fundamental_bundle_v1(root)["status"] == "FAIL"


def test_multiple_ciks_are_rejected_as_ambiguous_not_guessed(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path, symbols=("AAA",))
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    first = json.loads(_payload("AAA", "0000000001"))
    second = json.loads(_payload("AAA", "0000000002", filing_date="2020-05-01"))
    body = json.dumps({"status": "OK", "results": first["results"][:1] + second["results"][:1]}).encode()
    monkeypatch.setattr(acquisition, "_download_http_response", lambda *args, **kwargs: (body, {}))

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "COMPLETE"
    assert result["usable_ticker_count"] == 0
    assert result["ambiguous_ticker_count"] == 1
    manifest = json.loads((Path(str(request["output_dir"])) / "fundamental_manifest.json").read_text())
    assert manifest["records"][0]["status"] == "AMBIGUOUS_ISSUER_IDENTITY"
    assert manifest["records"][0]["normalized_path"] is None


def test_polygon_filing_url_is_reduced_to_atomic_sec_accession_identity(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path, symbols=("AAA",))
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    payload = json.loads(_payload("AAA", "0000320193"))
    payload["results"][0]["source_filing_url"] = (
        "https://api.polygon.io/v1/reference/sec/filings/0000320193-19-000010?apiKey=must-not-survive"
    )
    monkeypatch.setattr(
        acquisition,
        "_download_http_response",
        lambda *args, **kwargs: (json.dumps(payload).encode(), {}),
    )

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "COMPLETE"
    normalized_path = next((Path(str(request["output_dir"])) / "normalized").rglob("*.json.gz"))
    history = json.loads(gzip.decompress(normalized_path.read_bytes()))
    record = history["records"][0]
    assert record["source_filing_identity"] == "SEC_ACCESSION:0000320193-19-000010"
    assert "apiKey" not in repr(record)


def test_retryable_429_is_bounded_and_resume_skips_completed_ticker(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    calls: list[str] = []
    bbb_fails = True

    def fake_download(url: str, *, timeout_seconds: int, maximum_bytes: int):
        nonlocal bbb_fails
        symbol = "AAA" if "ticker=AAA" in url else "BBB"
        calls.append(symbol)
        if symbol == "AAA" and calls.count("AAA") == 1:
            raise acquisition.RetryableProviderFailure(429, retry_after_seconds=1.0)
        if symbol == "BBB" and bbb_fails:
            raise acquisition.RetryableProviderFailure(503, retry_after_seconds=0.0)
        return _payload(symbol, "0000000001" if symbol == "AAA" else "0000000002"), {}

    monkeypatch.setattr(acquisition, "_download_http_response", fake_download)
    first = run_massive_fundamental_acquisition_v1(request)
    assert first["status"] == "PROVIDER_ACQUISITION_INCOMPLETE"
    assert calls.count("AAA") == 2
    assert calls.count("BBB") == 3

    bbb_fails = False
    second = run_massive_fundamental_acquisition_v1(request)
    assert second["status"] == "COMPLETE"
    assert calls.count("AAA") == 2
    assert calls.count("BBB") == 4


def test_missing_key_fails_before_any_output_or_call(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setattr(acquisition, "_download_http_response", lambda *args, **kwargs: pytest.fail("provider called"))

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "MASSIVE_API_KEY_UNAVAILABLE"
    assert not Path(str(request["output_dir"])).exists()


def test_entitlement_failure_aborts_after_first_request(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise acquisition.FatalAcquisitionFailure("MASSIVE_ENTITLEMENT_UNAVAILABLE")

    monkeypatch.setattr(acquisition, "_download_http_response", forbidden)

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "MASSIVE_ENTITLEMENT_UNAVAILABLE"
    assert calls == 1


def test_unexpected_runtime_failure_preserves_truthful_call_accounting(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path, symbols=("AAA",))
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setattr(acquisition.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        acquisition,
        "_download_http_response",
        lambda *args, **kwargs: (_payload("AAA", "0000000001"), {}),
    )
    monkeypatch.setattr(acquisition, "_write_progress", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated")))

    result = run_massive_fundamental_acquisition_v1(request)

    assert result["status"] == "ACQUISITION_RUNTIME_FAILED"
    assert result["provider_http_requests_used"] == 1
    assert result["provider_call_units_used"] == 1
    assert Path(str(result["staging_dir"])).is_dir()
    assert "simulated" not in repr(result)
