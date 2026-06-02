from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable


PROVIDER = "FMP"

SUPPORTED_STATEMENT_TYPES = {
    "income_statement": "/stable/income-statement",
    "balance_sheet": "/stable/balance-sheet-statement",
    "cash_flow": "/stable/cash-flow-statement",
}
SUPPORTED_PERIOD_TYPES = {"annual", "quarterly"}
SUPPORTED_FMP_CORE_ENDPOINTS = tuple(SUPPORTED_STATEMENT_TYPES.values())

_METADATA_FIELDS = {
    "accepteddate",
    "accepted_date",
    "calendar_year",
    "calendaryear",
    "cik",
    "companyname",
    "company_name",
    "currency",
    "date",
    "filingdate",
    "filing_date",
    "fillingdate",
    "filling_date",
    "finallink",
    "final_link",
    "fiscalyear",
    "fiscal_year",
    "formtype",
    "form_type",
    "id",
    "link",
    "period",
    "periodenddate",
    "period_end_date",
    "reportedcurrency",
    "reported_currency",
    "sourceurl",
    "source_url",
    "symbol",
}


@dataclass(frozen=True)
class FmpFundamentalRecord:
    symbol: str
    provider: str
    statement_type: str
    period_type: str
    fiscal_year: int | None
    fiscal_period: str
    period_end_date: str | None
    filing_date: str | None
    accepted_date: str | None
    available_date: str | None
    field_name: str
    value: float
    currency: str
    source_url: str
    source_endpoint: str
    provider_record_id: str
    ingestion_timestamp: str
    timestamp_safe: bool
    timestamp_confidence: str
    timestamp_source: str


def normalize_fmp_core_statement_payload(
    *,
    symbol: str,
    statement_type: str,
    period_type: str,
    payload: Any,
    source_endpoint: str,
    source_url: str = "",
    api_key: str = "",
    ingestion_timestamp: str | None = None,
) -> list[FmpFundamentalRecord]:
    _validate_supported_target(statement_type, period_type, source_endpoint)
    if not isinstance(payload, list):
        return []

    normalized_symbol = _normalize_symbol(symbol)
    endpoint = _sanitize_url(source_endpoint, api_key)
    url = _sanitize_url(source_url, api_key)
    ingested_at = ingestion_timestamp or datetime.now(timezone.utc).isoformat()
    records: list[FmpFundamentalRecord] = []

    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        record_symbol = _normalize_symbol(str(item.get("symbol") or normalized_symbol))
        accepted_date = _string_or_none(_first_present(item, ("acceptedDate", "accepted_date")))
        filing_date = _string_or_none(_first_present(item, ("filingDate", "fillingDate", "filing_date", "filling_date")))
        available_date, timestamp_safe, timestamp_source, timestamp_confidence = _availability_fields(
            accepted_date=accepted_date,
            filing_date=filing_date,
        )
        period_end_date = _string_or_none(_first_present(item, ("date", "periodEndDate", "period_end_date")))
        fiscal_year = _fiscal_year(_first_present(item, ("fiscalYear", "calendarYear", "fiscal_year", "calendar_year", "date")))
        fiscal_period = str(_first_present(item, ("period",)) or "")
        currency = str(_first_present(item, ("reportedCurrency", "reported_currency", "currency")) or "")
        provider_record_id = str(_first_present(item, ("id", "provider_record_id")) or "")
        if not provider_record_id and len(payload) > 1:
            provider_record_id = f"{record_symbol}:{statement_type}:{period_type}:{index}"
        item_source_url = _sanitize_url(str(item.get("source_url") or item.get("sourceUrl") or item.get("link") or item.get("finalLink") or url), api_key)

        for field_name, raw_value in item.items():
            if _is_metadata_field(field_name):
                continue
            numeric = _to_float(raw_value)
            if numeric is None:
                continue
            records.append(
                FmpFundamentalRecord(
                    symbol=record_symbol,
                    provider=PROVIDER,
                    statement_type=statement_type,
                    period_type=period_type,
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    period_end_date=period_end_date,
                    filing_date=filing_date,
                    accepted_date=accepted_date,
                    available_date=available_date,
                    field_name=str(field_name),
                    value=numeric,
                    currency=currency,
                    source_url=item_source_url,
                    source_endpoint=endpoint,
                    provider_record_id=provider_record_id,
                    ingestion_timestamp=ingested_at,
                    timestamp_safe=timestamp_safe,
                    timestamp_confidence=timestamp_confidence,
                    timestamp_source=timestamp_source,
                )
            )
    return records


def fundamentals_asof(
    records: Iterable[FmpFundamentalRecord],
    asof_date: str | date | datetime,
    *,
    include_unsafe: bool = False,
) -> list[FmpFundamentalRecord]:
    cutoff = _parse_date(asof_date)
    if cutoff is None:
        raise ValueError(f"Invalid asof_date: {asof_date!r}")

    filtered: list[FmpFundamentalRecord] = []
    for record in records:
        if not record.timestamp_safe:
            if include_unsafe:
                filtered.append(record)
            continue
        available = _parse_date(record.available_date)
        if available is not None and available <= cutoff:
            filtered.append(record)
    return filtered


def coverage_diagnostics(
    *,
    symbols_requested: Iterable[str],
    records: Iterable[FmpFundamentalRecord],
) -> dict[str, Any]:
    requested = _ordered_symbols(symbols_requested)
    record_list = list(records)
    returned = _ordered_symbols(record.symbol for record in record_list if record.symbol)
    missing = [symbol for symbol in requested if symbol not in set(returned)]
    safe_records = [record for record in record_list if record.timestamp_safe]
    unsafe_records = [record for record in record_list if not record.timestamp_safe]
    available_dates = sorted(
        parsed
        for record in safe_records
        if (parsed := _parse_date(record.available_date)) is not None
    )
    warnings: list[str] = []
    if unsafe_records:
        warnings.append("timestamp-unsafe records are diagnostics-only")
    if missing:
        warnings.append("some requested symbols were not returned")

    return {
        "provider": PROVIDER,
        "symbols_requested": requested,
        "symbols_returned": returned,
        "missing_symbols": missing,
        "statement_types_present": sorted({record.statement_type for record in record_list}),
        "period_types_present": sorted({record.period_type for record in record_list}),
        "total_records": len(record_list),
        "timestamp_safe_records": len(safe_records),
        "timestamp_unsafe_records": len(unsafe_records),
        "records_with_acceptedDate": sum(1 for record in record_list if record.accepted_date),
        "records_with_filingDate_only": sum(1 for record in record_list if record.filing_date and not record.accepted_date),
        "records_missing_available_date": sum(1 for record in record_list if not record.available_date),
        "earliest_available_date": available_dates[0].isoformat() if available_dates else None,
        "latest_available_date": available_dates[-1].isoformat() if available_dates else None,
        "warnings": warnings,
    }


def _validate_supported_target(statement_type: str, period_type: str, source_endpoint: str) -> None:
    expected_endpoint = SUPPORTED_STATEMENT_TYPES.get(statement_type)
    endpoint_path = _endpoint_path(source_endpoint)
    if expected_endpoint is None or endpoint_path != expected_endpoint or period_type not in SUPPORTED_PERIOD_TYPES:
        raise ValueError("unsupported FMP core statement target")


def _endpoint_path(source_endpoint: str) -> str:
    return str(source_endpoint or "").split("?", 1)[0]


def _availability_fields(
    *,
    accepted_date: str | None,
    filing_date: str | None,
) -> tuple[str | None, bool, str, str]:
    if accepted_date:
        return accepted_date, True, "acceptedDate", "acceptedDate"
    if filing_date:
        return filing_date, True, "filingDate", "filingDate"
    return None, False, "missing", "unsafe_missing_accepted_or_filing_date"


def _ordered_symbols(symbols: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(str(symbol))
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _first_present(record: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _normalize_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0].strip().upper() if symbol else ""


def _fiscal_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _is_metadata_field(field_name: str) -> bool:
    return _metadata_key(field_name) in _METADATA_FIELDS


def _metadata_key(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", field_name.lower())


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _sanitize_url(url: str, api_key: str = "") -> str:
    if not url:
        return ""
    sanitized = re.sub(r"(?i)(apikey=)[^&]+", r"\1REDACTED", str(url))
    if api_key:
        sanitized = sanitized.replace(api_key, "REDACTED")
    return sanitized
