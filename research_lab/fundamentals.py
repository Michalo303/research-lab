from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FUNDAMENTAL_COLUMNS = ["ticker", "coverage_status", "valuation_json", "quality_json", "debt_json", "growth_json", "source"]
VALUATION_FIELDS = ["pe", "ev_ebitda", "price_to_sales", "fcf_yield"]
QUALITY_FIELDS = ["roe", "roic", "gross_margin", "operating_margin"]
DEBT_FIELDS = ["debt_to_ebitda", "net_debt_to_market_cap", "interest_coverage"]
GROWTH_FIELDS = ["revenue_growth", "eps_growth", "fcf_growth"]

AVAILABILITY_DATE_FIELDS = ("available_date", "asof_date", "accepted_date", "filing_date")


@dataclass(frozen=True)
class FundamentalRow:
    symbol: str = ""
    provider: str = ""
    statement_type: str = ""
    fiscal_period: str = ""
    fiscal_year: int | None = None
    period_end_date: str | date | datetime | None = None
    filing_date: str | date | datetime | None = None
    accepted_date: str | date | datetime | None = None
    available_date: str | date | datetime | None = None
    asof_date: str | date | datetime | None = None
    field_name: str = ""
    value: int | float | str | None = None
    currency: str = ""
    source_url: str = ""
    provider_record_id: str = ""
    ingestion_timestamp: str | date | datetime | None = None


def is_timestamp_safe(row: FundamentalRow | dict[str, Any]) -> bool:
    return availability_date(row) is not None


def availability_date(row: FundamentalRow | dict[str, Any]) -> date | None:
    for field in AVAILABILITY_DATE_FIELDS:
        parsed = _parse_date(_row_value(row, field))
        if parsed is not None:
            return parsed
    return None


def reject_timestamp_unsafe_rows(
    rows: Iterable[FundamentalRow | dict[str, Any]],
) -> tuple[list[FundamentalRow | dict[str, Any]], list[dict[str, str]]]:
    accepted: list[FundamentalRow | dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for row in rows:
        if is_timestamp_safe(row):
            accepted.append(row)
            continue
        rejected.append(
            {
                "symbol": str(_row_value(row, "symbol") or ""),
                "provider": str(_row_value(row, "provider") or ""),
                "field_name": str(_row_value(row, "field_name") or ""),
                "reason": "missing explicit filing/accepted/available/as-of date",
            }
        )
    return accepted, rejected


def filter_fundamentals_as_of(
    rows: Iterable[FundamentalRow],
    as_of_date: str | date | datetime,
) -> list[FundamentalRow]:
    cutoff = _parse_date(as_of_date)
    if cutoff is None:
        raise ValueError(f"Invalid as_of_date: {as_of_date!r}")
    eligible = [row for row in rows if (anchor := availability_date(row)) is not None and anchor <= cutoff]
    return dedupe_fundamental_rows(eligible)


def dedupe_fundamental_rows(rows: Iterable[FundamentalRow]) -> list[FundamentalRow]:
    grouped: dict[tuple[str, ...], list[FundamentalRow]] = {}
    for row in rows:
        grouped.setdefault(_dedupe_key(row), []).append(row)
    selected = [max(candidates, key=_dedupe_sort_key) for candidates in grouped.values()]
    return sorted(selected, key=_dedupe_key)


def fundamental_coverage_rows(
    candidates: list[dict[str, Any]],
    fundamentals_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    fundamentals_by_ticker = fundamentals_by_ticker or {}
    rows = []
    seen: set[str] = set()
    for candidate in candidates:
        ticker = str(candidate.get("ticker", "")).strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        data = fundamentals_by_ticker.get(ticker)
        if not data:
            rows.append({"ticker": ticker, "coverage_status": "missing", "valuation": {}, "quality": {}, "debt": {}, "growth": {}, "source": ""})
            continue
        rows.append(
            {
                "ticker": ticker,
                "coverage_status": "present",
                "valuation": _pick(data, VALUATION_FIELDS),
                "quality": _pick(data, QUALITY_FIELDS),
                "debt": _pick(data, DEBT_FIELDS),
                "growth": _pick(data, GROWTH_FIELDS),
                "source": str(data.get("source", "")),
            }
        )
    return rows


def enrich_smartmoney_fundamentals(
    root: Path,
    report_stem: str,
    fundamentals_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidates = _read_smartmoney_candidates(root / "registry" / "hypothesis_queue.jsonl")
    rows = fundamental_coverage_rows(candidates, fundamentals_by_ticker)
    csv_path = root / "registry" / "fundamentals_coverage.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(csv_path, rows)
    report_path = root / "reports" / "weekly" / f"{report_stem}_fundamentals.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, rows)
    return {"rows": rows, "csv_path": csv_path, "report_path": report_path}


def _read_smartmoney_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        tags = item.get("tags") or []
        if "smart_money" in tags or item.get("smartmoney") or item.get("apify_dataroma"):
            rows.append(item)
    return rows


def _pick(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data and data[field] is not None and data[field] != ""}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FUNDAMENTAL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "ticker": row["ticker"],
                    "coverage_status": row["coverage_status"],
                    "valuation_json": json.dumps(row["valuation"], sort_keys=True),
                    "quality_json": json.dumps(row["quality"], sort_keys=True),
                    "debt_json": json.dumps(row["debt"], sort_keys=True),
                    "growth_json": json.dumps(row["growth"], sort_keys=True),
                    "source": row.get("source", ""),
                }
            )


def _write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    missing = sum(1 for row in rows if row["coverage_status"] == "missing")
    lines = [
        "# Fundamentals Coverage",
        "",
        f"- smart-money tickers reviewed: {len(rows)}",
        f"- missing fundamentals coverage: {missing}",
        "- schema only: valuation, quality, debt, growth",
        "- no fake fundamental defaults are created",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _row_value(row: FundamentalRow | dict[str, Any], field: str) -> Any:
    if isinstance(row, dict):
        if field == "asof_date":
            return row.get("asof_date") or row.get("as_of_date")
        return row.get(field)
    return getattr(row, field)


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


def _parse_datetime(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _dedupe_key(row: FundamentalRow) -> tuple[str, ...]:
    period_end = _parse_date(row.period_end_date)
    return (
        row.symbol.upper(),
        row.provider.lower(),
        row.statement_type.lower(),
        row.fiscal_period.upper(),
        "" if row.fiscal_year is None else str(row.fiscal_year),
        period_end.isoformat() if period_end else "",
        row.field_name.lower(),
        row.currency.upper(),
    )


def _dedupe_sort_key(row: FundamentalRow) -> tuple[date, datetime, str, str]:
    anchor = availability_date(row) or date.min
    ingestion = _parse_datetime(row.ingestion_timestamp)
    provider_record = row.provider_record_id or ""
    source_url = row.source_url or ""
    return (anchor, ingestion, provider_record, source_url)
