from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from research_lab.fundamentals import FundamentalRow, is_timestamp_safe


def classify_massive_payload(payload: Any, request_url: str = "") -> dict[str, Any]:
    if _is_error_payload(payload):
        return {
            "provider": "massive",
            "status": "provider_error",
            "request_url": _sanitize_url(request_url),
            "message": _mask_secret_text(_error_message(payload)),
            "timestamp_safety": "unknown",
        }
    rows, diagnostics = parse_massive_fundamentals("", payload)
    safe_count = sum(1 for row in rows if is_timestamp_safe(row))
    uncertain_count = len(rows) - safe_count
    return {
        "provider": "massive",
        "status": "ok",
        "request_url": _sanitize_url(request_url),
        "row_count": len(rows),
        "diagnostic_count": len(diagnostics),
        "timestamp_safe_rows": safe_count,
        "uncertain_rows": uncertain_count,
        "timestamp_safety": "timestamp_safe" if rows and uncertain_count == 0 else "uncertain",
    }


def parse_massive_fundamentals(
    symbol: str,
    payload: Any,
    ingestion_timestamp: str | None = None,
) -> tuple[list[FundamentalRow], list[dict[str, Any]]]:
    ingestion_timestamp = ingestion_timestamp or datetime.now(timezone.utc).isoformat()
    rows: list[FundamentalRow] = []
    diagnostics: list[dict[str, Any]] = []
    if _is_error_payload(payload):
        diagnostics.append(
            {
                "provider": "massive",
                "status": "provider_error",
                "message": _mask_secret_text(_error_message(payload)),
                "timestamp_safety": "unknown",
            }
        )
        return rows, diagnostics
    records = _records(payload)
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized_symbol = _record_symbol(symbol, record)
        filing_date = record.get("filing_date")
        accepted_date = record.get("accepted_date") or record.get("accepted_datetime") or record.get("acceptance_datetime")
        available_date = record.get("available_date")
        asof_date = record.get("asof_date") or record.get("as_of_date")
        period_end_date = record.get("period_end_date") or record.get("period_end") or record.get("period_of_report_date") or record.get("end_date")
        fiscal_period = str(record.get("fiscal_period") or record.get("fiscal_quarter") or record.get("timeframe") or "")
        fiscal_year = _to_int(record.get("fiscal_year"))
        provider_record_id = str(record.get("id") or record.get("filing_accession_number") or "")
        source_url = str(record.get("source_filing_url") or record.get("filing_url") or "")
        financials = record.get("financials") or {}
        record_rows: list[FundamentalRow] = []
        if isinstance(financials, dict):
            for statement_type, statement_payload in financials.items():
                if not isinstance(statement_payload, dict):
                    continue
                for field_name, field_payload in statement_payload.items():
                    value, currency = _field_value_and_currency(field_payload)
                    if value is None:
                        continue
                    row = FundamentalRow(
                        symbol=normalized_symbol,
                        provider="massive",
                        statement_type=str(statement_type),
                        fiscal_period=fiscal_period,
                        fiscal_year=fiscal_year,
                        period_end_date=period_end_date,
                        filing_date=filing_date,
                        accepted_date=accepted_date,
                        available_date=available_date,
                        asof_date=asof_date,
                        field_name=str(field_name),
                        value=value,
                        currency=currency,
                        source_url=source_url,
                        provider_record_id=provider_record_id,
                        ingestion_timestamp=ingestion_timestamp,
                    )
                    rows.append(row)
                    record_rows.append(row)
        ratios_status = ""
        if record.get("ratios"):
            ratios_status = "ignored_untraceable_timestamp_source"
        diagnostics.append(
            {
                "provider": "massive",
                "symbol": normalized_symbol,
                "period_end_date": str(period_end_date or ""),
                "row_count": len(record_rows),
                "timestamp_safety": "timestamp_safe" if record_rows and all(is_timestamp_safe(row) for row in record_rows) else "uncertain",
                "ratios_status": ratios_status,
            }
        )
    return rows, diagnostics


def _records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return results
        if isinstance(results, dict):
            return [results]
    return []


def _is_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").upper()
    return status in {"ERROR", "NOT_AUTHORIZED", "LIMIT_EXCEEDED"} or bool(payload.get("error"))


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("error") or payload.get("message") or payload.get("detail") or payload.get("status") or "")


def _record_symbol(symbol: str, record: dict[str, Any]) -> str:
    if symbol:
        return symbol.upper()
    tickers = record.get("tickers")
    if isinstance(tickers, list) and tickers:
        return str(tickers[0]).upper()
    return str(record.get("ticker") or record.get("symbol") or "").upper()


def _field_value_and_currency(payload: Any) -> tuple[float | None, str]:
    if isinstance(payload, dict):
        value = payload.get("value")
        currency = str(payload.get("unit") or payload.get("currency") or "")
    else:
        value = payload
        currency = ""
    if value is None or value == "":
        return None, currency
    try:
        return float(str(value).replace(",", "")), currency
    except ValueError:
        return None, currency


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sanitize_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"(?i)(apikey=)[^&]+", r"\1***", url)


def _mask_secret_text(text: str) -> str:
    return re.sub(r"(?i)((?:api[_ -]?)?(?:token|key)\s*[=:]?\s*)\S+", r"\1***", text)
