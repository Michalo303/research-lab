from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import date


PLAN_VERSION = "minervini_eodhd_acquisition_plan_v2"
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"
SPLIT_LINEAGE_CLASSIFICATION = (
    "PROVIDER_REPORTED_EVENTS_NOT_COMPLETENESS_PROOF"
)
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def build_minervini_eodhd_acquisition_plan_v2(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build eleven paired EOD/split requests for atomic provider tickers."""
    active, active_duplicates = _normalize_universe(active_rows, "ACTIVE")
    delisted, delisted_duplicates = _normalize_universe(
        delisted_rows, "DELISTED"
    )
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    collisions = sorted(active_codes & delisted_codes)
    blockers = (
        ["ACTIVE_DELISTED_IDENTITY_COLLISION"] if collisions else []
    )
    sample_symbols = _sample_symbols(active, delisted)
    request_specs: list[dict[str, str]] = []
    for symbol in sample_symbols:
        artifact_symbol = _artifact_symbol(symbol)
        request_specs.extend(
            [
                {
                    "kind": "eod",
                    "symbol": symbol,
                    "artifact_name": f"eod-{artifact_symbol}.json",
                    "endpoint_identity": _endpoint(
                        f"/eod/{symbol}",
                        {
                            "from": RAW_START,
                            "to": RAW_END,
                            "period": "d",
                            "fmt": "json",
                        },
                    ),
                },
                {
                    "kind": "splits",
                    "symbol": symbol,
                    "artifact_name": f"splits-{artifact_symbol}.json",
                    "endpoint_identity": _endpoint(
                        f"/splits/{symbol}",
                        {
                            "from": RAW_START,
                            "to": RAW_END,
                            "fmt": "json",
                        },
                    ),
                },
            ]
        )
    if len(request_specs) + 2 != PROVIDER_REQUEST_LIMIT:
        raise RuntimeError("frozen V2 provider request count changed.")
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "provider_request_limit": PROVIDER_REQUEST_LIMIT,
        "raw_start": RAW_START,
        "raw_end": RAW_END,
        "evaluation_start": EVALUATION_START,
        "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
        "rename_continuity_supported": False,
        "wide_acquisition_scope": "ATOMIC_PROVIDER_TICKER_HISTORIES",
        "universe": {
            "active_count": len(active),
            "delisted_count": len(delisted),
            "active_duplicate_count": active_duplicates,
            "delisted_duplicate_count": delisted_duplicates,
            "active_delisted_collision_count": len(collisions),
            "active_delisted_collision_codes": collisions,
            "deduplicated_code_count": len(active_codes | delisted_codes),
            "active_identity_sha256": _hash(active),
            "delisted_identity_sha256": _hash(delisted),
        },
        "sample_symbols": sample_symbols,
        "request_specs": request_specs,
        "blockers": blockers,
    }
    result["output_payload_sha256"] = _hash(result)
    return result


def validate_minervini_symbol_splits_v2(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, list):
        raise ValueError("split payload must be an array.")
    previous: date | None = None
    for row in payload:
        if not isinstance(row, Mapping):
            raise ValueError("split rows must be objects.")
        current = _date(row.get("date"), "split date")
        if not date.fromisoformat(RAW_START) <= current <= date.fromisoformat(
            RAW_END
        ):
            raise ValueError("split date is outside the frozen interval.")
        if previous is not None and current <= previous:
            raise ValueError("split dates must be strictly ordered and unique.")
        ratio = row.get("split")
        if not isinstance(ratio, str) or ratio.count("/") != 1:
            raise ValueError("split ratio must use new/old form.")
        numerator_text, denominator_text = ratio.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError as exc:
            raise ValueError("split ratio must be numeric.") from exc
        if (
            not math.isfinite(numerator)
            or not math.isfinite(denominator)
            or numerator <= 0
            or denominator <= 0
        ):
            raise ValueError("split ratio values must be positive and finite.")
        previous = current
    return {
        "status": "VALID",
        "record_count": len(payload),
        "first_date": str(payload[0]["date"]) if payload else None,
        "last_date": str(payload[-1]["date"]) if payload else None,
        "lineage_classification": SPLIT_LINEAGE_CLASSIFICATION,
    }


def estimate_minervini_atomic_acquisition_v2(
    *,
    deduplicated_symbol_count: int,
    sample_summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    symbols = _positive_int(
        deduplicated_symbol_count, "deduplicated_symbol_count"
    )
    if (
        isinstance(sample_summaries, (str, bytes))
        or not isinstance(sample_summaries, Sequence)
        or not sample_summaries
    ):
        raise ValueError("sample_summaries must be a non-empty sequence.")
    bytes_per_row: list[float] = []
    row_counts: list[int] = []
    split_bytes: list[int] = []
    for summary in sample_summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("sample summaries must be objects.")
        rows = _positive_int(
            summary.get("eod_row_count"), "sample eod_row_count"
        )
        eod_bytes = _positive_int(
            summary.get("eod_response_bytes"), "sample eod_response_bytes"
        )
        per_symbol_split_bytes = _non_negative_int(
            summary.get("split_response_bytes"),
            "sample split_response_bytes",
        )
        row_counts.append(rows)
        bytes_per_row.append(eod_bytes / rows)
        split_bytes.append(per_symbol_split_bytes)
    median_rows = sorted(row_counts)[len(row_counts) // 2]
    estimated_rows = symbols * median_rows
    storage_lower = math.floor(
        min(bytes_per_row) * estimated_rows + min(split_bytes) * symbols
    )
    storage_upper = math.ceil(
        max(bytes_per_row) * estimated_rows + max(split_bytes) * symbols
    )
    total_requests = 2 + symbols * 2
    return {
        "full_history_eod_requests": symbols,
        "per_symbol_split_requests": symbols,
        "universe_requests": 2,
        "total_http_requests": total_requests,
        "total_call_units": total_requests,
        "total_call_units_exact": True,
        "minimum_acquisition_days_at_100000_units": math.ceil(
            total_requests / 100_000
        ),
        "minimum_runtime_seconds_at_1000_per_minute": math.ceil(
            total_requests * 60 / 1_000
        ),
        "conservative_runtime_seconds_at_5_per_second": math.ceil(
            total_requests / 5
        ),
        "raw_storage_bytes_lower_bound": storage_lower,
        "raw_storage_bytes_upper_bound": storage_upper,
        "storage_estimate_status": "ESTIMATED_FROM_SAMPLE",
    }


def _normalize_universe(
    rows: Sequence[Mapping[str, object]],
    status: str,
) -> tuple[list[dict[str, str | None]], int]:
    if (
        isinstance(rows, (str, bytes))
        or not isinstance(rows, Sequence)
        or not rows
    ):
        raise ValueError(f"{status.lower()} rows must be a non-empty sequence.")
    normalized: list[dict[str, str | None]] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    duplicate_count = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("universe rows must be objects.")
        instrument_type = _text(raw.get("Type"), "Type")
        if instrument_type.casefold() != "common stock":
            raise ValueError("universe rows must be Common Stock.")
        code = _text(raw.get("Code"), "Code").upper()
        if not _CODE_RE.fullmatch(code):
            raise ValueError("provider Code is malformed.")
        exchange = _text(raw.get("Exchange"), "Exchange").upper()
        currency = _text(raw.get("Currency"), "Currency").upper()
        isin_value = raw.get("Isin")
        isin = (
            None
            if isin_value is None or isin_value == ""
            else _text(isin_value, "Isin").upper()
        )
        identity = (code, exchange, currency, isin)
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        normalized.append(
            {
                "status": status,
                "code": code,
                "exchange": exchange,
                "currency": currency,
                "isin": isin,
            }
        )
    normalized.sort(
        key=lambda row: (
            str(row["code"]),
            str(row["exchange"]),
            str(row["currency"]),
            str(row["isin"] or ""),
        )
    )
    return normalized, duplicate_count


def _sample_symbols(
    active: list[dict[str, str | None]],
    delisted: list[dict[str, str | None]],
) -> list[str]:
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    if "AAPL" not in active_codes:
        raise ValueError("active universe does not contain AAPL.")
    if "ATVI" not in delisted_codes:
        raise ValueError("delisted universe does not contain ATVI.")
    active_candidates = [
        "SPY.US",
        "AAPL.US",
        *(
            f"{row['code']}.US"
            for row in _ranked(active)
            if row["code"] != "AAPL"
        ),
    ]
    active_sample = _unique(active_candidates)[:6]
    if len(active_sample) != 6:
        raise ValueError("active universe cannot fill the frozen V2 sample.")
    candidates = [
        *active_sample,
        "ATVI.US",
        *(
            f"{row['code']}.US"
            for row in _ranked(delisted)
            if row["code"] != "ATVI"
        ),
    ]
    result = _unique(candidates)[:11]
    if len(result) != 11:
        raise ValueError("delisted universe cannot fill the frozen V2 sample.")
    return result


def _ranked(
    rows: list[dict[str, str | None]],
) -> list[dict[str, str | None]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            (
                f"{row['status']}:{row['code']}:{row['exchange']}:"
                f"{row['currency']}:{row['isin'] or ''}"
            ).encode("utf-8")
        ).hexdigest(),
    )


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _endpoint(path: str, query: Mapping[str, str]) -> str:
    return f"https://eodhd.com/api{path}?{urllib.parse.urlencode(query)}"


def _artifact_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", symbol)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be normalized non-empty text.")
    return value


def _date(value: object, name: str) -> date:
    text = _text(value, name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date.") from exc


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
