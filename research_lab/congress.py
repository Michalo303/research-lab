from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


CONGRESS_COLUMNS = [
    "representative",
    "ticker",
    "transaction_type",
    "amount_range",
    "transaction_date",
    "disclosure_date",
    "disclosure_lag_days",
    "source_url",
    "amount_range_valid",
    "research_only",
    "event_source_only",
    "not_trading_signal",
]


def normalize_congress_event(item: dict[str, Any]) -> dict[str, Any]:
    transaction_date = str(item.get("transaction_date", "") or "")
    disclosure_date = str(item.get("disclosure_date", "") or "")
    return {
        "representative": str(item.get("representative") or item.get("representativeName") or "").strip(),
        "ticker": str(item.get("ticker") or item.get("symbol") or "").strip().upper(),
        "transaction_type": str(item.get("transaction_type") or item.get("transactionType") or "").strip(),
        "amount_range": str(item.get("amount_range") or item.get("amountRange") or "").strip(),
        "transaction_date": transaction_date,
        "disclosure_date": disclosure_date,
        "disclosure_lag_days": _lag_days(transaction_date, disclosure_date),
        "source_url": str(item.get("source_url") or item.get("sourceUrl") or "").strip(),
        "amount_range_valid": _valid_amount_range(str(item.get("amount_range") or item.get("amountRange") or "")),
        "research_only": True,
        "event_source_only": True,
        "not_trading_signal": True,
    }


def import_congress_disclosures(root: Path, source_path: Path, report_stem: str, limit: int = 100) -> dict[str, Any]:
    raw = _read_source(source_path)[:limit]
    rows = [normalize_congress_event(item) for item in raw]
    events_path = root / "registry" / "congress_events.csv"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(events_path, rows, CONGRESS_COLUMNS)
    summary = _quality_summary(rows)
    quality_path = root / "reports" / "weekly" / f"{report_stem}_congress_quality.md"
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    _write_quality_report(quality_path, summary)
    return {"rows": rows, "summary": summary, "events_path": events_path, "quality_path": quality_path}


def _read_source(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else payload.get("items", [])
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = set()
    duplicates = 0
    lags = []
    for row in rows:
        key = (row["representative"], row["ticker"], row["transaction_type"], row["transaction_date"], row["disclosure_date"])
        if key in keys:
            duplicates += 1
        keys.add(key)
        if row["disclosure_lag_days"]:
            lags.append(int(row["disclosure_lag_days"]))
    return {
        "event_count": len(rows),
        "ticker_coverage": sum(1 for row in rows if row["ticker"]),
        "missing_dates": sum(1 for row in rows if not row["transaction_date"] or not row["disclosure_date"]),
        "average_disclosure_lag_days": float(sum(lags) / len(lags)) if lags else 0.0,
        "duplicate_events": duplicates,
        "malformed_amount_ranges": sum(1 for row in rows if not row["amount_range_valid"]),
        "research_only": True,
        "event_source_only": True,
    }


def _valid_amount_range(value: str) -> bool:
    return bool(re.search(r"\$[\d,]+", value) and "-" in value)


def _lag_days(transaction_date: str, disclosure_date: str) -> int:
    if not transaction_date or not disclosure_date:
        return 0
    start = pd.to_datetime(transaction_date)
    end = pd.to_datetime(disclosure_date)
    return int((end.normalize() - start.normalize()).days)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_quality_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Congress Disclosure Pilot Quality",
        "",
        f"- events: {summary['event_count']}",
        f"- ticker coverage: {summary['ticker_coverage']}",
        f"- missing dates: {summary['missing_dates']}",
        f"- average disclosure lag days: {summary['average_disclosure_lag_days']:.2f}",
        f"- duplicate events: {summary['duplicate_events']}",
        f"- malformed amount ranges: {summary['malformed_amount_ranges']}",
        "- event_source_only: true",
        "- not a trading signal",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
