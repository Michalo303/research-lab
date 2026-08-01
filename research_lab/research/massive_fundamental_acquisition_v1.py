from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


REQUEST_VERSION = "massive_fundamental_acquisition_request_v1"
PLAN_VERSION = "massive_fundamental_acquisition_plan_v1"
MANIFEST_VERSION = "massive_fundamental_dataset_manifest_v1"
ACQUISITION_ID = "MASSIVE-FUNDAMENTALS-2009-2022-V1"
PROVENANCE_SOURCE = "operator_approved_massive_fundamental_acquisition_v1"
APPROVED_HOST = "api.massive.com"
ENDPOINT_PATH = "/vX/reference/financials"
FILING_START = "2009-01-01"
FILING_END = "2022-12-31"
MAXIMUM_CALL_UNITS = 10_000
MINIMUM_REQUEST_INTERVAL_SECONDS = 12.5
MAXIMUM_PAGES_PER_TICKER = 3
MAXIMUM_ATTEMPTS_PER_REQUEST = 3
TIMEOUT_SECONDS = 30
MAXIMUM_RESPONSE_BYTES = 10_000_000
_REQUEST_FIELDS = {
    "version",
    "acquisition_id",
    "output_dir",
    "source_manifest_path",
    "expected_source_manifest_sha256",
    "filing_start",
    "filing_end",
    "maximum_call_units",
    "minimum_request_interval_seconds",
    "maximum_pages_per_ticker",
    "maximum_attempts_per_request",
    "timeout_seconds",
    "maximum_response_bytes",
    "provenance",
}
_INSTRUMENT_FIELDS = {
    "instrument_id",
    "symbol",
    "qlib_instrument",
    "instrument_type",
    "exchange_mic",
    "listing_start",
    "listing_end",
    "ohlcv_path",
    "ohlcv_sha256",
}
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RESOLVED_STATUSES = {
    "USABLE",
    "EMPTY_PROVIDER_RESULT",
    "NO_USABLE_RECORDS",
    "AMBIGUOUS_ISSUER_IDENTITY",
}
_SUPPORTED_STATEMENTS = {"income_statement", "balance_sheet", "cash_flow_statement"}
_CREDENTIAL_KEYS = {"apikey", "api_key", "api-token", "api_token", "token", "authorization"}


class RetryableProviderFailure(RuntimeError):
    """A bounded provider failure carrying no URL or secret text."""

    def __init__(self, http_status: int, *, retry_after_seconds: float = 0.0):
        super().__init__(f"retryable provider status {int(http_status)}")
        self.http_status = int(http_status)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))


class ProviderFailure(RuntimeError):
    """A non-retryable redacted provider failure."""


class FatalAcquisitionFailure(RuntimeError):
    """A global fail-closed state that must stop the acquisition immediately."""


@dataclass
class _RateLimiter:
    interval_seconds: float
    next_allowed: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self.next_allowed > now:
            time.sleep(self.next_allowed - now)
        self.next_allowed = max(now, self.next_allowed) + self.interval_seconds


def build_massive_fundamental_acquisition_plan_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Validate the source manifest and return a credential-free acquisition plan."""

    validated = _validate_request(request)
    manifest_bytes = Path(validated["source_manifest_path"]).read_bytes()
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha256 != validated["expected_source_manifest_sha256"]:
        raise ValueError("source manifest hash mismatch.")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source manifest is invalid.") from exc
    subjects = _manifest_subjects(manifest)
    subject_identity_sha256 = _canonical_sha256(subjects)
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "acquisition_id": ACQUISITION_ID,
        "request_sha256": _canonical_sha256(validated),
        "source_manifest_sha256": actual_manifest_sha256,
        "source_dataset_id": str(manifest["dataset_id"]),
        "provider": "MASSIVE",
        "approved_host": APPROVED_HOST,
        "endpoint_path": ENDPOINT_PATH,
        "filing_interval": {"start": FILING_START, "end": FILING_END},
        "supported_timeframes": ["annual", "quarterly"],
        "subject_count": len(subjects),
        "subject_identity_sha256": subject_identity_sha256,
        "subjects": subjects,
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "minimum_request_interval_seconds": MINIMUM_REQUEST_INTERVAL_SECONDS,
        "maximum_pages_per_ticker": MAXIMUM_PAGES_PER_TICKER,
        "maximum_attempts_per_request": MAXIMUM_ATTEMPTS_PER_REQUEST,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_response_bytes": MAXIMUM_RESPONSE_BYTES,
        "provider_calls_used": 0,
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
        "provenance": {"source": PROVENANCE_SOURCE},
    }
    result["canonical_plan_sha256"] = _canonical_sha256(result)
    return result


def run_massive_fundamental_acquisition_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Execute or resume the exact provider acquisition and publish one immutable bundle."""

    try:
        plan = build_massive_fundamental_acquisition_plan_v1(request)
    except (OSError, ValueError):
        return _failure_result("REQUEST_VALIDATION_FAILED")
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if not api_key:
        return _failure_result("MASSIVE_API_KEY_UNAVAILABLE")
    output = Path(str(request["output_dir"]))
    if output.exists() or output.is_symlink():
        return _failure_result("OUTPUT_ALREADY_EXISTS")
    staging = output.with_name(f".{ACQUISITION_ID}-{str(plan['request_sha256'])[:12]}.partial")
    try:
        staging.mkdir(parents=True, exist_ok=True)
        connection = _open_state(staging / "state.sqlite", str(plan["request_sha256"]))
    except (OSError, sqlite3.Error, ValueError):
        return _failure_result("STAGING_IO_FAILED")

    limiter = _RateLimiter(float(plan["minimum_request_interval_seconds"]))
    try:
        for ordinal, subject in enumerate(plan["subjects"], start=1):
            existing = connection.execute(
                "SELECT status FROM subjects WHERE instrument_id = ?",
                (subject["instrument_id"],),
            ).fetchone()
            if existing and existing[0] in _RESOLVED_STATUSES:
                continue
            try:
                _acquire_subject(
                    connection=connection,
                    staging=staging,
                    plan=plan,
                    subject=subject,
                    ordinal=ordinal,
                    api_key=api_key,
                    limiter=limiter,
                )
            except (OSError, sqlite3.Error):
                _record_failure(connection, subject, ordinal, "LOCAL_IO_FAILURE")
            _write_progress(staging / "progress.json", connection, len(plan["subjects"]))
        summary = _state_summary(connection, len(plan["subjects"]))
        if summary["failed_ticker_count"]:
            connection.commit()
            connection.close()
            return {
                "version": "massive_fundamental_acquisition_result_v1",
                "status": "PROVIDER_ACQUISITION_INCOMPLETE",
                "staging_dir": str(staging),
                **summary,
                "sealed_oos_opened": False,
            }
        manifest = _build_fundamental_manifest(connection, plan)
        coverage = _build_coverage_report(manifest)
        connection.commit()
        connection.close()
        return _publish_bundle(
            request=request,
            plan=plan,
            manifest=manifest,
            coverage=coverage,
            staging=staging,
            output=output,
            summary=summary,
        )
    except FatalAcquisitionFailure as exc:
        try:
            summary = _state_summary(connection, len(plan["subjects"]))
            connection.commit()
            connection.close()
        except sqlite3.Error:
            summary = {}
        return {
            "version": "massive_fundamental_acquisition_result_v1",
            "status": str(exc),
            "staging_dir": str(staging),
            **summary,
            "sealed_oos_opened": False,
        }
    except Exception:
        try:
            summary = _state_summary(connection, len(plan["subjects"]))
            connection.commit()
            connection.close()
        except sqlite3.Error:
            summary = {
                "provider_call_units_used": None,
                "provider_http_requests_used": None,
            }
        return {
            "version": "massive_fundamental_acquisition_result_v1",
            "status": "ACQUISITION_RUNTIME_FAILED",
            "staging_dir": str(staging),
            **summary,
            "sealed_oos_opened": False,
        }


def verify_massive_fundamental_bundle_v1(root: str | Path) -> dict[str, object]:
    """Verify a completed bundle without provider or credential access."""

    path = Path(root)
    try:
        if not path.is_dir() or not (path / "COMPLETE").is_file():
            raise ValueError("bundle is incomplete")
        checksums = json.loads((path / "checksums.json").read_text(encoding="utf-8"))
        if checksums.get("version") != "massive_fundamental_bundle_checksums_v1":
            raise ValueError("checksum manifest version mismatch")
        declared_checksums = checksums.get("canonical_checksums_sha256")
        if declared_checksums != _canonical_sha256(
            {key: value for key, value in checksums.items() if key != "canonical_checksums_sha256"}
        ):
            raise ValueError("checksum manifest canonical hash mismatch")
        records = checksums.get("records")
        if not isinstance(records, list) or checksums.get("record_count") != len(records):
            raise ValueError("checksum records missing")
        declared: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
                raise ValueError("checksum record invalid")
            relative = str(record["path"])
            if relative in declared or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("checksum path invalid")
            declared.add(relative)
            candidate = path / relative
            if not candidate.is_file() or _file_sha256(candidate) != record["sha256"]:
                raise ValueError("checksum mismatch")
        actual = {
            item.relative_to(path).as_posix()
            for item in path.rglob("*")
            if item.is_file() and item.name not in {"checksums.json", "COMPLETE"}
        }
        if actual != declared:
            raise ValueError("bundle file set mismatch")
        result = json.loads((path / "result.json").read_text(encoding="utf-8"))
        if result.get("status") != "COMPLETE":
            raise ValueError("result status mismatch")
        if result.get("canonical_result_sha256") != _canonical_sha256(
            {key: value for key, value in result.items() if key != "canonical_result_sha256"}
        ):
            raise ValueError("result canonical hash mismatch")
        marker = (path / "COMPLETE").read_text(encoding="ascii").strip()
        if marker != result.get("canonical_result_sha256"):
            raise ValueError("complete marker mismatch")
        return {
            "version": "massive_fundamental_bundle_verification_v1",
            "status": "PASS",
            "verified_file_count": len(records),
            "canonical_result_sha256": marker,
            "provider_calls_used": 0,
            "sealed_oos_opened": False,
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return {
            "version": "massive_fundamental_bundle_verification_v1",
            "status": "FAIL",
            "provider_calls_used": 0,
            "sealed_oos_opened": False,
        }


def _validate_request(raw: Any) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS:
        raise ValueError("request fields are invalid.")
    fixed = {
        "version": REQUEST_VERSION,
        "acquisition_id": ACQUISITION_ID,
        "filing_start": FILING_START,
        "filing_end": FILING_END,
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "minimum_request_interval_seconds": MINIMUM_REQUEST_INTERVAL_SECONDS,
        "maximum_pages_per_ticker": MAXIMUM_PAGES_PER_TICKER,
        "maximum_attempts_per_request": MAXIMUM_ATTEMPTS_PER_REQUEST,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_response_bytes": MAXIMUM_RESPONSE_BYTES,
    }
    for field, expected in fixed.items():
        if raw.get(field) != expected:
            raise ValueError(f"{field} is invalid.")
    output = Path(_nonempty_text(raw.get("output_dir"), "output_dir"))
    source = Path(_nonempty_text(raw.get("source_manifest_path"), "source_manifest_path"))
    if not output.is_absolute() or not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise ValueError("request paths are invalid.")
    expected_sha = _required_sha(raw.get("expected_source_manifest_sha256"), "expected_source_manifest_sha256")
    provenance = raw.get("provenance")
    if provenance != {"source": PROVENANCE_SOURCE}:
        raise ValueError("provenance is invalid.")
    return {
        **fixed,
        "output_dir": str(output),
        "source_manifest_path": str(source),
        "expected_source_manifest_sha256": expected_sha,
        "provenance": {"source": PROVENANCE_SOURCE},
    }


def _manifest_subjects(raw: Any) -> list[dict[str, object]]:
    if not isinstance(raw, dict) or raw.get("version") != "eodhd_qlib_dataset_manifest_v1":
        raise ValueError("source manifest version is invalid.")
    if not isinstance(raw.get("dataset_id"), str) or not raw["dataset_id"]:
        raise ValueError("source dataset identity is invalid.")
    instruments = raw.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("source instruments are invalid.")
    subjects: list[dict[str, object]] = []
    for item in instruments:
        if not isinstance(item, dict) or set(item) != _INSTRUMENT_FIELDS:
            raise ValueError("source instrument fields are invalid.")
        instrument_id = _nonempty_text(item.get("instrument_id"), "instrument_id")
        ticker = _nonempty_text(item.get("qlib_instrument"), "qlib_instrument").upper()
        if not _TICKER_RE.fullmatch(ticker):
            raise ValueError("lookup ticker is invalid.")
        if item.get("instrument_type") != "COMMON_STOCK":
            raise ValueError("source instrument type is invalid.")
        listing_start = _date_text(item.get("listing_start"), "listing_start")
        listing_end_raw = item.get("listing_end")
        listing_end = None if listing_end_raw is None else _date_text(listing_end_raw, "listing_end")
        if listing_end is not None and listing_end < listing_start:
            raise ValueError("listing interval is invalid.")
        _required_sha(item.get("ohlcv_sha256"), "ohlcv_sha256")
        subjects.append(
            {
                "instrument_id": instrument_id,
                "lookup_ticker": ticker,
                "listing_start": listing_start,
                "listing_end": listing_end,
            }
        )
    subjects.sort(key=lambda item: str(item["instrument_id"]))
    identities = [str(item["instrument_id"]) for item in subjects]
    tickers = [str(item["lookup_ticker"]) for item in subjects]
    if len(identities) != len(set(identities)) or len(tickers) != len(set(tickers)):
        raise ValueError("source subject identity is duplicated.")
    return subjects


def _open_state(path: Path, request_sha256: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS subjects (
            instrument_id TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            lookup_ticker TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            page_count INTEGER NOT NULL,
            raw_paths_json TEXT NOT NULL,
            raw_sha256_json TEXT NOT NULL,
            normalized_path TEXT,
            normalized_sha256 TEXT,
            usable_record_count INTEGER NOT NULL,
            rejected_record_count INTEGER NOT NULL,
            cik TEXT,
            failure_class TEXT
        )
        """
    )
    existing = connection.execute("SELECT value FROM meta WHERE key='request_sha256'").fetchone()
    if existing and existing[0] != request_sha256:
        connection.close()
        raise ValueError("staging request identity mismatch.")
    connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('request_sha256',?)", (request_sha256,))
    connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('provider_http_requests','0')")
    connection.commit()
    return connection


def _acquire_subject(
    *,
    connection: sqlite3.Connection,
    staging: Path,
    plan: dict[str, object],
    subject: dict[str, object],
    ordinal: int,
    api_key: str,
    limiter: _RateLimiter,
) -> None:
    ticker = str(subject["lookup_ticker"])
    endpoint_identity = _initial_endpoint_identity(ticker)
    page_bodies: list[bytes] = []
    page_payloads: list[dict[str, Any]] = []
    next_identity: str | None = endpoint_identity
    attempts_used = 0
    failure_class: str | None = None
    try:
        for _page_number in range(1, int(plan["maximum_pages_per_ticker"]) + 1):
            if next_identity is None:
                break
            response: tuple[bytes, dict[str, str]] | None = None
            for attempt in range(int(plan["maximum_attempts_per_request"])):
                _reserve_provider_call(connection, int(plan["maximum_call_units"]))
                attempts_used += 1
                limiter.wait()
                try:
                    response = _download_http_response(
                        _authorized_url(next_identity, api_key),
                        timeout_seconds=int(plan["timeout_seconds"]),
                        maximum_bytes=int(plan["maximum_response_bytes"]),
                    )
                    break
                except RetryableProviderFailure as exc:
                    failure_class = f"HTTP_{exc.http_status}"
                    if attempt + 1 >= int(plan["maximum_attempts_per_request"]):
                        raise
                    time.sleep(min(60.0, max(exc.retry_after_seconds, float(2**attempt))))
            if response is None:
                raise ProviderFailure("provider response unavailable")
            body, _headers = response
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderFailure("provider payload invalid") from exc
            if not isinstance(payload, dict) or payload.get("status") not in {None, "OK", "DELAYED"}:
                raise ProviderFailure("provider payload status invalid")
            results = payload.get("results", [])
            if not isinstance(results, list):
                raise ProviderFailure("provider results invalid")
            page_bodies.append(body)
            page_payloads.append(payload)
            raw_next = payload.get("next_url")
            next_identity = None if not raw_next else _sanitize_next_identity(str(raw_next))
        if next_identity is not None:
            raise ProviderFailure("maximum pages exceeded")
        normalized = _normalize_subject_records(subject, page_payloads)
        raw_paths: list[str] = []
        raw_hashes: list[str] = []
        shard = hashlib.sha256(str(subject["instrument_id"]).encode()).hexdigest()
        for page_number, body in enumerate(page_bodies, start=1):
            relative = f"raw/{shard[:2]}/{shard}/page-{page_number:03d}.json.gz"
            raw_paths.append(relative)
            raw_hashes.append(_write_gzip(staging / relative, body))
        normalized_path: str | None = None
        normalized_sha: str | None = None
        if normalized["status"] == "USABLE":
            normalized_path = f"normalized/{shard[:2]}/{shard}.json.gz"
            body = _canonical_json_bytes(normalized["artifact"])
            normalized_sha = _write_gzip(staging / normalized_path, body)
        connection.execute(
            """
            INSERT OR REPLACE INTO subjects(
                instrument_id,ordinal,lookup_ticker,status,attempts,page_count,
                raw_paths_json,raw_sha256_json,normalized_path,normalized_sha256,
                usable_record_count,rejected_record_count,cik,failure_class
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
            """,
            (
                subject["instrument_id"], ordinal, ticker, normalized["status"], attempts_used,
                len(page_bodies), json.dumps(raw_paths), json.dumps(raw_hashes), normalized_path,
                normalized_sha, normalized["usable_record_count"], normalized["rejected_record_count"],
                normalized["cik"],
            ),
        )
        connection.commit()
    except (RetryableProviderFailure, ProviderFailure, ValueError) as exc:
        if isinstance(exc, RetryableProviderFailure):
            failure_class = f"HTTP_{exc.http_status}"
        elif failure_class is None:
            failure_class = type(exc).__name__.upper()
        _record_failure(connection, subject, ordinal, str(failure_class), attempts_used=attempts_used)


def _normalize_subject_records(
    subject: dict[str, object],
    pages: list[dict[str, Any]],
) -> dict[str, object]:
    raw_records = [record for page in pages for record in page.get("results", [])]
    if not raw_records:
        return {"status": "EMPTY_PROVIDER_RESULT", "usable_record_count": 0, "rejected_record_count": 0, "cik": None, "artifact": None}
    normalized: list[dict[str, object]] = []
    rejected = 0
    ciks: set[str] = set()
    for raw in raw_records:
        record = _normalize_record(raw, subject)
        if record is None:
            rejected += 1
            continue
        ciks.add(str(record["cik"]))
        normalized.append(record)
    if not normalized:
        return {"status": "NO_USABLE_RECORDS", "usable_record_count": 0, "rejected_record_count": rejected, "cik": None, "artifact": None}
    if len(ciks) != 1:
        return {"status": "AMBIGUOUS_ISSUER_IDENTITY", "usable_record_count": 0, "rejected_record_count": rejected + len(normalized), "cik": None, "artifact": None}
    unique: dict[str, dict[str, object]] = {}
    for record in normalized:
        record_hash = _canonical_sha256(record)
        unique[record_hash] = record
    ordered = [unique[key] for key in sorted(unique, key=lambda key: (str(unique[key]["filing_date"]), str(unique[key]["period_end_date"]), str(unique[key]["timeframe"]), key))]
    cik = next(iter(ciks))
    artifact: dict[str, object] = {
        "version": "massive_normalized_fundamental_history_v1",
        "instrument_id": subject["instrument_id"],
        "lookup_ticker": subject["lookup_ticker"],
        "cik": cik,
        "listing_start": subject["listing_start"],
        "listing_end": subject["listing_end"],
        "records": ordered,
    }
    artifact["canonical_history_sha256"] = _canonical_sha256(artifact)
    return {
        "status": "USABLE",
        "usable_record_count": len(ordered),
        "rejected_record_count": rejected + len(normalized) - len(ordered),
        "cik": cik,
        "artifact": artifact,
    }


def _normalize_record(raw: Any, subject: dict[str, object]) -> dict[str, object] | None:
    if not isinstance(raw, dict) or raw.get("timeframe") not in {"annual", "quarterly"}:
        return None
    filing_date = _optional_date(raw.get("filing_date"))
    period_end = _optional_date(
        raw.get("end_date") or raw.get("period_end_date") or raw.get("period_end") or raw.get("period_of_report_date")
    )
    if filing_date is None or period_end is None or not (FILING_START <= filing_date <= FILING_END) or period_end > filing_date:
        return None
    cik = str(raw.get("cik") or "").strip()
    if not cik.isdigit() or len(cik) > 10:
        return None
    cik = cik.zfill(10)
    financials = raw.get("financials")
    if not isinstance(financials, dict):
        return None
    statements: dict[str, dict[str, dict[str, object]]] = {}
    for statement_name in sorted(_SUPPORTED_STATEMENTS):
        statement = financials.get(statement_name)
        if not isinstance(statement, dict):
            continue
        fields: dict[str, dict[str, object]] = {}
        for field_name, field_payload in sorted(statement.items()):
            value, unit = _numeric_field(field_payload)
            if value is not None:
                fields[str(field_name)] = {"value": value, "unit": unit}
        if fields:
            statements[statement_name] = fields
    if not statements:
        return None
    tickers = raw.get("tickers")
    normalized_tickers = sorted({str(value).upper() for value in tickers if value}) if isinstance(tickers, list) else []
    result: dict[str, object] = {
        "cik": cik,
        "requested_instrument_id": subject["instrument_id"],
        "requested_ticker": subject["lookup_ticker"],
        "reported_tickers": normalized_tickers,
        "filing_date": filing_date,
        "period_end_date": period_end,
        "timeframe": str(raw["timeframe"]),
        "fiscal_year": _optional_int(raw.get("fiscal_year")),
        "fiscal_period": str(raw.get("fiscal_period") or ""),
        "source_filing_identity": _source_filing_identity(raw.get("source_filing_url") or raw.get("source_filing_file_url")),
        "statements": statements,
    }
    result["canonical_record_sha256"] = _canonical_sha256(result)
    return result


def _initial_endpoint_identity(ticker: str) -> str:
    query = urllib.parse.urlencode(
        {
            "filing_date.gte": FILING_START,
            "filing_date.lte": FILING_END,
            "limit": "100",
            "order": "asc",
            "sort": "filing_date",
            "ticker": ticker,
        }
    )
    return f"https://{APPROVED_HOST}{ENDPOINT_PATH}?{query}"


def _sanitize_next_identity(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST or parsed.path != ENDPOINT_PATH:
        raise ProviderFailure("next page authority invalid")
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if key.casefold() not in _CREDENTIAL_KEYS]
    if not query:
        raise ProviderFailure("next page query invalid")
    return urllib.parse.urlunparse(("https", APPROVED_HOST, ENDPOINT_PATH, "", urllib.parse.urlencode(sorted(query)), ""))


def _authorized_url(endpoint_identity: str, api_key: str) -> str:
    separator = "&" if "?" in endpoint_identity else "?"
    return f"{endpoint_identity}{separator}{urllib.parse.urlencode({'apiKey': api_key})}"


def _download_http_response(
    url: str,
    *,
    timeout_seconds: int,
    maximum_bytes: int,
) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > maximum_bytes:
                raise ProviderFailure("provider response too large")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise ProviderFailure("provider response too large")
            return body, {str(key): str(value) for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise RetryableProviderFailure(exc.code, retry_after_seconds=retry_after) from None
        if exc.code in {401, 403}:
            raise FatalAcquisitionFailure("MASSIVE_ENTITLEMENT_UNAVAILABLE") from None
        raise ProviderFailure(f"provider status {int(exc.code)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RetryableProviderFailure(503, retry_after_seconds=0.0) from exc


def _reserve_provider_call(connection: sqlite3.Connection, maximum: int) -> None:
    current = int(connection.execute("SELECT value FROM meta WHERE key='provider_http_requests'").fetchone()[0])
    if current >= maximum:
        raise FatalAcquisitionFailure("PROVIDER_CALL_BUDGET_EXHAUSTED")
    connection.execute("UPDATE meta SET value=? WHERE key='provider_http_requests'", (str(current + 1),))
    connection.commit()


def _record_failure(
    connection: sqlite3.Connection,
    subject: dict[str, object],
    ordinal: int,
    failure_class: str,
    *,
    attempts_used: int = 0,
) -> None:
    previous = connection.execute("SELECT attempts FROM subjects WHERE instrument_id=?", (subject["instrument_id"],)).fetchone()
    attempts = (int(previous[0]) if previous else 0) + attempts_used
    connection.execute(
        """
        INSERT OR REPLACE INTO subjects(
            instrument_id,ordinal,lookup_ticker,status,attempts,page_count,raw_paths_json,
            raw_sha256_json,normalized_path,normalized_sha256,usable_record_count,
            rejected_record_count,cik,failure_class
        ) VALUES(?,?,?,'FAILED',?,0,'[]','[]',NULL,NULL,0,0,NULL,?)
        """,
        (subject["instrument_id"], ordinal, subject["lookup_ticker"], attempts, failure_class[:80]),
    )
    connection.commit()


def _state_summary(connection: sqlite3.Connection, total: int) -> dict[str, int]:
    rows = dict(connection.execute("SELECT status, COUNT(*) FROM subjects GROUP BY status").fetchall())
    calls = int(connection.execute("SELECT value FROM meta WHERE key='provider_http_requests'").fetchone()[0])
    usable_records = int(connection.execute("SELECT COALESCE(SUM(usable_record_count),0) FROM subjects WHERE status='USABLE'").fetchone()[0])
    completed = sum(int(rows.get(status, 0)) for status in _RESOLVED_STATUSES)
    return {
        "total_ticker_count": total,
        "resolved_ticker_count": completed,
        "usable_ticker_count": int(rows.get("USABLE", 0)),
        "usable_record_count": usable_records,
        "empty_ticker_count": int(rows.get("EMPTY_PROVIDER_RESULT", 0)) + int(rows.get("NO_USABLE_RECORDS", 0)),
        "ambiguous_ticker_count": int(rows.get("AMBIGUOUS_ISSUER_IDENTITY", 0)),
        "failed_ticker_count": int(rows.get("FAILED", 0)) + max(0, total - completed - int(rows.get("FAILED", 0))),
        "provider_call_units_used": calls,
        "provider_http_requests_used": calls,
    }


def _build_fundamental_manifest(connection: sqlite3.Connection, plan: dict[str, object]) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT instrument_id,ordinal,lookup_ticker,status,attempts,page_count,raw_paths_json,
               raw_sha256_json,normalized_path,normalized_sha256,usable_record_count,
               rejected_record_count,cik,failure_class
        FROM subjects ORDER BY ordinal
        """
    ).fetchall()
    records = [
        {
            "instrument_id": row[0],
            "ordinal": row[1],
            "lookup_ticker": row[2],
            "status": row[3],
            "attempts": row[4],
            "page_count": row[5],
            "raw_paths": json.loads(row[6]),
            "raw_sha256": json.loads(row[7]),
            "normalized_path": row[8],
            "normalized_sha256": row[9],
            "usable_record_count": row[10],
            "rejected_record_count": row[11],
            "cik": row[12],
            "failure_class": row[13],
        }
        for row in rows
    ]
    result: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "acquisition_id": ACQUISITION_ID,
        "request_sha256": plan["request_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "subject_identity_sha256": plan["subject_identity_sha256"],
        "filing_interval": plan["filing_interval"],
        "records": records,
    }
    result["canonical_manifest_sha256"] = _canonical_sha256(result)
    return result


def _build_coverage_report(manifest: dict[str, object]) -> dict[str, object]:
    records = manifest["records"]
    statuses: dict[str, int] = {}
    for record in records:
        statuses[str(record["status"])] = statuses.get(str(record["status"]), 0) + 1
    result: dict[str, object] = {
        "version": "massive_fundamental_coverage_report_v1",
        "subject_count": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "usable_ticker_count": statuses.get("USABLE", 0),
        "usable_record_count": sum(int(record["usable_record_count"]) for record in records),
        "ambiguous_tickers": sorted(record["lookup_ticker"] for record in records if record["status"] == "AMBIGUOUS_ISSUER_IDENTITY"),
        "empty_tickers": sorted(record["lookup_ticker"] for record in records if record["status"] in {"EMPTY_PROVIDER_RESULT", "NO_USABLE_RECORDS"}),
        "failed_tickers": sorted(record["lookup_ticker"] for record in records if record["status"] == "FAILED"),
        "sealed_oos_opened": False,
    }
    result["canonical_coverage_sha256"] = _canonical_sha256(result)
    return result


def _publish_bundle(
    *,
    request: dict[str, object],
    plan: dict[str, object],
    manifest: dict[str, object],
    coverage: dict[str, object],
    staging: Path,
    output: Path,
    summary: dict[str, int],
) -> dict[str, object]:
    request_body = _canonical_json_bytes(request)
    _write_file(staging / "request.json", request_body)
    _write_json(staging / "acquisition_plan.json", plan)
    _write_json(staging / "fundamental_manifest.json", manifest)
    _write_json(staging / "coverage_report.json", coverage)
    result: dict[str, object] = {
        "version": "massive_fundamental_acquisition_result_v1",
        "status": "COMPLETE",
        "acquisition_id": ACQUISITION_ID,
        "request_sha256": plan["request_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "fundamental_manifest_sha256": manifest["canonical_manifest_sha256"],
        **summary,
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
        "output_dir": str(output),
    }
    result["canonical_result_sha256"] = _canonical_sha256(result)
    _write_json(staging / "result.json", result)
    records = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and path.name not in {"checksums.json", "COMPLETE"}:
            records.append({"path": path.relative_to(staging).as_posix(), "sha256": _file_sha256(path)})
    checksums: dict[str, object] = {
        "version": "massive_fundamental_bundle_checksums_v1",
        "record_count": len(records),
        "records": records,
    }
    checksums["canonical_checksums_sha256"] = _canonical_sha256(checksums)
    _write_json(staging / "checksums.json", checksums)
    _write_file(staging / "COMPLETE", (str(result["canonical_result_sha256"]) + "\n").encode("ascii"))
    staging.replace(output)
    return result


def _write_progress(path: Path, connection: sqlite3.Connection, total: int) -> None:
    payload = {
        "version": "massive_fundamental_acquisition_progress_v1",
        **_state_summary(connection, total),
    }
    payload["canonical_progress_sha256"] = _canonical_sha256(payload)
    _write_json(path, payload)


def _write_gzip(path: Path, body: bytes) -> str:
    return _write_file(path, gzip.compress(body, compresslevel=9, mtime=0))


def _write_json(path: Path, payload: object) -> str:
    return _write_file(path, _canonical_json_bytes(payload))


def _write_file(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    if temporary.read_bytes() != body:
        raise OSError("artifact write verification failed")
    temporary.replace(path)
    return hashlib.sha256(body).hexdigest()


def _numeric_field(raw: Any) -> tuple[float | None, str]:
    if isinstance(raw, dict):
        value = raw.get("value")
        unit = str(raw.get("unit") or raw.get("currency") or "")
    else:
        value = raw
        unit = ""
    if isinstance(value, bool) or value in {None, ""}:
        return None, unit
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, unit
    return (numeric, unit) if math.isfinite(numeric) else (None, unit)


def _source_filing_identity(raw: Any) -> str:
    if not raw:
        return ""
    text = str(raw)
    accession = re.search(r"(?<!\d)(\d{10}-\d{2}-\d{6})(?!\d)", text)
    if accession:
        return f"SEC_ACCESSION:{accession.group(1)}"
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "https" or parsed.hostname not in {"sec.gov", "www.sec.gov"} or parsed.username or parsed.password:
        return ""
    return urllib.parse.urlunparse(("https", "www.sec.gov", parsed.path, "", "", ""))


def _retry_after_seconds(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value >= 0.0 else 0.0


def _optional_int(raw: Any) -> int | None:
    if raw in {None, ""} or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _date_text(raw: Any, name: str) -> str:
    value = _optional_date(raw)
    if value is None or value != raw:
        raise ValueError(f"{name} is invalid.")
    return value


def _optional_date(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == raw else None


def _nonempty_text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be nonempty text.")
    return raw.strip()


def _required_sha(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not _SHA_RE.fullmatch(raw):
        raise ValueError(f"{name} must be lowercase SHA-256.")
    return raw


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _failure_result(status: str) -> dict[str, object]:
    return {
        "version": "massive_fundamental_acquisition_result_v1",
        "status": status,
        "provider_call_units_used": 0,
        "provider_http_requests_used": 0,
        "sealed_oos_opened": False,
    }
