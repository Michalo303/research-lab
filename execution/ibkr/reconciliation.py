from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any


RECONCILIATION_COLUMNS = [
    "symbol",
    "target_quantity",
    "ibkr_quantity",
    "target_notional",
    "ibkr_notional",
    "notional_diff",
    "exposure_diff",
    "verdict",
]


def reconcile_paper_ledger_to_ibkr(
    root: Path,
    ibkr_snapshot: dict[str, Any],
    as_of: str | None = None,
    quantity_tolerance: float = 0.0001,
) -> dict[str, Any]:
    as_of = as_of or date.today().isoformat()
    ledger = _latest_ledger_row(root)
    equity = float(ledger.get("equity", 0.0) or 0.0)
    targets = _target_positions(ledger)
    ibkr_positions = _ibkr_positions(ibkr_snapshot)
    symbols = sorted(set(targets) | set(ibkr_positions))
    rows = []
    for symbol in symbols:
        target = targets.get(symbol, {"quantity": 0.0, "notional": 0.0})
        ibkr = ibkr_positions.get(symbol, {"quantity": 0.0, "notional": 0.0})
        target_quantity = float(target["quantity"])
        ibkr_quantity = float(ibkr["quantity"])
        target_notional = float(target["notional"])
        ibkr_notional = float(ibkr["notional"])
        notional_diff = ibkr_notional - target_notional
        rows.append(
            {
                "symbol": symbol,
                "target_quantity": target_quantity,
                "ibkr_quantity": ibkr_quantity,
                "target_notional": target_notional,
                "ibkr_notional": ibkr_notional,
                "notional_diff": notional_diff,
                "exposure_diff": notional_diff / equity if equity else 0.0,
                "verdict": _verdict(target_quantity, ibkr_quantity, quantity_tolerance),
            }
        )
    paths = _write_reconciliation(root, as_of, rows)
    return {"as_of": as_of, "rows": rows, **paths}


def _latest_ledger_row(root: Path) -> dict[str, Any]:
    path = root / "registry" / "paper_ledger_daily.jsonl"
    if not path.exists():
        return {"positions": [], "equity": 0.0}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-1] if rows else {"positions": [], "equity": 0.0}


def _target_positions(ledger: dict[str, Any]) -> dict[str, dict[str, float]]:
    positions: dict[str, dict[str, float]] = {}
    for item in ledger.get("positions", []):
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        positions.setdefault(symbol, {"quantity": 0.0, "notional": 0.0})
        positions[symbol]["quantity"] += float(item.get("quantity", item.get("target_quantity", 0.0)) or 0.0)
        positions[symbol]["notional"] += float(item.get("notional", 0.0) or 0.0)
    return positions


def _ibkr_positions(snapshot: dict[str, Any]) -> dict[str, dict[str, float]]:
    positions: dict[str, dict[str, float]] = {}
    for item in snapshot.get("positions", []):
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        quantity = float(item.get("position", item.get("quantity", 0.0)) or 0.0)
        avg_cost = float(item.get("avg_cost", item.get("avgCost", 0.0)) or 0.0)
        positions.setdefault(symbol, {"quantity": 0.0, "notional": 0.0})
        positions[symbol]["quantity"] += quantity
        positions[symbol]["notional"] += quantity * avg_cost
    return positions


def _verdict(target_quantity: float, ibkr_quantity: float, tolerance: float) -> str:
    if abs(target_quantity) <= tolerance and abs(ibkr_quantity) > tolerance:
        return "extra"
    if abs(target_quantity) > tolerance and abs(ibkr_quantity) <= tolerance:
        return "missing"
    if abs(target_quantity - ibkr_quantity) <= tolerance:
        return "match"
    return "diff"


def _write_reconciliation(root: Path, as_of: str, rows: list[dict[str, Any]]) -> dict[str, Path]:
    report_dir = root / "reports" / "execution"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f"ibkr_reconciliation_{as_of}.csv"
    json_path = report_dir / f"ibkr_reconciliation_{as_of}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECONCILIATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps({"as_of": as_of, "rows": rows}, indent=2), encoding="utf-8")
    return {"csv_path": csv_path, "json_path": json_path}
