from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import research_lab.research.massive_sec_filing_audit_v1 as audit_module
from research_lab.research.massive_sec_filing_audit_v1 import (
    audit_massive_record_against_sec_companyfacts_v1,
    build_massive_sec_audit_sample_v1,
    run_massive_sec_filing_audit_v1,
)


def _record(index: int, *, revenue: float = 100.0) -> dict[str, object]:
    cik = str(index + 1).zfill(10)
    accession = f"{cik}-20-{index + 1:06d}"
    return {
        "cik": cik,
        "requested_instrument_id": f"I-{index:03d}",
        "requested_ticker": f"T{index:03d}",
        "reported_tickers": [f"T{index:03d}"],
        "filing_date": "2020-02-15",
        "period_end_date": "2019-12-31",
        "timeframe": "annual",
        "fiscal_year": 2019,
        "fiscal_period": "FY",
        "source_filing_identity": f"SEC_ACCESSION:{accession}",
        "statements": {
            "income_statement": {
                "revenues": {"value": revenue, "unit": "USD"},
                "gross_profit": {"value": 40.0, "unit": "USD"},
                "operating_income_loss": {"value": 20.0, "unit": "USD"},
                "net_income_loss": {"value": 10.0, "unit": "USD"},
            },
            "balance_sheet": {
                "assets": {"value": 500.0, "unit": "USD"},
                "liabilities": {"value": 200.0, "unit": "USD"},
            },
            "cash_flow_statement": {
                "net_cash_flow_from_operating_activities": {"value": 30.0, "unit": "USD"},
            },
        },
        "canonical_record_sha256": "a" * 64,
    }


def _companyfacts(record: dict[str, object], *, revenue: float = 100.0) -> dict[str, object]:
    accession = str(record["source_filing_identity"]).split(":", 1)[1]
    common = {
        "accn": accession,
        "filed": record["filing_date"],
        "end": record["period_end_date"],
        "form": "10-K",
        "fy": 2019,
        "fp": "FY",
    }
    concepts = {
        "Revenues": revenue,
        "GrossProfit": 40.0,
        "OperatingIncomeLoss": 20.0,
        "NetIncomeLoss": 10.0,
        "Assets": 500.0,
        "Liabilities": 200.0,
        "NetCashProvidedByUsedInOperatingActivities": 30.0,
    }
    return {
        "cik": int(record["cik"]),
        "facts": {
            "us-gaap": {
                concept: {"units": {"USD": [{**common, "val": value}]}}
                for concept, value in concepts.items()
            }
        },
    }


def _history(record: dict[str, object]) -> dict[str, object]:
    return {
        "version": "massive_normalized_fundamental_history_v1",
        "instrument_id": record["requested_instrument_id"],
        "lookup_ticker": record["requested_ticker"],
        "cik": record["cik"],
        "listing_start": "2009-01-01",
        "listing_end": None,
        "records": [record],
        "canonical_history_sha256": "b" * 64,
    }


def _request(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "fundamental"
    root.mkdir()
    manifest = root / "fundamental_manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    return {
        "version": "massive_sec_filing_audit_request_v1",
        "audit_id": "MASSIVE-SEC-AUDIT-001",
        "fundamental_bundle_root": str(root.resolve()),
        "expected_fundamental_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "expected_fundamental_canonical_manifest_sha256": "c" * 64,
        "output_dir": str((tmp_path / "audit-output").resolve()),
        "sample_size": 30,
        "minimum_matched_fields_per_record": 3,
        "maximum_call_units": 30,
        "minimum_request_interval_seconds": 0.2,
        "timeout_seconds": 30,
        "maximum_response_bytes": 20_000_000,
        "provenance": {"source": "operator_approved_sec_filing_audit_v1"},
    }


def test_value_audit_matches_accession_date_period_and_sec_values() -> None:
    record = _record(0)

    result = audit_massive_record_against_sec_companyfacts_v1(record, _companyfacts(record))

    assert result["status"] == "PASS"
    assert result["matched_field_count"] == 7
    assert result["accession"] == "0000000001-20-000001"
    assert result["mismatched_fields"] == []
    mismatched = audit_massive_record_against_sec_companyfacts_v1(record, _companyfacts(record, revenue=999.0))
    assert mismatched["status"] == "PASS"
    assert "revenues" in mismatched["mismatched_fields"]
    assert mismatched["matched_field_count"] == 6


def test_sample_is_deterministic_and_uses_annual_accession_records() -> None:
    histories = {f"T{index:03d}": _history(_record(index)) for index in range(40)}

    first = build_massive_sec_audit_sample_v1(histories, sample_size=30)
    second = build_massive_sec_audit_sample_v1(dict(reversed(list(histories.items()))), sample_size=30)

    assert [item["requested_ticker"] for item in first] == [item["requested_ticker"] for item in second]
    assert len(first) == 30
    assert all(item["timeframe"] == "annual" for item in first)


def test_runner_publishes_pass_for_thirty_independently_matched_records(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    histories = {f"T{index:03d}": _history(_record(index)) for index in range(30)}
    monkeypatch.setenv("SEC_USER_AGENT", "research-lab audit@example.test")
    monkeypatch.setattr(audit_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        audit_module,
        "load_massive_fundamental_histories_v1",
        lambda root, expected: (
            histories,
            {
                "fundamental_manifest_sha256": expected,
                "fundamental_canonical_manifest_sha256": request["expected_fundamental_canonical_manifest_sha256"],
                "provider_calls_used": 0,
                "sealed_oos_rows_read": 0,
            },
        ),
    )

    def fake_download(cik: str, *, user_agent: str, timeout_seconds: int, maximum_bytes: int) -> bytes:
        record = next(history["records"][0] for history in histories.values() if history["cik"] == cik)
        return json.dumps(_companyfacts(record), separators=(",", ":")).encode()

    monkeypatch.setattr(audit_module, "_download_sec_companyfacts", fake_download)

    result = run_massive_sec_filing_audit_v1(request)

    assert result["status"] == "PASS"
    assert result["sample_size"] == 30
    assert result["passed_record_count"] == 30
    assert result["provider_http_requests_used"] == 30
    assert result["sealed_oos_opened"] is False
    output = Path(str(request["output_dir"]))
    assert (output / "COMPLETE").is_file()
    assert "audit@example.test" not in repr(result)


def test_missing_sec_user_agent_fails_before_network_or_output(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(
        audit_module,
        "_download_sec_companyfacts",
        lambda *args, **kwargs: pytest.fail("network called"),
    )

    result = run_massive_sec_filing_audit_v1(request)

    assert result["status"] == "SEC_USER_AGENT_UNAVAILABLE"
    assert not Path(str(request["output_dir"])).exists()


def test_runtime_failure_reports_consumed_sec_request_count(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    histories = {f"T{index:03d}": _history(_record(index)) for index in range(30)}
    monkeypatch.setenv("SEC_USER_AGENT", "research-lab audit@example.test")
    monkeypatch.setattr(audit_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        audit_module,
        "load_massive_fundamental_histories_v1",
        lambda root, expected: (
            histories,
            {
                "fundamental_manifest_sha256": expected,
                "fundamental_canonical_manifest_sha256": request["expected_fundamental_canonical_manifest_sha256"],
            },
        ),
    )
    calls = 0

    def fail_after_first(cik: str, **kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("bounded simulated SEC failure")
        record = next(history["records"][0] for history in histories.values() if history["cik"] == cik)
        return json.dumps(_companyfacts(record), separators=(",", ":")).encode()

    monkeypatch.setattr(audit_module, "_download_sec_companyfacts", fail_after_first)

    result = run_massive_sec_filing_audit_v1(request)

    assert result["status"] == "AUDIT_RUNTIME_FAILED"
    assert result["provider_http_requests_used"] == 2
    assert "bounded simulated" not in repr(result)
