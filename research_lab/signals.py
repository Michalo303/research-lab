from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from research_lab.robustness import load_backtest_results


SIGNAL_COLUMNS = [
    "strategy_id",
    "family",
    "short_name",
    "tier",
    "as_of",
    "symbol",
    "action",
    "from_weight",
    "to_weight",
    "delta",
]


def run_signal_generation(root: Path) -> dict[str, Any]:
    rows = []
    for item in load_backtest_results(root):
        if item.get("tier") == "Rejected":
            continue
        signal = item.get("latest_signal") or {}
        for action in signal.get("actions", []):
            rows.append(
                {
                    "strategy_id": item.get("strategy_id", ""),
                    "family": item.get("family", ""),
                    "short_name": item.get("short_name", ""),
                    "tier": item.get("tier", ""),
                    "as_of": signal.get("as_of", ""),
                    "symbol": action.get("symbol", ""),
                    "action": action.get("action", ""),
                    "from_weight": action.get("from_weight", 0.0),
                    "to_weight": action.get("to_weight", 0.0),
                    "delta": action.get("delta", 0.0),
                }
            )
    path = root / "reports" / "signals" / "latest_signals.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, rows)
    return {"rows": rows, "path": path}


def summarize_signals(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- signal generator: no eligible strategy signals"]
    actionable = [row for row in rows if row["action"] in {"buy", "sell"}]
    return [
        f"- signal rows generated: {len(rows)}",
        f"- actionable buy/sell rows: {len(actionable)}",
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIGNAL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SIGNAL_COLUMNS})
