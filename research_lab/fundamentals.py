from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


FUNDAMENTAL_COLUMNS = ["ticker", "coverage_status", "valuation_json", "quality_json", "debt_json", "growth_json", "source"]
VALUATION_FIELDS = ["pe", "ev_ebitda", "price_to_sales", "fcf_yield"]
QUALITY_FIELDS = ["roe", "roic", "gross_margin", "operating_margin"]
DEBT_FIELDS = ["debt_to_ebitda", "net_debt_to_market_cap", "interest_coverage"]
GROWTH_FIELDS = ["revenue_growth", "eps_growth", "fcf_growth"]


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
