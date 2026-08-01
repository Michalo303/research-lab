from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from research_lab.research.massive_fundamental_dataset_v1 import (
    load_massive_fundamental_histories_v1,
)


REQUEST_VERSION = "massive_sec_filing_audit_request_v1"
AUDIT_VERSION = "massive_sec_filing_audit_v1"
PROVENANCE_SOURCE = "operator_approved_sec_filing_audit_v1"
SAMPLE_SIZE = 30
MINIMUM_MATCHED_FIELDS = 3
MAXIMUM_CALL_UNITS = 30
MINIMUM_REQUEST_INTERVAL_SECONDS = 0.2
TIMEOUT_SECONDS = 30
MAXIMUM_RESPONSE_BYTES = 20_000_000
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ACCESSION_RE = re.compile(r"^SEC_ACCESSION:(\d{10}-\d{2}-\d{6})$")
_FIELD_CONCEPTS = {
    "revenues": (
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income_loss": ("OperatingIncomeLoss",),
    "net_income_loss": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "net_cash_flow_from_operating_activities": (
        "NetCashProvidedByUsedInOperatingActivities",
    ),
}
_FIELD_LOCATIONS = {
    "revenues": "income_statement",
    "gross_profit": "income_statement",
    "operating_income_loss": "income_statement",
    "net_income_loss": "income_statement",
    "assets": "balance_sheet",
    "liabilities": "balance_sheet",
    "net_cash_flow_from_operating_activities": "cash_flow_statement",
}
_REQUEST_FIELDS = {
    "version",
    "audit_id",
    "fundamental_bundle_root",
    "expected_fundamental_manifest_sha256",
    "expected_fundamental_canonical_manifest_sha256",
    "output_dir",
    "sample_size",
    "minimum_matched_fields_per_record",
    "maximum_call_units",
    "minimum_request_interval_seconds",
    "timeout_seconds",
    "maximum_response_bytes",
    "provenance",
}


def audit_massive_record_against_sec_companyfacts_v1(
    record: dict[str, object],
    companyfacts: dict[str, object],
    *,
    minimum_matched_fields: int = MINIMUM_MATCHED_FIELDS,
) -> dict[str, object]:
    """Compare one Massive annual record with one official SEC Company Facts response."""

    accession_match = _ACCESSION_RE.fullmatch(str(record.get("source_filing_identity", "")))
    if accession_match is None:
        raise ValueError("record lacks an atomic SEC accession identity")
    accession = accession_match.group(1)
    cik = _normalize_cik(record.get("cik"))
    if _normalize_cik(companyfacts.get("cik")) != cik:
        return _record_audit_result(record, accession, [], list(_FIELD_CONCEPTS), [], "FAIL_CIK_MISMATCH")
    filing_date = str(record.get("filing_date", ""))
    period_end = str(record.get("period_end_date", ""))
    source_values = _massive_values(record)
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []
    facts = companyfacts.get("facts")
    us_gaap = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(us_gaap, dict):
        us_gaap = {}
    for field, source_value in source_values.items():
        sec_values: list[float] = []
        for concept in _FIELD_CONCEPTS[field]:
            concept_body = us_gaap.get(concept)
            units = concept_body.get("units") if isinstance(concept_body, dict) else None
            observations = units.get("USD") if isinstance(units, dict) else None
            if not isinstance(observations, list):
                continue
            for observation in observations:
                if not isinstance(observation, dict):
                    continue
                if (
                    observation.get("accn") != accession
                    or observation.get("filed") != filing_date
                    or observation.get("end") != period_end
                ):
                    continue
                value = _finite_number(observation.get("val"))
                if value is not None:
                    sec_values.append(value)
        if not sec_values:
            missing.append(field)
        elif any(_values_equal(source_value, value) for value in sec_values):
            matched.append(field)
        else:
            mismatched.append(field)
    if len(matched) >= minimum_matched_fields:
        status = "PASS"
    else:
        status = "FAIL_INSUFFICIENT_MATCHED_FIELDS"
    return _record_audit_result(record, accession, matched, mismatched, missing, status)


def build_massive_sec_audit_sample_v1(
    histories: dict[str, dict[str, object]],
    *,
    sample_size: int = SAMPLE_SIZE,
) -> list[dict[str, object]]:
    """Select one latest auditable annual filing per issuer in stable hash order."""

    if sample_size != SAMPLE_SIZE:
        raise ValueError("sample size is not frozen")
    latest_by_cik: dict[str, dict[str, object]] = {}
    for history in histories.values():
        cik = _normalize_cik(history.get("cik"))
        candidates = []
        for raw in history.get("records", []):
            if not isinstance(raw, dict) or raw.get("timeframe") != "annual":
                continue
            if _ACCESSION_RE.fullmatch(str(raw.get("source_filing_identity", ""))) is None:
                continue
            if len(_massive_values(raw)) < MINIMUM_MATCHED_FIELDS:
                continue
            candidates.append(raw)
        if not candidates:
            continue
        latest = max(
            candidates,
            key=lambda item: (
                str(item.get("filing_date", "")),
                str(item.get("period_end_date", "")),
                str(item.get("canonical_record_sha256", "")),
            ),
        )
        current = latest_by_cik.get(cik)
        latest_key = (
            str(latest.get("filing_date", "")),
            str(latest.get("period_end_date", "")),
            str(latest.get("canonical_record_sha256", "")),
        )
        current_key = (
            str(current.get("filing_date", "")),
            str(current.get("period_end_date", "")),
            str(current.get("canonical_record_sha256", "")),
        ) if current is not None else None
        if current_key is None or latest_key > current_key:
            latest_by_cik[cik] = latest
    ordered = sorted(
        latest_by_cik.values(),
        key=lambda item: hashlib.sha256(
            f"{_normalize_cik(item.get('cik'))}|{item.get('requested_instrument_id', '')}".encode("utf-8")
        ).hexdigest(),
    )
    if len(ordered) < sample_size:
        raise ValueError("insufficient independently auditable annual records")
    return ordered[:sample_size]


def build_massive_sec_filing_audit_plan_v1(request: dict[str, object]) -> dict[str, object]:
    """Verify the frozen local input and select the exact sample without network access."""

    validated = _validate_request(request)
    histories, metadata = load_massive_fundamental_histories_v1(
        validated["fundamental_bundle_root"],
        str(validated["expected_fundamental_manifest_sha256"]),
    )
    if metadata.get("fundamental_canonical_manifest_sha256") != validated["expected_fundamental_canonical_manifest_sha256"]:
        raise ValueError("fundamental canonical manifest hash mismatch")
    sample = build_massive_sec_audit_sample_v1(histories, sample_size=SAMPLE_SIZE)
    identities = [
        {
            "cik": _normalize_cik(record.get("cik")),
            "accession": str(record["source_filing_identity"]),
            "source_record_sha256": str(record.get("canonical_record_sha256", "")),
        }
        for record in sample
    ]
    result: dict[str, object] = {
        "version": "massive_sec_filing_audit_plan_v1",
        "audit_id": validated["audit_id"],
        "request_sha256": _canonical_sha256(validated),
        "fundamental_manifest_sha256": metadata["fundamental_manifest_sha256"],
        "fundamental_canonical_manifest_sha256": metadata["fundamental_canonical_manifest_sha256"],
        "sample_size": SAMPLE_SIZE,
        "sample_identity_sha256": _canonical_sha256(identities),
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "provider_http_requests_used": 0,
        "sealed_oos_opened": False,
    }
    result["canonical_plan_sha256"] = _canonical_sha256(result)
    return result


def run_massive_sec_filing_audit_v1(request: dict[str, object]) -> dict[str, object]:
    """Run a bounded official-SEC value audit and publish an immutable local bundle."""

    try:
        validated = _validate_request(request)
    except (OSError, ValueError):
        return _failure_result("REQUEST_VALIDATION_FAILED")
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        return _failure_result("SEC_USER_AGENT_UNAVAILABLE")
    output = Path(str(validated["output_dir"]))
    if output.exists() or output.is_symlink():
        return _failure_result("OUTPUT_ALREADY_EXISTS")
    try:
        histories, metadata = load_massive_fundamental_histories_v1(
            validated["fundamental_bundle_root"],
            str(validated["expected_fundamental_manifest_sha256"]),
        )
        if metadata.get("fundamental_canonical_manifest_sha256") != validated["expected_fundamental_canonical_manifest_sha256"]:
            raise ValueError("fundamental canonical manifest hash mismatch")
        sample = build_massive_sec_audit_sample_v1(histories, sample_size=SAMPLE_SIZE)
    except (OSError, ValueError, TypeError):
        return _failure_result("FUNDAMENTAL_INPUT_INVALID")
    request_sha = _canonical_sha256(validated)
    staging = output.with_name(f".{validated['audit_id']}-{request_sha[:12]}.partial")
    if staging.exists() or staging.is_symlink():
        return _failure_result("STAGING_ALREADY_EXISTS")
    calls_used = 0
    try:
        staging.mkdir(parents=True)
        audit_records: list[dict[str, object]] = []
        for index, record in enumerate(sample):
            if index:
                time.sleep(MINIMUM_REQUEST_INTERVAL_SECONDS)
            cik = _normalize_cik(record.get("cik"))
            calls_used += 1
            body = _download_sec_companyfacts(
                cik,
                user_agent=user_agent,
                timeout_seconds=TIMEOUT_SECONDS,
                maximum_bytes=MAXIMUM_RESPONSE_BYTES,
            )
            try:
                companyfacts = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("SEC response is not valid JSON") from exc
            raw_relative = f"raw/sec-companyfacts/CIK{cik}.json.gz"
            raw_sha = _write_file(staging / raw_relative, gzip.compress(body, compresslevel=9, mtime=0))
            audited = audit_massive_record_against_sec_companyfacts_v1(
                record,
                companyfacts,
                minimum_matched_fields=MINIMUM_MATCHED_FIELDS,
            )
            audit_records.append({**audited, "raw_response_path": raw_relative, "raw_response_sha256": raw_sha})
        passed = sum(item["status"] == "PASS" for item in audit_records)
        status = "PASS" if passed == SAMPLE_SIZE else "FAIL"
        audit: dict[str, object] = {
            "version": AUDIT_VERSION,
            "status": status,
            "audit_id": validated["audit_id"],
            "request_sha256": request_sha,
            "fundamental_manifest_sha256": metadata["fundamental_manifest_sha256"],
            "fundamental_canonical_manifest_sha256": metadata["fundamental_canonical_manifest_sha256"],
            "sample_size": SAMPLE_SIZE,
            "minimum_matched_fields_per_record": MINIMUM_MATCHED_FIELDS,
            "passed_record_count": passed,
            "failed_record_count": SAMPLE_SIZE - passed,
            "records": audit_records,
            "provider_http_requests_used": calls_used,
            "sealed_oos_opened": False,
            "broker_calls_used": 0,
            "registry_write_performed": False,
            "deployment_performed": False,
            "provenance": {"source": PROVENANCE_SOURCE},
        }
        audit["canonical_audit_sha256"] = _canonical_sha256(audit)
        _write_json(staging / "request.json", validated)
        _write_json(staging / "audit.json", audit)
        result = {
            key: value
            for key, value in audit.items()
            if key not in {"records", "canonical_audit_sha256"}
        }
        result["canonical_audit_sha256"] = audit["canonical_audit_sha256"]
        result["output_dir"] = str(output)
        result["canonical_result_sha256"] = _canonical_sha256(result)
        _write_json(staging / "result.json", result)
        checksums = _build_checksums(staging)
        _write_json(staging / "checksums.json", checksums)
        _write_file(staging / "COMPLETE", (str(result["canonical_result_sha256"]) + "\n").encode("ascii"))
        staging.replace(output)
        return result
    except Exception:
        return _failure_result("AUDIT_RUNTIME_FAILED", provider_http_requests_used=calls_used)


def _download_sec_companyfacts(
    cik: str,
    *,
    user_agent: str,
    timeout_seconds: int,
    maximum_bytes: int,
) -> bytes:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_normalize_cik(cik)}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "identity"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        body = response.read(maximum_bytes + 1)
    if len(body) > maximum_bytes:
        raise ValueError("SEC response exceeds byte limit")
    return body


def _massive_values(record: dict[str, object]) -> dict[str, float]:
    statements = record.get("statements")
    if not isinstance(statements, dict):
        return {}
    values: dict[str, float] = {}
    for field, statement_name in _FIELD_LOCATIONS.items():
        statement = statements.get(statement_name)
        raw = statement.get(field) if isinstance(statement, dict) else None
        if not isinstance(raw, dict) or raw.get("unit") != "USD":
            continue
        value = _finite_number(raw.get("value"))
        if value is not None:
            values[field] = value
    return values


def _record_audit_result(
    record: dict[str, object],
    accession: str,
    matched: list[str],
    mismatched: list[str],
    missing: list[str],
    status: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "instrument_id": str(record.get("requested_instrument_id", "")),
        "ticker": str(record.get("requested_ticker", "")),
        "cik": _normalize_cik(record.get("cik")),
        "accession": accession,
        "filing_date": str(record.get("filing_date", "")),
        "period_end_date": str(record.get("period_end_date", "")),
        "source_record_sha256": str(record.get("canonical_record_sha256", "")),
        "matched_field_count": len(matched),
        "matched_fields": sorted(matched),
        "mismatched_fields": sorted(mismatched),
        "missing_sec_fields": sorted(missing),
    }
    result["canonical_record_audit_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(raw: Any) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS:
        raise ValueError("request fields are invalid")
    fixed = {
        "version": REQUEST_VERSION,
        "sample_size": SAMPLE_SIZE,
        "minimum_matched_fields_per_record": MINIMUM_MATCHED_FIELDS,
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "minimum_request_interval_seconds": MINIMUM_REQUEST_INTERVAL_SECONDS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_response_bytes": MAXIMUM_RESPONSE_BYTES,
    }
    if any(raw.get(key) != value for key, value in fixed.items()):
        raise ValueError("frozen request field mismatch")
    audit_id = raw.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id.strip():
        raise ValueError("audit id is invalid")
    root = Path(str(raw.get("fundamental_bundle_root", "")))
    output = Path(str(raw.get("output_dir", "")))
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or not output.is_absolute():
        raise ValueError("request paths are invalid")
    expected_file = _required_sha(raw.get("expected_fundamental_manifest_sha256"))
    expected_canonical = _required_sha(raw.get("expected_fundamental_canonical_manifest_sha256"))
    if raw.get("provenance") != {"source": PROVENANCE_SOURCE}:
        raise ValueError("provenance is invalid")
    return {
        **fixed,
        "audit_id": audit_id.strip(),
        "fundamental_bundle_root": str(root.resolve()),
        "expected_fundamental_manifest_sha256": expected_file,
        "expected_fundamental_canonical_manifest_sha256": expected_canonical,
        "output_dir": str(output.resolve()),
        "provenance": {"source": PROVENANCE_SOURCE},
    }


def _normalize_cik(raw: Any) -> str:
    text = str(raw)
    if not text.isdigit() or len(text) > 10:
        raise ValueError("CIK is invalid")
    return text.zfill(10)


def _finite_number(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _values_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


def _required_sha(raw: Any) -> str:
    if not isinstance(raw, str) or _SHA_RE.fullmatch(raw) is None:
        raise ValueError("SHA-256 is invalid")
    return raw


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value).rstrip(b"\n")).hexdigest()


def _write_json(path: Path, value: object) -> str:
    return _write_file(path, _canonical_bytes(value))


def _write_file(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    if temporary.read_bytes() != body:
        raise OSError("artifact write verification failed")
    temporary.replace(path)
    return hashlib.sha256(body).hexdigest()


def _build_checksums(staging: Path) -> dict[str, object]:
    records = [
        {"path": path.relative_to(staging).as_posix(), "sha256": _file_sha256(path)}
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name not in {"checksums.json", "COMPLETE"}
    ]
    result: dict[str, object] = {
        "version": "massive_sec_filing_audit_checksums_v1",
        "record_count": len(records),
        "records": records,
    }
    result["canonical_checksums_sha256"] = _canonical_sha256(result)
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _failure_result(status: str, *, provider_http_requests_used: int = 0) -> dict[str, object]:
    return {
        "version": "massive_sec_filing_audit_result_v1",
        "status": status,
        "provider_http_requests_used": provider_http_requests_used,
        "sealed_oos_opened": False,
    }
