from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
        except Exception:
            frames = {}
            source = "synthetic"
    if not frames:
        frames = {symbol: synthetic_daily_ohlcv(symbol) for symbol in symbols}

    panel = pd.concat(frames, axis=1).sort_index()
    manifest = _write_manifest(root, "daily_universe", source, list(frames), panel)
    return DataBundle("daily_universe", "1D", panel, manifest)


def load_intraday_symbol(root: Path, symbol: str) -> DataBundle:
    data = synthetic_intraday_ohlcv(symbol)
    manifest = _write_manifest(root, f"intraday_{symbol}", "synthetic", [symbol], data)
    return DataBundle(f"intraday_{symbol}", "15M", data, manifest)


def _write_manifest(root: Path, name: str, source: str, symbols: list[str], data: pd.DataFrame) -> dict:
    payload = {
        "name": name,
        "source": source,
        "symbols": symbols,
        "rows": int(len(data)),
        "start": str(data.index.min()),
        "end": str(data.index.max()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
    }
    path = root / "data" / "manifests" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

