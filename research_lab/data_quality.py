from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


DATA_QUALITY_COLUMNS = ["dataset", "symbol", "check", "status", "value", "threshold", "details"]
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def audit_ohlcv_panel(panel: pd.DataFrame, manifest: dict[str, Any], required_symbols: list[str] | None = None) -> list[dict[str, Any]]:
    dataset = str(manifest.get("name", "daily_universe"))
    symbols = _symbols(panel)
    rows: list[dict[str, Any]] = []
    index = pd.DatetimeIndex(panel.index)
    rows.append(_row(dataset, "*", "duplicate_dates", not index.duplicated().any(), int(index.duplicated().sum()), 0, "Duplicate timestamps in OHLCV index."))
    rows.append(_row(dataset, "*", "non_monotonic_index", index.is_monotonic_increasing, int(not index.is_monotonic_increasing), 0, "Index should be sorted ascending."))
    missing_bars = _missing_business_days(index)
    rows.append(_row(dataset, "*", "missing_bars", missing_bars == 0, missing_bars, 0, "Business-day gaps between first and last bar."))
    for symbol in symbols:
        frame = _symbol_frame(panel, symbol)
        missing_cols = [column for column in OHLCV_COLUMNS if column not in frame.columns]
        rows.append(_row(dataset, symbol, "missing_ohlcv_columns", not missing_cols, len(missing_cols), 0, ",".join(missing_cols)))
        if missing_cols:
            continue
        nan_count = int(frame[OHLCV_COLUMNS].isna().sum().sum())
        rows.append(_row(dataset, symbol, "missing_ohlcv_nan", nan_count == 0, nan_count, 0, "NaN count across OHLCV."))
        price_bad = int((frame[["open", "high", "low", "close"]] <= 0).sum().sum())
        rows.append(_row(dataset, symbol, "zero_or_negative_prices", price_bad == 0, price_bad, 0, "Open/high/low/close must be positive."))
        volume_bad = int((frame["volume"] <= 0).sum())
        rows.append(_row(dataset, symbol, "zero_or_negative_volume", volume_bad == 0, volume_bad, 0, "Volume should be positive for EOD bars."))
        returns = frame["close"].pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
        extreme = int((returns.abs() > 0.50).sum())
        rows.append(_row(dataset, symbol, "extreme_returns", extreme == 0, extreme, "abs(return)<=50%", "Potential split/corporate-action or bad-tick issue."))
    required = set(required_symbols or manifest.get("symbols") or [])
    missing_symbols = sorted(required - set(symbols))
    rows.append(_row(dataset, "*", "symbol_coverage", not missing_symbols, len(missing_symbols), 0, ",".join(missing_symbols)))
    adjusted = manifest.get("adjusted", "unknown")
    rows.append(_row(dataset, "*", "adjustment_assumption", adjusted is not None and adjusted != "unknown", str(adjusted), "known", "Split/dividend adjustment assumption from manifest."))
    return rows


def run_data_quality_audit(root: Path, report_stem: str) -> dict[str, Any]:
    manifest_path = root / "data" / "manifests" / "daily_universe.json"
    rows: list[dict[str, Any]] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        csv_path = _manifest_csv_path(root, manifest)
        if csv_path is not None and csv_path.exists():
            panel = _read_processed_panel(csv_path)
            rows = audit_ohlcv_panel(panel, manifest, required_symbols=list(manifest.get("symbols") or []))
    registry_path = root / "registry" / "data_quality_audit.csv"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(registry_path, rows)
    report_path = root / "reports" / "weekly" / f"{report_stem}_data_quality.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, rows)
    return {"rows": rows, "csv_path": registry_path, "report_path": report_path}


def _manifest_csv_path(root: Path, manifest: dict[str, Any]) -> Path | None:
    stored_csv = str(manifest.get("stored_csv") or "").strip()
    if stored_csv:
        path = Path(stored_csv)
        return path if path.is_absolute() else root / path
    source = str(manifest.get("source") or "").strip().lower()
    if source in {"eodhd", "massive"}:
        return root / "data" / "processed" / f"{source}_daily_universe.csv"
    return None


def _read_processed_panel(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    tuples = []
    for column in frame.columns:
        if "." in column:
            symbol, field = column.split(".", 1)
            tuples.append((symbol, field))
        else:
            tuples.append(("", column))
    frame.columns = pd.MultiIndex.from_tuples(tuples)
    return frame


def _symbols(panel: pd.DataFrame) -> list[str]:
    if isinstance(panel.columns, pd.MultiIndex):
        return sorted(str(symbol) for symbol in panel.columns.get_level_values(0).unique() if str(symbol))
    return [""]


def _symbol_frame(panel: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(panel.columns, pd.MultiIndex):
        frame = panel.xs(symbol, axis=1, level=0)
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    frame = panel.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    return frame


def _missing_business_days(index: pd.DatetimeIndex) -> int:
    if index.empty:
        return 0
    unique = pd.DatetimeIndex(sorted(set(index.normalize())))
    expected = pd.bdate_range(unique.min(), unique.max())
    return int(len(expected.difference(unique)))


def _row(dataset: str, symbol: str, check: str, passed: bool, value: Any, threshold: Any, details: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "symbol": symbol,
        "check": check,
        "status": "pass" if passed else "fail",
        "value": value,
        "threshold": threshold,
        "details": details,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATA_QUALITY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in DATA_QUALITY_COLUMNS})


def _write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    failures = [row for row in rows if row.get("status") == "fail"]
    lines = [
        "# Data Quality Audit",
        "",
        f"- checks: {len(rows)}",
        f"- failures: {len(failures)}",
        "- scope: report-only; does not change paper/deployment gates",
        "",
    ]
    for row in failures[:50]:
        lines.append(f"- {row['dataset']} {row['symbol']} {row['check']}: {row['details']} value={row['value']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
