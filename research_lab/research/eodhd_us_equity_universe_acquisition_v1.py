from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import date
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
