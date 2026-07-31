from __future__ import annotations

import hashlib
import csv
import gzip
import io
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


REQUEST_VERSION = "eodhd_us_equity_universe_acquisition_request_v1"
PLAN_VERSION = "eodhd_us_equity_universe_acquisition_plan_v1"
ACQUISITION_ID = "EODHD-US-EQUITY-2006-2022-V1"
PROVENANCE_SOURCE = "operator_approved_eodhd_acquisition_v1"
START_DATE = "2006-01-01"
END_DATE = "2022-12-31"
MAXIMUM_CALL_UNITS = 90_000
MAXIMUM_ATTEMPTS_PER_REQUEST = 2
HISTORY_CONCURRENCY = 8
TIMEOUT_SECONDS = 90
MAXIMUM_SYMBOL_RESPONSE_BYTES = 2_000_000
MAXIMUM_BULK_RESPONSE_BYTES = 20_000_000
MINIMUM_FREE_DISK_BYTES = 8_000_000_000
BULK_EXCHANGES = ("AMEX", "NASDAQ", "NYSE")
SUPPORTED_EXCHANGE_MICS = {
    "AMEX": "XASE",
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "NYSE MKT": "XASE",
}
_REQUEST_FIELDS = {
    "version",
    "acquisition_id",
    "output_dir",
    "provider",
    "approved_host",
    "start_date",
    "end_date",
    "maximum_call_units",
    "maximum_attempts_per_request",
    "history_concurrency",
    "timeout_seconds",
    "maximum_symbol_response_bytes",
    "maximum_bulk_response_bytes",
    "provenance",
}
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


class RetryableProviderFailure(RuntimeError):
    """A redacted provider failure that may consume one bounded retry."""


class _AcquisitionFailure(RuntimeError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class _RateLimiter:
    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0.0 or burst <= 0:
            raise ValueError("rate limiter settings must be positive.")
        self._rate = float(rate_per_second)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._monotonic = monotonic
        self._sleep = sleep
        self._last = monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._monotonic()
                elapsed = max(0.0, now - self._last)
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_seconds = (1.0 - self._tokens) / self._rate
            self._sleep(wait_seconds)


def build_eodhd_us_equity_acquisition_plan_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Validate the frozen request and return a credential-free plan."""

    validated = _validate_request(request)
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "acquisition_id": ACQUISITION_ID,
        "request_sha256": _canonical_sha256(validated),
        "output_dir": validated["output_dir"],
        "provider": "EODHD",
        "approved_host": "eodhd.com",
        "interval": {"start": START_DATE, "end": END_DATE},
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "maximum_attempts_per_request": MAXIMUM_ATTEMPTS_PER_REQUEST,
        "history_concurrency": HISTORY_CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_symbol_response_bytes": MAXIMUM_SYMBOL_RESPONSE_BYTES,
        "maximum_bulk_response_bytes": MAXIMUM_BULK_RESPONSE_BYTES,
        "minimum_free_disk_bytes": MINIMUM_FREE_DISK_BYTES,
        "bulk_exchanges": list(BULK_EXCHANGES),
        "supported_exchange_mics": dict(sorted(SUPPORTED_EXCHANGE_MICS.items())),
        "universe": {
            "instrument_type": "COMMON_STOCK",
            "currency": "USD",
            "minimum_price": 5.0,
            "minimum_history_sessions": 252,
            "minimum_median_dollar_volume": 10_000_000.0,
            "maximum_instruments": 1_500,
        },
        "initial_requests": [
            {
                "kind": "ACTIVE_COMMON_STOCKS",
                "call_units": 1,
                "endpoint_identity": _endpoint_identity(
                    "/api/exchange-symbol-list/US",
                    {"fmt": "json", "type": "common_stock"},
                ),
            },
            {
                "kind": "DELISTED_COMMON_STOCKS",
                "call_units": 1,
                "endpoint_identity": _endpoint_identity(
                    "/api/exchange-symbol-list/US",
                    {"delisted": "1", "fmt": "json", "type": "common_stock"},
                ),
            },
            {
                "kind": "SPY_SESSION_PROXY",
                "call_units": 1,
                "endpoint_identity": _endpoint_identity(
                    "/api/eod/SPY.US",
                    {
                        "fmt": "json",
                        "from": START_DATE,
                        "period": "d",
                        "to": END_DATE,
                    },
                ),
            },
        ],
        "provider_calls_used": 0,
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
        "provenance": {"source": PROVENANCE_SOURCE},
    }
    result["canonical_plan_sha256"] = _canonical_sha256(result)
    return result


def run_eodhd_us_equity_universe_acquisition_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Execute or resume the exact local acquisition and stop before selection."""

    try:
        plan = build_eodhd_us_equity_acquisition_plan_v1(request)
    except ValueError:
        return _failure_result("REQUEST_VALIDATION_FAILED", call_units=0, http_requests=0)
    api_key = os.getenv("EODHD_API_KEY", "").strip()
    if not api_key:
        return _failure_result("EODHD_API_KEY_UNAVAILABLE", call_units=0, http_requests=0)
    output_dir = Path(str(plan["output_dir"]))
    if output_dir.exists() or output_dir.is_symlink():
        return _failure_result("OUTPUT_ALREADY_EXISTS", call_units=0, http_requests=0)
    staging = output_dir.with_name(
        f".{ACQUISITION_ID}-{str(plan['request_sha256'])[:12]}.partial"
    )
    try:
        staging.mkdir(parents=True, exist_ok=True)
        connection = _open_state(staging / "state.sqlite", str(plan["request_sha256"]))
    except _AcquisitionFailure as exc:
        return _failure_result(exc.status, call_units=0, http_requests=0)
    except (OSError, sqlite3.Error):
        return _failure_result("STAGING_IO_FAILED", call_units=0, http_requests=0)
    try:
        if not _verify_staged_artifacts(staging, connection):
            return _state_result("STAGING_HASH_MISMATCH", staging, connection)
        initial = {str(item["kind"]): item for item in plan["initial_requests"]}
        active_raw = _obtain_response(
            connection=connection,
            staging=staging,
            api_key=api_key,
            ordinal=1,
            kind="ACTIVE_COMMON_STOCKS",
            endpoint_identity=str(initial["ACTIVE_COMMON_STOCKS"]["endpoint_identity"]),
            call_units=1,
            relative_raw_path="raw/universe/active.json.gz",
            max_response_bytes=_response_size_cap("ACTIVE_COMMON_STOCKS"),
            validator=_validate_identity_response,
        )
        delisted_raw = _obtain_response(
            connection=connection,
            staging=staging,
            api_key=api_key,
            ordinal=2,
            kind="DELISTED_COMMON_STOCKS",
            endpoint_identity=str(initial["DELISTED_COMMON_STOCKS"]["endpoint_identity"]),
            call_units=1,
            relative_raw_path="raw/universe/delisted.json.gz",
            max_response_bytes=_response_size_cap("DELISTED_COMMON_STOCKS"),
            validator=_validate_identity_response,
        )
        spy_raw = _obtain_response(
            connection=connection,
            staging=staging,
            api_key=api_key,
            ordinal=3,
            kind="SPY_SESSION_PROXY",
            endpoint_identity=str(initial["SPY_SESSION_PROXY"]["endpoint_identity"]),
            call_units=1,
            relative_raw_path="raw/session-proxy/spy.json.gz",
            max_response_bytes=_response_size_cap("SPY_SESSION_PROXY"),
            validator=lambda raw: _validate_eod_response(raw, expected_date=None),
        )
        active = _json_list(active_raw)
        delisted = _json_list(delisted_raw)
        identity_result = _normalize_identity_universe(active, delisted)
        identities = list(identity_result["identities"])
        identity_artifact = {
            "version": "eodhd_us_equity_identity_universe_v1",
            **identity_result,
        }
        identity_body = (
            json.dumps(identity_artifact, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        identity_artifact_sha256 = _write_verified(
            staging / "identity_universe.json", identity_body
        )
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('identity_universe_sha256', ?)",
            (identity_artifact_sha256,),
        )
        connection.commit()
        spy_rows = _json_list(spy_raw)
        month_ends = _last_spy_session_per_month(spy_rows, start=START_DATE, end=END_DATE)
        if len(month_ends) != 204:
            raise _AcquisitionFailure("SESSION_PROXY_COVERAGE_INVALID")
        nominal_units = _nominal_call_units(
            identity_count=len(identities),
            month_end_count=len(month_ends),
        )
        if nominal_units > MAXIMUM_CALL_UNITS:
            raise _AcquisitionFailure("CALL_BUDGET_PREFLIGHT_FAILED")
        if _available_disk_bytes(staging) < MINIMUM_FREE_DISK_BYTES:
            raise _AcquisitionFailure("INSUFFICIENT_DISK_SPACE")

        ordinal = 4
        allowed_codes = frozenset(str(identity["code"]) for identity in identities)
        for month_end in month_ends:
            for exchange in BULK_EXCHANGES:
                endpoint = _endpoint_identity(
                    f"/api/eod-bulk-last-day/{exchange}",
                    {"date": month_end, "fmt": "json"},
                )
                _obtain_response(
                    connection=connection,
                    staging=staging,
                    api_key=api_key,
                    ordinal=ordinal,
                    kind="MONTH_END_BULK",
                    endpoint_identity=endpoint,
                    call_units=100,
                    relative_raw_path=f"raw/bulk/{exchange}/{month_end}.json.gz",
                    max_response_bytes=_response_size_cap("MONTH_END_BULK"),
                    validator=lambda raw, expected=month_end, expected_exchange=exchange: (
                        _validate_bulk_response(
                            raw,
                            expected_date=expected,
                            expected_exchange=expected_exchange,
                            allowed_codes=allowed_codes,
                        )
                    ),
                )
                ordinal += 1

        history_summary = _download_symbol_histories(
            identities=identities,
            ordinal_start=ordinal,
            staging=staging,
            api_key=api_key,
        )
        if history_summary["unresolved"] / max(1, history_summary["requested"]) > 0.01:
            raise _AcquisitionFailure("UNRESOLVED_IDENTITY_FAILURE_LIMIT_EXCEEDED")
        return {
            **_state_result("DOWNLOAD_COMPLETE_PENDING_SELECTION", staging, connection),
            "identity_count": len(identities),
            "month_end_count": len(month_ends),
            "bulk_snapshot_count": len(month_ends) * len(BULK_EXCHANGES),
            "resolved_identity_count": history_summary["resolved"],
            "unresolved_identity_count": history_summary["unresolved"],
            "identity_sha256": identity_result["canonical_identity_sha256"],
            "request_sha256": plan["request_sha256"],
            "sealed_oos_opened": False,
            "broker_calls_used": 0,
            "registry_write_performed": False,
            "deployment_performed": False,
        }
    except _AcquisitionFailure as exc:
        return _state_result(exc.status, staging, connection)
    except (OSError, sqlite3.Error, ValueError):
        return _state_result("ACQUISITION_VALIDATION_FAILED", staging, connection)
    finally:
        connection.close()


def _validate_request(raw: Any) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS:
        raise ValueError("request fields are invalid.")
    expected = {
        "version": REQUEST_VERSION,
        "acquisition_id": ACQUISITION_ID,
        "provider": "EODHD",
        "approved_host": "eodhd.com",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "maximum_call_units": MAXIMUM_CALL_UNITS,
        "maximum_attempts_per_request": MAXIMUM_ATTEMPTS_PER_REQUEST,
        "history_concurrency": HISTORY_CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_symbol_response_bytes": MAXIMUM_SYMBOL_RESPONSE_BYTES,
        "maximum_bulk_response_bytes": MAXIMUM_BULK_RESPONSE_BYTES,
        "provenance": {"source": PROVENANCE_SOURCE},
    }
    for field, value in expected.items():
        if raw.get(field) != value:
            raise ValueError(f"{field} is invalid.")
    output_raw = raw.get("output_dir")
    if not isinstance(output_raw, str) or not output_raw.strip():
        raise ValueError("output_dir is invalid.")
    output_path = Path(output_raw)
    if not output_path.is_absolute():
        raise ValueError("output_dir must be absolute.")
    if output_path.is_symlink():
        raise ValueError("output_dir must not be a symlink.")
    resolved = output_path.resolve(strict=False)
    repository_root = Path(__file__).resolve().parents[2]
    try:
        resolved.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ValueError("output_dir must remain outside the repository.")
    return {**expected, "output_dir": str(resolved)}


def _open_state(path: Path, request_sha256: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            endpoint_identity TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            call_units INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            subject TEXT,
            raw_path TEXT,
            raw_sha256 TEXT,
            normalized_path TEXT,
            normalized_sha256 TEXT,
            response_bytes INTEGER,
            row_count INTEGER
        )
        """
    )
    existing = connection.execute(
        "SELECT value FROM meta WHERE key='request_sha256'"
    ).fetchone()
    if existing is not None and existing[0] != request_sha256:
        connection.close()
        raise _AcquisitionFailure("STAGING_REQUEST_MISMATCH")
    connection.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('request_sha256', ?)",
        (request_sha256,),
    )
    connection.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('provider_call_units_used', '0')"
    )
    connection.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('provider_http_requests_used', '0')"
    )
    connection.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('created_utc', ?)",
        (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
    )
    connection.commit()
    return connection


def _verify_staged_artifacts(staging: Path, connection: sqlite3.Connection) -> bool:
    identity_hash = connection.execute(
        "SELECT value FROM meta WHERE key='identity_universe_sha256'"
    ).fetchone()
    if identity_hash is not None:
        identity_path = staging / "identity_universe.json"
        if not identity_path.is_file() or _file_sha256(identity_path) != identity_hash[0]:
            return False
    rows = connection.execute(
        """
        SELECT raw_path, raw_sha256, normalized_path, normalized_sha256
        FROM requests WHERE status IN ('COMPLETE', 'RESOLVED_EMPTY')
        ORDER BY ordinal
        """
    ).fetchall()
    for raw_path, raw_sha256, normalized_path, normalized_sha256 in rows:
        if raw_path is not None:
            path = staging / str(raw_path)
            if not path.is_file() or _file_sha256(path) != raw_sha256:
                return False
        if normalized_path is not None:
            path = staging / str(normalized_path)
            if not path.is_file() or _file_sha256(path) != normalized_sha256:
                return False
    return True


def _obtain_response(
    *,
    connection: sqlite3.Connection,
    staging: Path,
    api_key: str,
    ordinal: int,
    kind: str,
    endpoint_identity: str,
    call_units: int,
    relative_raw_path: str,
    max_response_bytes: int,
    validator,
    relative_normalized_path: str | None = None,
    subject: str | None = None,
    before_request: Callable[[], None] | None = None,
) -> bytes:
    existing = connection.execute(
        """
        SELECT status, raw_path FROM requests WHERE endpoint_identity=?
        """,
        (endpoint_identity,),
    ).fetchone()
    if existing is not None and existing[0] in {"COMPLETE", "RESOLVED_EMPTY"}:
        return gzip.decompress((staging / str(existing[1])).read_bytes())
    if existing is not None and str(existing[0]).startswith("FAILED_"):
        raise _AcquisitionFailure(str(existing[0]).removeprefix("FAILED_"))
    connection.execute(
        """
        INSERT OR IGNORE INTO requests(
            endpoint_identity, ordinal, kind, call_units, attempts, status, subject
        ) VALUES(?, ?, ?, ?, 0, 'PLANNED', ?)
        """,
        (endpoint_identity, ordinal, kind, call_units, subject),
    )
    connection.commit()
    attempts = int(
        connection.execute(
            "SELECT attempts FROM requests WHERE endpoint_identity=?", (endpoint_identity,)
        ).fetchone()[0]
    )
    while attempts < MAXIMUM_ATTEMPTS_PER_REQUEST:
        _reserve_attempt(connection, endpoint_identity, call_units)
        attempts += 1
        try:
            if before_request is not None:
                before_request()
            raw, metadata = _download_raw(
                _authorized_url(endpoint_identity, api_key),
                timeout_seconds=TIMEOUT_SECONDS,
                max_response_bytes=max_response_bytes,
            )
            secret_encodings = {
                api_key.encode("utf-8"),
                urllib.parse.quote(api_key, safe="").encode("ascii"),
                urllib.parse.quote_plus(api_key).encode("ascii"),
            }
            if any(secret and secret in raw for secret in secret_encodings):
                raise _AcquisitionFailure("PROVIDER_RESPONSE_CONTAINED_SECRET")
            _validate_response_metadata(metadata, endpoint_identity, len(raw), max_response_bytes)
            payload, row_count = validator(raw)
        except RetryableProviderFailure:
            if attempts >= MAXIMUM_ATTEMPTS_PER_REQUEST:
                _mark_request_failed(
                    connection,
                    endpoint_identity,
                    "RETRYABLE_PROVIDER_FAILURE_EXHAUSTED",
                )
                raise _AcquisitionFailure("RETRYABLE_PROVIDER_FAILURE_EXHAUSTED")
            continue
        except _AcquisitionFailure as exc:
            _mark_request_failed(connection, endpoint_identity, exc.status)
            raise
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            _mark_request_failed(connection, endpoint_identity, "PROVIDER_RESPONSE_INVALID")
            raise _AcquisitionFailure("PROVIDER_RESPONSE_INVALID")

        raw_relative = Path(relative_raw_path)
        raw_encoded = gzip.compress(raw, compresslevel=6, mtime=0)
        raw_sha256 = _write_verified(staging / raw_relative, raw_encoded)
        normalized_sha256 = None
        normalized_relative: Path | None = None
        status = "RESOLVED_EMPTY" if row_count == 0 else "COMPLETE"
        if relative_normalized_path is not None and row_count > 0:
            normalized_relative = Path(relative_normalized_path)
            normalized_sha256 = _write_verified(
                staging / normalized_relative,
                _eod_csv_bytes(payload),
            )
        connection.execute(
            """
            UPDATE requests SET status=?, raw_path=?, raw_sha256=?, normalized_path=?,
                normalized_sha256=?, response_bytes=?, row_count=?
            WHERE endpoint_identity=?
            """,
            (
                status,
                raw_relative.as_posix(),
                raw_sha256,
                None if normalized_relative is None else normalized_relative.as_posix(),
                normalized_sha256,
                len(raw),
                row_count,
                endpoint_identity,
            ),
        )
        connection.commit()
        return raw
    _mark_request_failed(
        connection,
        endpoint_identity,
        "RETRYABLE_PROVIDER_FAILURE_EXHAUSTED",
    )
    raise _AcquisitionFailure("RETRYABLE_PROVIDER_FAILURE_EXHAUSTED")


def _mark_request_failed(
    connection: sqlite3.Connection,
    endpoint_identity: str,
    status: str,
) -> None:
    connection.execute(
        "UPDATE requests SET status=? WHERE endpoint_identity=?",
        (f"FAILED_{status}", endpoint_identity),
    )
    connection.commit()


def _reserve_attempt(
    connection: sqlite3.Connection,
    endpoint_identity: str,
    call_units: int,
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        used = int(
            connection.execute(
                "SELECT value FROM meta WHERE key='provider_call_units_used'"
            ).fetchone()[0]
        )
        requests = int(
            connection.execute(
                "SELECT value FROM meta WHERE key='provider_http_requests_used'"
            ).fetchone()[0]
        )
        if used + call_units > MAXIMUM_CALL_UNITS:
            raise _AcquisitionFailure("CALL_BUDGET_EXHAUSTED")
        connection.execute(
            "UPDATE meta SET value=? WHERE key='provider_call_units_used'",
            (str(used + call_units),),
        )
        connection.execute(
            "UPDATE meta SET value=? WHERE key='provider_http_requests_used'",
            (str(requests + 1),),
        )
        connection.execute(
            "UPDATE requests SET attempts=attempts+1, status='IN_PROGRESS' WHERE endpoint_identity=?",
            (endpoint_identity,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _validate_response_metadata(
    metadata: Any,
    endpoint_identity: str,
    response_bytes: int,
    maximum_bytes: int,
) -> None:
    if not isinstance(metadata, Mapping) or metadata.get("http_status") != 200:
        raise ValueError("provider metadata is invalid.")
    if response_bytes > maximum_bytes:
        raise ValueError("provider response exceeds the byte cap.")
    final_url = metadata.get("final_url")
    if not isinstance(final_url, str):
        raise ValueError("provider final URL is invalid.")
    final = urllib.parse.urlparse(final_url)
    expected = urllib.parse.urlparse(endpoint_identity)
    if final.scheme != "https" or final.hostname != "eodhd.com" or final.path != expected.path:
        raise ValueError("provider redirect or path drift is invalid.")
    final_query = urllib.parse.parse_qsl(final.query, keep_blank_values=True)
    token_values = [value for key, value in final_query if key == "api_token"]
    public_query = sorted((key, value) for key, value in final_query if key != "api_token")
    expected_query = sorted(urllib.parse.parse_qsl(expected.query, keep_blank_values=True))
    if len(token_values) != 1 or not token_values[0] or public_query != expected_query:
        raise ValueError("provider query drift is invalid.")


def _validate_identity_response(raw: bytes) -> tuple[list[dict[str, object]], int]:
    payload = _json_list(raw)
    if not payload or not all(isinstance(item, dict) for item in payload):
        raise ValueError("identity response is invalid.")
    return payload, len(payload)


def _validate_eod_response(
    raw: bytes,
    *,
    expected_date: str | None,
) -> tuple[list[dict[str, object]], int]:
    payload = _json_list(raw)
    normalized: list[dict[str, object]] = []
    previous: str | None = None
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("EOD row is invalid.")
        row = _normalize_eod_row(item, expected_date=expected_date)
        day = str(row["date"])
        if previous is not None and day <= previous:
            raise ValueError("EOD rows must be strictly ordered and unique.")
        normalized.append(row)
        previous = day
    return normalized, len(normalized)


def _validate_bulk_response(
    raw: bytes,
    *,
    expected_date: str,
    expected_exchange: str,
    allowed_codes: set[str] | frozenset[str],
) -> tuple[list[dict[str, object]], int]:
    if expected_exchange not in BULK_EXCHANGES:
        raise ValueError("bulk expected exchange is invalid.")
    payload = _json_list(raw)
    if not payload:
        raise ValueError("bulk response is empty.")
    normalized: list[dict[str, object]] = []
    seen_codes: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("bulk row is invalid.")
        code = str(item.get("code", "")).strip().upper()
        provider_exchange = str(item.get("exchange_short_name", "")).strip().upper()
        if (
            not code
            or len(code) > 64
            or any(not character.isprintable() for character in code)
            or provider_exchange != "US"
            or (code in allowed_codes and not _CODE_RE.fullmatch(code))
        ):
            raise ValueError("bulk identity or exchange is invalid.")
        identity = (code, expected_exchange)
        if identity in seen_codes:
            raise ValueError("bulk identities must be unique.")
        seen_codes.add(identity)
        day = _validate_eod_date(item, expected_date=expected_date)
        if code not in allowed_codes:
            continue
        normalized.append(
            {"code": code, "exchange_short_name": expected_exchange, "date": day}
        )
    return normalized, len(payload)


def _normalize_eod_row(
    item: Mapping[str, object],
    *,
    expected_date: str | None,
) -> dict[str, object]:
    day = _validate_eod_date(item, expected_date=expected_date)
    values = {
        field: _finite_number(item.get(field), field)
        for field in ("open", "high", "low", "close", "adjusted_close", "volume")
    }
    if any(
        values[field] <= 0.0
        for field in ("open", "high", "low", "close", "adjusted_close")
    ):
        raise ValueError("EOD prices must be positive.")
    if values["volume"] < 0.0:
        raise ValueError("EOD volume must be non-negative.")
    if (
        values["high"] < max(values["open"], values["low"], values["close"])
        or values["low"] > min(values["open"], values["high"], values["close"])
    ):
        raise ValueError("EOD OHLC relationship is invalid.")
    return {"date": day, **values}


def _validate_eod_date(
    item: Mapping[str, object],
    *,
    expected_date: str | None,
) -> str:
    day = item.get("date")
    if not isinstance(day, str):
        raise ValueError("EOD date is invalid.")
    parsed = date.fromisoformat(day)
    if not date.fromisoformat(START_DATE) <= parsed <= date.fromisoformat(END_DATE):
        raise ValueError("EOD date is outside the approved interval.")
    if expected_date is not None and day != expected_date:
        raise ValueError("EOD date does not match the requested date.")
    return day


def _json_list(raw: bytes) -> list[Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("provider response must be a JSON array.")
    return payload


def _eod_csv_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["timestamp", "open", "high", "low", "close", "adjusted_close", "volume"])
    for row in rows:
        writer.writerow(
            [
                row["date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["adjusted_close"],
                row["volume"],
            ]
        )
    return stream.getvalue().encode("utf-8")


def _write_verified(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    observed = temporary.read_bytes()
    if observed != body:
        raise OSError("staged artifact verification failed.")
    os.replace(temporary, path)
    return hashlib.sha256(body).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _available_disk_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _instrument_id(identity: Mapping[str, object]) -> str:
    return f"EODHD-US-{identity['exchange_mic']}-{identity['code']}"


def _download_symbol_histories(
    *,
    identities: Sequence[Mapping[str, object]],
    ordinal_start: int,
    staging: Path,
    api_key: str,
) -> dict[str, int]:
    limiter = _RateLimiter(rate_per_second=8.0, burst=8)

    def execute(index_and_identity: tuple[int, Mapping[str, object]]) -> bool:
        index, identity = index_and_identity
        instrument_id = _instrument_id(identity)
        digest = hashlib.sha256(instrument_id.encode("utf-8")).hexdigest()
        endpoint = _endpoint_identity(
            f"/api/eod/{urllib.parse.quote(str(identity['symbol']), safe='')}",
            {
                "fmt": "json",
                "from": START_DATE,
                "period": "d",
                "to": END_DATE,
            },
        )
        worker_connection = sqlite3.connect(staging / "state.sqlite", timeout=30.0)
        worker_connection.execute("PRAGMA busy_timeout=30000")
        try:
            try:
                _obtain_response(
                    connection=worker_connection,
                    staging=staging,
                    api_key=api_key,
                    ordinal=ordinal_start + index,
                    kind="SYMBOL_HISTORY",
                    endpoint_identity=endpoint,
                    call_units=1,
                    relative_raw_path=f"raw/history/{digest[:2]}/{digest}.json.gz",
                    max_response_bytes=_response_size_cap("SYMBOL_HISTORY"),
                    validator=lambda raw: _validate_eod_response(raw, expected_date=None),
                    relative_normalized_path=f"ohlcv-full/{digest[:2]}/{digest}.csv",
                    subject=instrument_id,
                    before_request=limiter.acquire,
                )
                return True
            except _AcquisitionFailure as exc:
                if exc.status not in {
                    "PROVIDER_RESPONSE_INVALID",
                    "RETRYABLE_PROVIDER_FAILURE_EXHAUSTED",
                }:
                    raise
                worker_connection.execute(
                    "UPDATE requests SET status=? WHERE endpoint_identity=?",
                    (f"FAILED_{exc.status}", endpoint),
                )
                worker_connection.commit()
                return False
        finally:
            worker_connection.close()

    executor = ThreadPoolExecutor(max_workers=HISTORY_CONCURRENCY)
    futures = [executor.submit(execute, item) for item in enumerate(identities)]
    try:
        resolved = sum(1 for future in futures if future.result())
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return {
        "requested": len(identities),
        "resolved": resolved,
        "unresolved": len(identities) - resolved,
    }


def _nominal_call_units(*, identity_count: int, month_end_count: int) -> int:
    if identity_count < 0 or month_end_count < 0:
        raise ValueError("request counts must be non-negative.")
    return 3 + month_end_count * len(BULK_EXCHANGES) * 100 + identity_count


def _worst_case_call_units(*, identity_count: int, month_end_count: int) -> int:
    return MAXIMUM_ATTEMPTS_PER_REQUEST * _nominal_call_units(
        identity_count=identity_count,
        month_end_count=month_end_count,
    )


def _response_size_cap(kind: str) -> int:
    if kind in {"ACTIVE_COMMON_STOCKS", "DELISTED_COMMON_STOCKS", "MONTH_END_BULK"}:
        return MAXIMUM_BULK_RESPONSE_BYTES
    if kind in {"SPY_SESSION_PROXY", "SYMBOL_HISTORY"}:
        return MAXIMUM_SYMBOL_RESPONSE_BYTES
    raise ValueError("request kind is invalid.")


def _authorized_url(endpoint_identity: str, api_key: str) -> str:
    parsed = urllib.parse.urlparse(endpoint_identity)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("api_token", api_key))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _download_raw(
    url: str,
    *,
    timeout_seconds: int,
    max_response_bytes: int,
) -> tuple[bytes, dict[str, object]]:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("redirects are forbidden.")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "research-lab-eodhd-acquisition/1"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise ValueError("response exceeds byte cap.")
            return raw, {
                "http_status": int(getattr(response, "status", 200)),
                "final_url": response.geturl(),
                "response_bytes": len(raw),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise RetryableProviderFailure("retryable provider failure") from None
        raise ValueError("permanent provider failure") from None
    except (TimeoutError, urllib.error.URLError):
        raise RetryableProviderFailure("retryable provider failure") from None


def _finite_number(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")
    return value


def _state_result(
    status: str,
    staging: Path,
    connection: sqlite3.Connection,
) -> dict[str, object]:
    call_units = int(
        connection.execute(
            "SELECT value FROM meta WHERE key='provider_call_units_used'"
        ).fetchone()[0]
    )
    http_requests = int(
        connection.execute(
            "SELECT value FROM meta WHERE key='provider_http_requests_used'"
        ).fetchone()[0]
    )
    return {
        "version": "eodhd_us_equity_universe_acquisition_result_v1",
        "status": status,
        "staging_dir": str(staging),
        "provider_call_units_used": call_units,
        "provider_http_requests_used": http_requests,
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
    }


def _failure_result(status: str, *, call_units: int, http_requests: int) -> dict[str, object]:
    return {
        "version": "eodhd_us_equity_universe_acquisition_result_v1",
        "status": status,
        "provider_call_units_used": call_units,
        "provider_http_requests_used": http_requests,
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
    }


def _normalize_identity_universe(
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    normalized: list[dict[str, str | None]] = []
    seen_exact: set[tuple[str, str, str, str, str | None]] = set()
    exact_duplicates = 0
    filtered = 0
    for status, rows in (("ACTIVE", active_rows), ("DELISTED", delisted_rows)):
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise ValueError("identity rows must be a sequence.")
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("identity row must be a mapping.")
            code = str(raw.get("Code", "")).strip().upper()
            exchange = str(raw.get("Exchange", "")).strip().upper()
            currency = str(raw.get("Currency", "")).strip().upper()
            instrument_type = str(raw.get("Type", "")).strip().casefold()
            isin_raw = raw.get("Isin")
            isin = None if isin_raw in (None, "") else str(isin_raw).strip().upper()
            if (
                not _CODE_RE.fullmatch(code)
                or exchange not in SUPPORTED_EXCHANGE_MICS
                or currency != "USD"
                or instrument_type != "common stock"
            ):
                filtered += 1
                continue
            exact = (status, code, exchange, currency, isin)
            if exact in seen_exact:
                exact_duplicates += 1
                continue
            seen_exact.add(exact)
            normalized.append(
                {
                    "code": code,
                    "symbol": f"{code}.US",
                    "status": status,
                    "exchange": exchange,
                    "exchange_mic": SUPPORTED_EXCHANGE_MICS[exchange],
                    "currency": currency,
                    "isin": isin,
                }
            )
    by_code: dict[str, list[dict[str, str | None]]] = {}
    for item in normalized:
        by_code.setdefault(str(item["code"]), []).append(item)
    ambiguous = sorted(code for code, items in by_code.items() if len(items) != 1)
    identities = sorted(
        (items[0] for code, items in by_code.items() if code not in set(ambiguous)),
        key=lambda item: str(item["code"]),
    )
    return {
        "identities": identities,
        "identity_count": len(identities),
        "exact_duplicate_count": exact_duplicates,
        "ambiguous_codes": ambiguous,
        "filtered_row_count": filtered,
        "canonical_identity_sha256": _canonical_sha256(identities),
    }


def _last_spy_session_per_month(
    rows: Sequence[Mapping[str, object]],
    *,
    start: str,
    end: str,
) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError("interval is invalid.")
    observed: list[date] = []
    for raw in rows:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("date"), str):
            raise ValueError("SPY session row is invalid.")
        try:
            current = date.fromisoformat(str(raw["date"]))
        except ValueError as exc:
            raise ValueError("SPY session date is invalid.") from exc
        if not start_date <= current <= end_date:
            raise ValueError("SPY session date is outside the approved interval.")
        if observed and current <= observed[-1]:
            raise ValueError("SPY sessions must be strictly ordered and unique.")
        observed.append(current)
    if not observed:
        raise ValueError("SPY session history is empty.")
    last_by_month: dict[tuple[int, int], date] = {}
    for current in observed:
        last_by_month[(current.year, current.month)] = current
    return [value.isoformat() for _, value in sorted(last_by_month.items())]


def _endpoint_identity(path: str, params: dict[str, str]) -> str:
    return f"https://eodhd.com{path}?{urllib.parse.urlencode(sorted(params.items()))}"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
