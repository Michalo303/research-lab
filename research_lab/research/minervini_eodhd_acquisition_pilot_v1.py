from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence


PLAN_VERSION = "minervini_eodhd_acquisition_plan_v1"
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def build_minervini_eodhd_acquisition_plan_v1(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Normalize the universe and construct the remaining 22 requests."""
    active, active_duplicates = _normalize_universe(active_rows, "ACTIVE")
    delisted, delisted_duplicates = _normalize_universe(
        delisted_rows, "DELISTED"
    )
    active_codes = {row["code"] for row in active}
    delisted_codes = {row["code"] for row in delisted}
    collisions = sorted(active_codes & delisted_codes)
    blockers = (
        ["ACTIVE_DELISTED_IDENTITY_COLLISION"] if collisions else []
    )
    sample_symbols = _sample_symbols(active, delisted)
    request_specs = [
        {
            "name": "symbol_change_history",
            "artifact_name": "symbol-change-history.json",
            "endpoint_identity": _endpoint(
                "/symbol-change-history",
                {
                    "from": RAW_START,
                    "to": RAW_END,
                    "ex": "US",
                    "fmt": "json",
                },
            ),
        },
        {
            "name": "split_calendar",
            "artifact_name": "split-calendar-sample.json",
            "endpoint_identity": _endpoint(
                "/calendar/splits",
                {"from": RAW_START, "to": RAW_END, "fmt": "json"},
            ),
        },
        *[
            {
                "name": f"eod_{symbol}",
                "artifact_name": f"eod-{_artifact_symbol(symbol)}.json",
                "endpoint_identity": _endpoint(
                    f"/eod/{symbol}",
                    {
                        "from": RAW_START,
                        "to": RAW_END,
                        "period": "d",
                        "fmt": "json",
                    },
                ),
            }
            for symbol in sample_symbols
        ],
    ]
    if len(request_specs) + 2 != PROVIDER_REQUEST_LIMIT:
        raise RuntimeError("frozen provider request count changed.")
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "provider_request_limit": PROVIDER_REQUEST_LIMIT,
        "raw_start": RAW_START,
        "raw_end": RAW_END,
        "evaluation_start": EVALUATION_START,
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


def _normalize_universe(
    rows: Sequence[Mapping[str, object]],
    status: str,
) -> tuple[list[dict[str, str | None]], int]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
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
        if isin_value is None:
            isin = None
        else:
            isin = _text(isin_value, "Isin").upper()
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
    active_ranked = _ranked(active)
    delisted_ranked = _ranked(delisted)
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    if "AAPL" not in active_codes:
        raise ValueError("active universe does not contain AAPL.")
    if "ATVI" not in delisted_codes:
        raise ValueError("delisted universe does not contain ATVI.")
    chosen = ["SPY.US", "AAPL.US"]
    chosen.extend(
        f"{row['code']}.US"
        for row in active_ranked
        if row["code"] != "AAPL"
    )
    chosen_active = _unique(chosen)[:10]
    if len(chosen_active) != 10:
        raise ValueError("active universe cannot fill the frozen sample.")
    chosen = [*chosen_active, "ATVI.US"]
    chosen.extend(
        f"{row['code']}.US"
        for row in delisted_ranked
        if row["code"] != "ATVI"
    )
    result = _unique(chosen)[:20]
    if len(result) != 20:
        raise ValueError("delisted universe cannot fill the frozen sample.")
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


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
