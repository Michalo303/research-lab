from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


EODHD_VALIDATION_UNIVERSE = [
    "SPY.US",
    "QQQ.US",
    "IWM.US",
    "TLT.US",
    "GLD.US",
    "XLK.US",
    "XLF.US",
    "XLE.US",
    "SMH.US",
    "SOXX.US",
]

TARGET_VALIDATION_STRATEGIES = [
    "ROTATION_ETF_1D_QUEUE_MOM_DD",
    "ROTATION_ETF_1D_DUAL_MOMENTUM",
    "LONGTERM_ETF_1D_QUEUE_VOL_TARGET",
]

QUALITY_FIELDS = [
    "symbol",
    "provider",
    "coverage_status",
    "row_count",
    "first_date",
    "last_date",
    "coverage_years",
    "duplicate_dates",
    "missing_weekdays",
    "non_monotonic_index",
    "zero_price_rows",
    "negative_price_rows",
    "nan_ohlcv_rows",
    "extreme_return_rows",
    "adjusted_status",
    "research_only",
    "not_trading_signal",
]

MANIFEST_FIELDS = [
    "symbol",
    "provider",
    "coverage_status",
    "row_count",
    "first_date",
    "last_date",
    "coverage_years",
    "adjusted_status",
    "created_at",
    "research_only",
    "not_trading_signal",
]


def audit_history_rows(symbol: str, rows: list[dict], adjusted: bool = True, extreme_return_threshold: float = 0.25) -> dict:
    if not rows:
        return _empty_audit(symbol, adjusted)

    frame = pd.DataFrame(rows)
    if "date" not in frame.columns:
        return _empty_audit(symbol, adjusted, coverage_status="error")

    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    duplicate_dates = int(parsed_dates.duplicated().sum())
    non_monotonic = bool(parsed_dates.dropna().is_monotonic_increasing is False)

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        if col not in frame.columns:
            frame[col] = pd.NA
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    valid = frame.assign(_date=parsed_dates).dropna(subset=["_date"]).sort_values("_date")
    unique = valid.drop_duplicates("_date", keep="last").set_index("_date")
    first = unique.index.min() if len(unique) else pd.NaT
    last = unique.index.max() if len(unique) else pd.NaT
    missing_weekdays = _missing_weekdays(unique.index) if len(unique) else 0
    ohlc = frame[["open", "high", "low", "close"]]
    zero_price_rows = int((ohlc == 0).any(axis=1).sum())
    negative_price_rows = int((ohlc < 0).any(axis=1).sum())
    nan_ohlcv_rows = int(frame[numeric_cols].isna().any(axis=1).sum())
    returns = unique["close"].pct_change().abs()
    extreme_return_rows = int((returns > extreme_return_threshold).sum())
    adjusted_status = _adjusted_status(frame, adjusted)

    coverage_years = calculate_history_length_years(first, last)
    status = "available"
    tolerated_calendar_gaps = int(max(0, coverage_years) * 12) if coverage_years >= 1.0 else 0
    missing_dates_exceed_tolerance = missing_weekdays > tolerated_calendar_gaps
    if any([duplicate_dates, missing_dates_exceed_tolerance, non_monotonic, zero_price_rows, negative_price_rows, nan_ohlcv_rows]):
        status = "partial"
    if len(unique) == 0:
        status = "missing"

    return {
        "symbol": symbol,
        "provider": "eodhd",
        "coverage_status": status,
        "row_count": int(len(unique)),
        "first_date": _date_or_blank(first),
        "last_date": _date_or_blank(last),
        "coverage_years": round(coverage_years, 2),
        "duplicate_dates": duplicate_dates,
        "missing_weekdays": missing_weekdays,
        "non_monotonic_index": non_monotonic,
        "zero_price_rows": zero_price_rows,
        "negative_price_rows": negative_price_rows,
        "nan_ohlcv_rows": nan_ohlcv_rows,
        "extreme_return_rows": extreme_return_rows,
        "adjusted_status": adjusted_status,
        "research_only": True,
        "not_trading_signal": True,
    }


def calculate_history_length_years(first_date, last_date) -> float:
    if pd.isna(first_date) or pd.isna(last_date):
        return 0.0
    start = pd.Timestamp(first_date)
    end = pd.Timestamp(last_date)
    return max((end - start).days / 365.25, 0.0)


def write_quality_outputs(root: Path, audits: list[dict]) -> dict:
    registry = root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    quality_path = registry / "eodhd_data_quality.csv"
    manifest_path = registry / "eodhd_history_manifest.csv"
    created_at = datetime.now(timezone.utc).isoformat()

    quality_rows = [_with_fields(row, QUALITY_FIELDS) for row in audits]
    manifest_rows = [
        _with_fields({**row, "created_at": created_at}, MANIFEST_FIELDS)
        for row in audits
    ]
    _write_csv(quality_path, quality_rows, QUALITY_FIELDS)
    _write_csv(manifest_path, manifest_rows, MANIFEST_FIELDS)
    return {"quality_path": str(quality_path), "manifest_path": str(manifest_path)}


def write_vendor_reports(root: Path, audits: list[dict], massive_manifest: dict | None = None) -> dict:
    out_dir = root / "reports" / "vendor_reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / "eodhd_vs_massive.md"
    summary_path = out_dir / "eodhd_history_summary.md"
    massive_manifest = massive_manifest or _read_json(root / "data" / "manifests" / "daily_universe.json")

    comparison_path.write_text(_comparison_report(audits, massive_manifest), encoding="utf-8")
    summary_path.write_text(_history_summary(audits), encoding="utf-8")
    return {"comparison_path": str(comparison_path), "summary_path": str(summary_path)}


def _empty_audit(symbol: str, adjusted: bool, coverage_status: str = "missing") -> dict:
    return {
        "symbol": symbol,
        "provider": "eodhd",
        "coverage_status": coverage_status,
        "row_count": 0,
        "first_date": "",
        "last_date": "",
        "coverage_years": 0.0,
        "duplicate_dates": 0,
        "missing_weekdays": 0,
        "non_monotonic_index": False,
        "zero_price_rows": 0,
        "negative_price_rows": 0,
        "nan_ohlcv_rows": 0,
        "extreme_return_rows": 0,
        "adjusted_status": "adjusted_requested_missing" if adjusted else "raw",
        "research_only": True,
        "not_trading_signal": True,
    }


def _missing_weekdays(index: pd.DatetimeIndex) -> int:
    if len(index) < 2:
        return 0
    expected = pd.bdate_range(index.min(), index.max())
    return int(len(expected.difference(pd.DatetimeIndex(index.normalize()).unique())))


def _adjusted_status(frame: pd.DataFrame, adjusted: bool) -> str:
    if not adjusted:
        return "raw"
    if "adjusted_close" not in frame.columns:
        return "adjusted_requested_missing"
    adjusted_close = pd.to_numeric(frame["adjusted_close"], errors="coerce")
    return "adjusted" if adjusted_close.notna().any() else "adjusted_requested_missing"


def _date_or_blank(value) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).date().isoformat()


def _with_fields(row: dict, fields: list[str]) -> dict:
    return {field: row.get(field, "") for field in fields}


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _comparison_report(audits: list[dict], massive_manifest: dict) -> str:
    eodhd_available = [row for row in audits if row.get("coverage_status") in {"available", "partial"}]
    min_years = min((float(row.get("coverage_years", 0.0)) for row in eodhd_available), default=0.0)
    max_years = max((float(row.get("coverage_years", 0.0)) for row in eodhd_available), default=0.0)
    massive_years = float(massive_manifest.get("years", 0.0) or 0.0)
    massive_rows = int(massive_manifest.get("rows", 0) or 0)
    lines = [
        "# EODHD vs Massive Historical Data Review",
        "",
        "Research-only vendor review. This report is factual and does not create trading permission.",
        "",
        "## Coverage Snapshot",
        "",
        f"- EODHD symbols audited: {len(audits)}",
        f"- EODHD available/partial symbols: {len(eodhd_available)}",
        f"- EODHD history range across audited symbols: {min_years:.2f} to {max_years:.2f} years",
        f"- Massive current manifest source: {massive_manifest.get('source', 'missing')}",
        f"- Massive current manifest rows: {massive_rows}",
        f"- Massive current manifest years: {massive_years:.2f}",
        "",
        "## Per-Symbol Quality",
        "",
        "Missing weekdays are an approximate calendar-gap count and can include exchange holidays; status is only downgraded when gaps exceed the built-in long-history tolerance or other quality checks fail.",
        "",
        "| Symbol | Status | Rows | First | Last | Years | Missing weekdays | Extreme returns | Adjusted |",
        "|---|---:|---:|---|---|---:|---:|---:|---|",
    ]
    for row in audits:
        lines.append(
            "| {symbol} | {coverage_status} | {row_count} | {first_date} | {last_date} | {coverage_years} | "
            "{missing_weekdays} | {extreme_return_rows} | {adjusted_status} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Strategy Validation Scope",
            "",
            "Target strategies for longer-history validation:",
            *[f"- {strategy}" for strategy in TARGET_VALIDATION_STRATEGIES],
            "",
            "The scaffold validates EODHD history availability and quality before strategy promotion decisions. It does not modify paper/live execution, broker code, or deployment gates.",
        ]
    )
    return "\n".join(lines) + "\n"


def _history_summary(audits: list[dict]) -> str:
    lines = [
        "# EODHD Historical Validation Summary",
        "",
        "Primary question: do the best rotation/momentum strategies have enough validated history to test beyond the current Massive window?",
        "",
        "| Symbol | Status | Rows | First date | Last date | Coverage years |",
        "|---|---:|---:|---|---|---:|",
    ]
    for row in audits:
        lines.append(
            f"| {row['symbol']} | {row['coverage_status']} | {row['row_count']} | {row['first_date']} | {row['last_date']} | {row['coverage_years']} |"
        )
    enough = [row for row in audits if float(row.get("coverage_years", 0.0)) >= 10.0]
    lines.extend(
        [
            "",
            f"Symbols with at least 10 years of EODHD history: {len(enough)} / {len(audits)}",
            "",
            "Research-only status: no trading signal, no paper/live change, no broker integration, no deployment-gate change.",
        ]
    )
    return "\n".join(lines) + "\n"
