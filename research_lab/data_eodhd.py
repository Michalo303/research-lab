from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class EODHDCoverageRow:
    ticker: str
    start_date: str
    end_date: str
    row_count: int
    coverage_years: float
    missing_rows: int
    gap_days: int
    status: str


class EODHDConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EODHDDiagnostic:
    ticker: str
    request_url: str
    http_status: int
    content_type: str
    body_length: int
    body_preview: str
    parsed_row_count: int
    error_reason: str


def _sanitize_url(url: str) -> str:
    if "api_token=" not in url:
        return url
    prefix, suffix = url.split("api_token=", 1)
    if "&" in suffix:
        _, tail = suffix.split("&", 1)
        return f"{prefix}api_token=***&{tail}"
    return f"{prefix}api_token=***"


def get_eodhd_api_key() -> str:
    key = os.getenv("EODHD_API_KEY", "").strip()
    if not key:
        raise EODHDConfigError("EODHD_API_KEY is required")
    return key


def fetch_eodhd_eod(symbol: str, api_key: str, start: str = "1990-01-01") -> pd.DataFrame:
    base = "https://eodhd.com/api/eod"
    query = urllib.parse.urlencode({"api_token": api_key, "fmt": "json", "from": start, "period": "d"})
    url = f"{base}/{urllib.parse.quote(symbol)}?{query}"
    payload, _ = _download_json(url)
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError(f"EODHD error for {symbol}: {payload.get('message', payload.get('error'))}")
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"EODHD returned no data for {symbol}")
    frame = pd.DataFrame(payload)
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        missing = required - set(frame.columns)
        raise ValueError(f"EODHD response missing fields for {symbol}: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], utc=False)
    frame = frame.set_index("date").sort_index()
    out = frame[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = out.index.tz_localize(None)
    return out


def coverage_row(
    symbol: str,
    frame: pd.DataFrame,
    min_years_ok: float = 30.0,
    min_row_coverage_ok: float = 0.90,
) -> EODHDCoverageRow:
    start = frame.index.min()
    end = frame.index.max()
    years = max((end - start).days / 365.25, 0.0) if len(frame) else 0.0
    expected_days = len(pd.bdate_range(start=start, end=end)) if len(frame) else 0
    missing = max(expected_days - len(frame), 0)
    gaps = int((frame.index.to_series().diff().dt.days.fillna(1) > 3).sum()) if len(frame) else 0
    row_coverage = (len(frame) / expected_days) if expected_days else 0.0
    status = "OK" if years >= min_years_ok and row_coverage >= min_row_coverage_ok else "WARNING"
    return EODHDCoverageRow(
        ticker=symbol,
        start_date=str(start.date()) if len(frame) else "",
        end_date=str(end.date()) if len(frame) else "",
        row_count=int(len(frame)),
        coverage_years=round(years, 2),
        missing_rows=int(missing),
        gap_days=gaps,
        status=status,
    )


def write_coverage_report(rows: list[EODHDCoverageRow], out_csv: Path) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([r.__dict__ for r in rows])
    frame.to_csv(out_csv, index=False)
    return out_csv


def write_vendor_report(rows: list[EODHDCoverageRow], out_json: Path) -> Path:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vendor": "EODHD",
        "symbols": [r.__dict__ for r in rows],
        "ok_count": sum(1 for r in rows if r.status == "OK"),
        "warning_count": sum(1 for r in rows if r.status == "WARNING"),
        "fail_count": sum(1 for r in rows if r.status == "FAIL"),
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_json


def fetch_eodhd_eod_diagnostic(symbol: str, api_key: str, start: str = "1990-01-01") -> EODHDDiagnostic:
    base = "https://eodhd.com/api/eod"
    query = urllib.parse.urlencode({"api_token": api_key, "fmt": "json", "from": start, "period": "d"})
    url = f"{base}/{urllib.parse.quote(symbol)}?{query}"
    try:
        payload, meta = _download_json(url)
        parsed_rows = len(payload) if isinstance(payload, list) else 0
        error_reason = ""
        if isinstance(payload, dict) and payload.get("error"):
            error_reason = str(payload.get("message", payload.get("error")))
        preview = meta["body_preview"][:300]
        return EODHDDiagnostic(symbol, _sanitize_url(url), meta["http_status"], meta["content_type"], meta["body_length"], preview, parsed_rows, error_reason)
    except Exception as exc:
        return EODHDDiagnostic(symbol, _sanitize_url(url), 0, "", 0, "", 0, str(exc)[:300])


def _download_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            return payload, {"http_status": int(getattr(response, "status", 200)), "content_type": str(response.headers.get("Content-Type", "")), "body_length": len(raw), "body_preview": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": True, "message": raw[:300]}
        return payload, {"http_status": int(exc.code), "content_type": str(exc.headers.get("Content-Type", "")), "body_length": len(raw), "body_preview": raw}
