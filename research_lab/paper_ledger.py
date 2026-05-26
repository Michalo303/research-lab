from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.registry import append_jsonl
from research_lab.robustness import load_backtest_results


LEDGER_COLUMNS = [
    "date",
    "cash",
    "equity",
    "daily_pnl",
    "gross_exposure_pct",
    "strategy_count",
    "source",
]

POSITION_COLUMNS = [
    "as_of",
    "strategy_id",
    "family",
    "short_name",
    "symbol",
    "strategy_weight_pct",
    "symbol_weight_pct",
    "portfolio_weight_pct",
    "notional",
]


def run_paper_portfolio_ledger(
    root: Path,
    report_stem: str,
    portfolio_rows: list[dict[str, Any]],
    portfolio_equity: pd.Series,
    starting_equity: float = 100_000.0,
) -> dict[str, Any]:
    selected = [row for row in portfolio_rows if float(row.get("suggested_weight_pct", 0.0) or 0.0) > 0]
    total_weight = min(sum(float(row.get("suggested_weight_pct", 0.0) or 0.0) for row in selected) / 100.0, 1.0)
    ledger_rows = _ledger_rows(portfolio_equity, starting_equity, total_weight, len(selected))
    position_rows = _position_rows(load_backtest_results(root), selected, starting_equity)
    report_dir = root / "reports" / "paper"
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = report_dir / f"{report_stem}_paper_ledger.csv"
    positions_path = report_dir / f"{report_stem}_paper_positions.csv"
    _write_csv(ledger_path, ledger_rows, LEDGER_COLUMNS)
    _write_csv(positions_path, position_rows, POSITION_COLUMNS)
    if ledger_rows:
        latest = ledger_rows[-1]
        append_daily_paper_ledger(
            root,
            {
                "date": latest["date"],
                "cash": latest["cash"],
                "positions": position_rows,
                "target_weights": _target_weights_from_positions(position_rows),
                "equity": latest["equity"],
                "daily_pnl": latest["daily_pnl"],
                "cumulative_pnl": float(latest["equity"]) - starting_equity,
                "gross_exposure": float(latest["gross_exposure_pct"]) / 100.0,
                "net_exposure": float(latest["gross_exposure_pct"]) / 100.0,
                "source_strategy_ids": [str(row.get("strategy_id", "")) for row in selected],
                "latest_signals": _latest_signals(load_backtest_results(root), selected),
                "data_source": _portfolio_data_source(load_backtest_results(root), selected),
            },
        )
    return {"rows": ledger_rows, "positions": position_rows, "path": ledger_path, "positions_path": positions_path}


def append_daily_paper_ledger(root: Path, payload: dict[str, Any]) -> Path:
    required = [
        "date",
        "cash",
        "positions",
        "target_weights",
        "equity",
        "daily_pnl",
        "cumulative_pnl",
        "gross_exposure",
        "net_exposure",
        "source_strategy_ids",
        "latest_signals",
        "data_source",
    ]
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"Missing paper ledger fields: {', '.join(missing)}")
    path = root / "registry" / "paper_ledger_daily.jsonl"
    append_jsonl(path, {**payload, "research_only": True})
    return path


def summarize_paper_ledger(rows: list[dict[str, Any]], positions: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- paper ledger: no portfolio equity curve available"]
    latest = rows[-1]
    return [
        (
            "- paper ledger: "
            f"equity={float(latest['equity']):.2f}, cash={float(latest['cash']):.2f}, "
            f"positions={len(positions)}"
        )
    ]


def _ledger_rows(portfolio_equity: pd.Series, starting_equity: float, total_weight: float, strategy_count: int) -> list[dict[str, Any]]:
    if portfolio_equity.empty:
        return []
    scaled_equity = portfolio_equity * starting_equity
    daily_pnl = scaled_equity.diff().fillna(0.0)
    rows = []
    for ts, equity in scaled_equity.items():
        rows.append(
            {
                "date": _format_index_value(ts),
                "cash": float(equity) * max(0.0, 1.0 - total_weight),
                "equity": float(equity),
                "daily_pnl": float(daily_pnl.loc[ts]),
                "gross_exposure_pct": total_weight * 100.0,
                "strategy_count": strategy_count,
                "source": "model_portfolio_backtest_no_broker_orders",
            }
        )
    return rows


def _position_rows(results: list[dict[str, Any]], portfolio_rows: list[dict[str, Any]], equity: float) -> list[dict[str, Any]]:
    by_id = {item.get("strategy_id"): item for item in results}
    rows = []
    for portfolio_row in portfolio_rows:
        strategy_id = portfolio_row.get("strategy_id", "")
        item = by_id.get(strategy_id, {})
        signal = item.get("latest_signal") or {}
        strategy_weight = float(portfolio_row.get("suggested_weight_pct", 0.0) or 0.0)
        for symbol, symbol_weight in (signal.get("target_weights") or {}).items():
            symbol_weight_pct = float(symbol_weight) * 100.0
            portfolio_weight_pct = strategy_weight * float(symbol_weight)
            rows.append(
                {
                    "as_of": signal.get("as_of", ""),
                    "strategy_id": strategy_id,
                    "family": item.get("family", ""),
                    "short_name": item.get("short_name", ""),
                    "symbol": symbol,
                    "strategy_weight_pct": strategy_weight,
                    "symbol_weight_pct": symbol_weight_pct,
                    "portfolio_weight_pct": portfolio_weight_pct,
                    "notional": equity * portfolio_weight_pct / 100.0,
                }
            )
    return rows


def _target_weights_from_positions(position_rows: list[dict[str, Any]]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in position_rows:
        symbol = str(row.get("symbol", ""))
        weights[symbol] = weights.get(symbol, 0.0) + float(row.get("portfolio_weight_pct", 0.0) or 0.0) / 100.0
    return weights


def _latest_signals(results: list[dict[str, Any]], portfolio_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item.get("strategy_id"): item for item in results}
    signals = []
    for row in portfolio_rows:
        strategy_id = row.get("strategy_id", "")
        signal = by_id.get(strategy_id, {}).get("latest_signal") or {}
        signals.append({"strategy_id": strategy_id, **signal})
    return signals


def _portfolio_data_source(results: list[dict[str, Any]], portfolio_rows: list[dict[str, Any]]) -> str:
    by_id = {item.get("strategy_id"): item for item in results}
    sources = []
    for row in portfolio_rows:
        strategy_id = row.get("strategy_id", "")
        source = by_id.get(strategy_id, {}).get("data_source", by_id.get(strategy_id, {}).get("data_manifest", {}).get("source", ""))
        if source and source not in sources:
            sources.append(str(source))
    return ",".join(sources)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _format_index_value(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
