from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any


COST_COLUMNS = [
    "category",
    "unit",
    "quantity",
    "estimated_cost_usd",
    "pricing_source",
    "notes",
]


def run_research_cost_monitor(root: Path, report_stem: str) -> dict[str, Any]:
    rows = [
        _apify_row(root),
        _market_data_row(root),
        _hypothesis_queue_row(root),
    ]
    total = sum(float(row["estimated_cost_usd"]) for row in rows)
    rows.append(
        {
            "category": "total",
            "unit": "usd",
            "quantity": "",
            "estimated_cost_usd": round(total, 6),
            "pricing_source": "configured_estimates",
            "notes": "Costs are estimates from local usage counts and env-configured unit prices.",
        }
    )
    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report_stem}_research_costs.csv"
    _write_csv(path, rows)
    return {"rows": rows, "path": path, "total_estimated_cost_usd": total}


def summarize_research_costs(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- research cost monitor: no usage rows"]
    total = next((row for row in rows if row["category"] == "total"), None)
    apify = next((row for row in rows if row["category"] == "apify_dataroma"), None)
    market = next((row for row in rows if row["category"] == "market_data"), None)
    return [
        (
            "- research cost estimate: "
            f"${float(total['estimated_cost_usd'] if total else 0.0):.4f} "
            f"(apify_rows={int(float(apify['quantity'])) if apify else 0}, "
            f"market_units={int(float(market['quantity'])) if market else 0})"
        )
    ]


def _apify_row(root: Path) -> dict[str, Any]:
    rows = 0
    for path in (root / "data" / "processed" / "apify_dataroma").glob("holdings_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows += int(payload.get("item_count", 0) or 0)
    price_per_1000 = float(os.getenv("RESEARCH_COST_APIFY_DOLLARS_PER_1000_ROWS", "0"))
    return {
        "category": "apify_dataroma",
        "unit": "rows",
        "quantity": rows,
        "estimated_cost_usd": round((rows / 1000.0) * price_per_1000, 6),
        "pricing_source": "RESEARCH_COST_APIFY_DOLLARS_PER_1000_ROWS",
        "notes": "Counts local Apify Dataroma processed holdings payloads.",
    }


def _market_data_row(root: Path) -> dict[str, Any]:
    manifests = []
    for path in (root / "data" / "manifests").glob("*.json"):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    massive_units = 0
    yfinance_units = 0
    synthetic_units = 0
    for manifest in manifests:
        symbols = manifest.get("symbols") or []
        units = len(symbols) if isinstance(symbols, list) else 1
        source = manifest.get("source")
        if source == "massive":
            massive_units += units
        elif source == "yfinance":
            yfinance_units += units
        elif source == "synthetic":
            synthetic_units += units
    price_per_unit = float(os.getenv("RESEARCH_COST_MARKET_DATA_DOLLARS_PER_SYMBOL_REFRESH", "0"))
    paid_units = massive_units
    return {
        "category": "market_data",
        "unit": "symbol_refreshes",
        "quantity": paid_units,
        "estimated_cost_usd": round(paid_units * price_per_unit, 6),
        "pricing_source": "RESEARCH_COST_MARKET_DATA_DOLLARS_PER_SYMBOL_REFRESH",
        "notes": f"latest manifests by source: massive={massive_units}, yfinance={yfinance_units}, synthetic={synthetic_units}",
    }


def _hypothesis_queue_row(root: Path) -> dict[str, Any]:
    path = root / "registry" / "hypothesis_queue.jsonl"
    count = 0
    if path.exists():
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "category": "hypothesis_queue",
        "unit": "queued_items",
        "quantity": count,
        "estimated_cost_usd": 0.0,
        "pricing_source": "n/a",
        "notes": "Queue size is tracked as a research-noise proxy, not a direct vendor charge.",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COST_COLUMNS})
