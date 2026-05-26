from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DataBundle:
    name: str
    timeframe: str
    data: pd.DataFrame
    manifest: dict


def _seed(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _ohlcv_from_returns(name: str, dates: pd.DatetimeIndex, returns: np.ndarray) -> pd.DataFrame:
    rng = np.random.default_rng(_seed(name) + len(dates))
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]] * (1.0 + rng.normal(0, 0.001, len(dates)))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0005, 0.008, len(dates)))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0005, 0.008, len(dates)))
    volume = rng.integers(500_000, 5_000_000, len(dates))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def synthetic_daily_ohlcv(symbol: str, start: str = "2012-01-02", periods: int = 3600) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=periods)
    rng = np.random.default_rng(_seed(symbol))
    drift_map = {
        "SPY": 0.00030,
        "QQQ": 0.00038,
        "TLT": 0.00010,
        "GLD": 0.00012,
        "BTC-USD": 0.00055,
    }
    vol_map = {
        "SPY": 0.010,
        "QQQ": 0.013,
        "TLT": 0.008,
        "GLD": 0.011,
        "BTC-USD": 0.030,
    }
    drift = drift_map.get(symbol, 0.00020)
    vol = vol_map.get(symbol, 0.012)
    cycle = 0.0006 * np.sin(np.linspace(0, 18 * np.pi, len(dates)))
    shock = rng.normal(drift, vol, len(dates))
    for crash_start in (650, 1750, 2850):
        shock[crash_start : crash_start + 35] -= np.linspace(0.012, 0.002, 35)
    return _ohlcv_from_returns(symbol, dates, shock + cycle)


def synthetic_intraday_ohlcv(symbol: str, start: str = "2024-01-02", days: int = 260) -> pd.DataFrame:
    sessions = pd.bdate_range(start=start, periods=days)
    stamps = []
    for session in sessions:
        stamps.extend(pd.date_range(session.replace(hour=9, minute=30), periods=26, freq="15min"))
    index = pd.DatetimeIndex(stamps)
    rng = np.random.default_rng(_seed(symbol) + 15)
    intraday_wave = 0.0008 * np.sin(np.linspace(0, days * 2 * np.pi, len(index)))
    returns = rng.normal(0.00003, 0.0035, len(index)) + intraday_wave
    return _ohlcv_from_returns(symbol, index, returns)


def load_daily_universe(root: Path, symbols: list[str], use_yfinance: bool) -> DataBundle:
    frames = {}
    source = "synthetic"
    if use_yfinance:
        provider_error: Exception | None = None
        try:
            import yfinance as yf  # type: ignore

            downloaded = yf.download(symbols, period="15y", auto_adjust=True, progress=False)
            if not downloaded.empty:
                source = "yfinance"
                for symbol in symbols:
                    if isinstance(downloaded.columns, pd.MultiIndex):
                        raw = downloaded.xs(symbol, axis=1, level=1, drop_level=False)
                        raw.columns = raw.columns.get_level_values(0).str.lower()
                    else:
                        raw = downloaded.copy()
                        raw.columns = raw.columns.str.lower()
                    frames[symbol] = raw[["open", "high", "low", "close", "volume"]].dropna()
            else:
                provider_error = RuntimeError("yfinance returned no rows")
        except Exception as exc:
            provider_error = exc
            frames = {}
            source = "synthetic"
        if not frames and not _allow_synthetic_fallback():
            raise RuntimeError(
                "yfinance daily data failed and synthetic fallback is disabled; "
                "set RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK=1 for smoke tests."
            ) from provider_error
    if not frames:
        frames = {symbol: synthetic_daily_ohlcv(symbol) for symbol in symbols}

    panel = pd.concat(frames, axis=1).sort_index()
    manifest = _write_manifest(root, "daily_universe", source, list(frames), panel)
    return DataBundle("daily_universe", "1D", panel, manifest)


def _allow_synthetic_fallback() -> bool:
    return os.getenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "0") == "1"


def load_massive_daily_universe(
    root: Path,
    symbols: list[str],
    api_key: str,
    base_url: str,
    start_date: str,
    adjusted: bool,
) -> DataBundle:
    if not api_key:
        raise ValueError("MASSIVE_API_KEY is required for the massive data provider")
    frames = {}
    failed_symbols = {}
    required_symbols = set(symbols[:4])
    for symbol in symbols:
        try:
            frames[symbol] = _fetch_massive_daily(symbol, api_key, base_url, start_date, adjusted)
        except Exception as exc:
            if symbol in required_symbols:
                raise
            failed_symbols[symbol] = str(exc)
            continue
        time.sleep(0.15)
    panel = pd.concat(frames, axis=1).sort_index()
    raw_path = root / "data" / "processed" / "massive_daily_universe.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(raw_path)
    manifest = _write_manifest(root, "daily_universe", "massive", list(frames), panel)
    manifest.update(
        {
            "provider": "massive",
            "base_url": base_url,
            "adjusted": adjusted,
            "api_key_present": True,
            "stored_csv": str(raw_path),
            "failed_symbols": failed_symbols,
        }
    )
    (root / "data" / "manifests" / "daily_universe.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return DataBundle("daily_universe", "1D", panel, manifest)


def load_intraday_symbol(root: Path, symbol: str) -> DataBundle:
    data = synthetic_intraday_ohlcv(symbol)
    manifest = _write_manifest(root, f"intraday_{symbol}", "synthetic", [symbol], data)
    return DataBundle(f"intraday_{symbol}", "15M", data, manifest)


def _write_manifest(root: Path, name: str, source: str, symbols: list[str], data: pd.DataFrame) -> dict:
    start = data.index.min()
    end = data.index.max()
    years = 0.0
    if isinstance(start, pd.Timestamp) and isinstance(end, pd.Timestamp):
        years = max((end - start).days / 365.25, 0.0)
    payload = {
        "name": name,
        "source": source,
        "symbols": symbols,
        "rows": int(len(data)),
        "start": str(start),
        "end": str(end),
        "years": years,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
    }
    path = root / "data" / "manifests" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _fetch_massive_daily(symbol: str, api_key: str, base_url: str, start_date: str, adjusted: bool) -> pd.DataFrame:
    end_date = date.today().isoformat()
    url = (
        f"{base_url.rstrip('/')}/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/"
        f"{start_date}/{end_date}"
    )
    params = urllib.parse.urlencode(
        {
            "adjusted": str(adjusted).lower(),
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        }
    )
    payload = _download_json(f"{url}?{params}")
    rows = payload.get("results", [])
    if not rows:
        raise ValueError(f"Massive returned no daily bars for {symbol}")
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["t"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
    frame = frame.set_index("date").sort_index()
    output = pd.DataFrame(
        {
            "open": frame["o"].astype(float),
            "high": frame["h"].astype(float),
            "low": frame["l"].astype(float),
            "close": frame["c"].astype(float),
            "volume": frame["v"].astype(float),
        },
        index=frame.index,
    )
    output.index.name = None
    return output


def _download_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))
