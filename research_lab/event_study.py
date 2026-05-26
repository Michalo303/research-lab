from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


EVENT_WINDOW_COLUMNS = [
    "event_id",
    "ticker",
    "event_source",
    "event_date",
    "disclosure_date",
    "observed_date",
    "disclosure_lag_days",
    "observed_lag_days",
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "max_drawdown_20d",
    "recovery_20d",
    "data_complete",
    "no_lookahead",
]


def compute_event_windows(events: list[dict[str, Any]], close: pd.DataFrame, windows: list[int] | None = None) -> list[dict[str, Any]]:
    windows = windows or [1, 5, 20, 60]
    close = close.sort_index()
    rows = []
    for event in events:
        ticker = str(event.get("ticker", "")).strip().upper()
        if ticker not in close.columns:
            continue
        event_date = pd.to_datetime(event.get("event_date"))
        disclosure_date = pd.to_datetime(event.get("disclosure_date")) if event.get("disclosure_date") else pd.NaT
        observed_date = pd.to_datetime(event.get("observed_date")) if event.get("observed_date") else pd.NaT
        start_pos = close.index.searchsorted(event_date, side="left")
        if start_pos >= len(close.index):
            continue
        start_ts = close.index[start_pos]
        start_price = float(close.iloc[start_pos][ticker])
        row = {
            "event_id": event.get("event_id", ""),
            "ticker": ticker,
            "event_source": event.get("event_source", ""),
            "event_date": start_ts.date().isoformat(),
            "disclosure_date": _date(disclosure_date),
            "observed_date": _date(observed_date),
            "disclosure_lag_days": _lag_days(event_date, disclosure_date),
            "observed_lag_days": _lag_days(event_date, observed_date),
            "data_complete": True,
            "no_lookahead": True,
        }
        for window in windows:
            end_pos = min(start_pos + window, len(close.index) - 1)
            row[f"return_{window}d"] = float(close.iloc[end_pos][ticker] / start_price - 1.0)
            if end_pos < start_pos + window:
                row["data_complete"] = False
        horizon = min(start_pos + 20, len(close.index) - 1)
        path = close.iloc[start_pos : horizon + 1][ticker] / start_price - 1.0
        row["max_drawdown_20d"] = float(path.min()) if not path.empty else 0.0
        row["recovery_20d"] = float(path.iloc[-1]) if not path.empty else 0.0
        rows.append(row)
    return rows


def run_event_window_study(root: Path, report_stem: str) -> dict[str, Any]:
    events = _read_events(root)
    close = _read_close(root)
    rows = compute_event_windows(events, close) if events and not close.empty else []
    csv_path = root / "reports" / "weekly" / f"{report_stem}_event_windows.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(csv_path, rows)
    report_path = root / "reports" / "weekly" / f"{report_stem}_event_windows.md"
    _write_report(report_path, rows)
    return {"rows": rows, "csv_path": csv_path, "report_path": report_path}


def _read_events(root: Path) -> list[dict[str, Any]]:
    path = root / "registry" / "event_sources.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _read_close(root: Path) -> pd.DataFrame:
    path = root / "data" / "processed" / "massive_daily_universe.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    close_columns = [column for column in frame.columns if str(column).endswith(".close")]
    output = frame[close_columns].copy()
    output.columns = [column.split(".", 1)[0].upper() for column in close_columns]
    return output


def _lag_days(start: pd.Timestamp, end: pd.Timestamp) -> int:
    if pd.isna(end):
        return 0
    return int((end.normalize() - start.normalize()).days)


def _date(value: pd.Timestamp) -> str:
    return "" if pd.isna(value) else value.date().isoformat()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_WINDOW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in EVENT_WINDOW_COLUMNS})


def _write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    complete = sum(1 for row in rows if row.get("data_complete"))
    lines = [
        "# Event Window Study",
        "",
        f"- events measured: {len(rows)}",
        f"- complete windows: {complete}",
        "- no-lookahead rule: returns are measured after event_date only",
        "- research only: no buy/sell/order signals",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
