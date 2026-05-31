from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from research_lab.fundamentals import FundamentalRow, is_timestamp_safe


_METADATA_FIELDS = {
    "date",
    "filing_date",
    "filingdate",
    "accepted_date",
    "accepteddate",
    "available_date",
    "availabledate",
    "asof_date",
    "as_of_date",
    "currency_symbol",
    "currency",
}


def classify_eodhd_payload(payload: Any, request_url: str = "") -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("error"):
        return {
            "provider": "eodhd",
            "status": "provider_error",
            "request_url": _sanitize_url(request_url),
            "message": _mask_secret_text(str(payload.get("message", payload.get("error", "")))),
            "timestamp_safety": "unknown",
        }
    rows, diagnostics = parse_eodhd_fundamentals("", payload)
    safe_count = sum(1 for row in rows if is_timestamp_safe(row))
    uncertain_count = len(rows) - safe_count
    return {
        "provider": "eodhd",
        "status": "ok",
        "request_url": _sanitize_url(request_url),
        "row_count": len(rows),
        "diagnostic_count": len(diagnostics),
        "timestamp_safe_rows": safe_count,
        "uncertain_rows": uncertain_count,
        "timestamp_safety": "timestamp_safe" if rows and uncertain_count == 0 else "uncertain",
    }


def parse_eodhd_fundamentals(
    symbol: str,
    payload: Any,
    ingestion_timestamp: str | None = None,
) -> tuple[list[FundamentalRow], list[dict[str, Any]]]:
    ingestion_timestamp = ingestion_timestamp or datetime.now(timezone.utc).isoformat()
    rows: list[FundamentalRow] = []
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return rows, [{"provider": "eodhd", "status": "unsupported_payload", "timestamp_safety": "unknown"}]
    if payload.get("error"):
        diagnostics.append(
            {
                "provider": "eodhd",
                "status": "provider_error",
                "message": _mask_secret_text(str(payload.get("message", payload.get("error", "")))),
                "timestamp_safety": "unknown",
            }
        )
        return rows, diagnostics

    financials = payload.get("Financials") or payload.get("financials") or {}
    if not isinstance(financials, dict):
        return rows, [{"provider": "eodhd", "status": "missing_financials", "timestamp_safety": "unknown"}]

    normalized_symbol = _normalize_symbol(symbol)
    for raw_statement_type, statement_payload in financials.items():
        if not isinstance(statement_payload, dict):
            continue
        statement_type = _snake_case(str(raw_statement_type))
        for period_kind, fiscal_period in (("yearly", "FY"), ("quarterly", "Q")):
            period_payload = statement_payload.get(period_kind)
            if not isinstance(period_payload, dict):
                continue
            for period_key, record in period_payload.items():
                if not isinstance(record, dict):
                    continue
                dates = _extract_dates(record)
                period_end_date = record.get("date") or record.get("period_end_date") or record.get("period_end") or period_key
                currency = str(record.get("currency_symbol") or record.get("currency") or "")
                fiscal_year = _fiscal_year(period_end_date)
                record_rows = []
                for field_name, value in record.items():
                    if field_name.lower() in _METADATA_FIELDS:
                        continue
                    numeric = _to_float(value)
                    if numeric is None:
                        continue
                    row = FundamentalRow(
                        symbol=normalized_symbol,
                        provider="eodhd",
                        statement_type=statement_type,
                        fiscal_period=fiscal_period,
                        fiscal_year=fiscal_year,
                        period_end_date=period_end_date,
                        filing_date=dates.get("filing_date"),
                        accepted_date=dates.get("accepted_date"),
                        available_date=dates.get("available_date"),
                        asof_date=dates.get("asof_date"),
                        field_name=str(field_name),
                        value=numeric,
                        currency=currency,
                        source_url=str(record.get("source_url") or ""),
                        provider_record_id=str(record.get("id") or period_key),
                        ingestion_timestamp=ingestion_timestamp,
                    )
                    rows.append(row)
                    record_rows.append(row)
                if record_rows or record:
                    timestamp_safety = "timestamp_safe" if any(is_timestamp_safe(row) for row in record_rows) else "uncertain"
                    diagnostics.append(
                        {
                            "provider": "eodhd",
                            "symbol": normalized_symbol,
                            "statement_type": statement_type,
                            "period_kind": period_kind,
                            "period_end_date": str(period_end_date or ""),
                            "row_count": len(record_rows),
                            "timestamp_safety": timestamp_safety,
                        }
                    )
    return rows, diagnostics


def _extract_dates(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "filing_date": record.get("filing_date") or record.get("filingDate"),
        "accepted_date": record.get("accepted_date") or record.get("acceptedDate"),
        "available_date": record.get("available_date") or record.get("availableDate"),
        "asof_date": record.get("asof_date") or record.get("as_of_date") or record.get("asOfDate"),
    }


def _normalize_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0].upper() if symbol else ""


def _fiscal_year(value: Any) -> int | None:
    text = str(value or "")
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.replace(" ", "_").replace("-", "_").lower()


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _sanitize_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"(?i)(api_token=)[^&]+", r"\1***", url)


def _mask_secret_text(text: str) -> str:
    return re.sub(r"(?i)((?:api[_ -]?)?(?:token|key)\s*[=:]?\s*)\S+", r"\1***", text)
