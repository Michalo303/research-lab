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


REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


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
    manifest["requested_symbols"] = symbols
    manifest["symbol_diagnostics"] = _symbol_diagnostics(symbols, source, frames, fallback_used=False)
    (root / "data" / "manifests" / "daily_universe.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
            "requested_symbols": symbols,
            "base_url": base_url,
            "adjusted": adjusted,
            "api_key_present": True,
            "stored_csv": str(raw_path),
            "failed_symbols": failed_symbols,
            "symbol_diagnostics": _symbol_diagnostics(symbols, "massive", frames, fallback_used=False),
        }
    )
    (root / "data" / "manifests" / "daily_universe.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return DataBundle("daily_universe", "1D", panel, manifest)


def load_eodhd_daily_universe(
    root: Path,
    symbols: list[str],
    api_key: str,
    start_date: str = "1990-01-01",
) -> DataBundle:
    if not api_key:
        raise ValueError("EODHD_API_KEY is required for the eodhd data provider")
    from research_lab.data_eodhd import fetch_eodhd_eod

    requested_symbols = list(symbols)
    excluded_intraday_symbols = [symbol for symbol in requested_symbols if symbol == "BTCUSDT"]
    symbols = [symbol for symbol in symbols if symbol not in excluded_intraday_symbols]
    if not symbols:
        raise ValueError("EODHD daily universe has no non-intraday symbols to load")
    frames = {}
    provider_symbols = {}
    for symbol in symbols:
        provider_symbol = _eodhd_symbol(symbol)
        provider_symbols[symbol] = provider_symbol
        frame = fetch_eodhd_eod(provider_symbol, api_key=api_key, start=start_date)
        frames[symbol] = frame
        time.sleep(0.15)
    panel = pd.concat(frames, axis=1).sort_index()
    raw_path = root / "data" / "processed" / "eodhd_daily_universe.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(raw_path)
    manifest = _write_manifest(root, "daily_universe", "eodhd", list(frames), panel)
    manifest.update(
        {
            "provider": "eodhd",
            "requested_symbols": requested_symbols,
            "loaded_symbols": symbols,
            "provider_symbols": provider_symbols,
            "excluded_intraday_symbols": excluded_intraday_symbols,
            "api_key_present": True,
            "stored_csv": str(raw_path),
            "symbol_diagnostics": _symbol_diagnostics(
                symbols,
                "eodhd",
                frames,
                fallback_used=False,
                provider_symbols=provider_symbols,
            ),
        }
    )
    (root / "data" / "manifests" / "daily_universe.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return DataBundle("daily_universe", "1D", panel, manifest)


def load_cached_eodhd_daily_universe(root: Path, symbols: list[str]) -> DataBundle:
    """Load an immutable, previously materialized real-EODHD daily universe."""
    root = Path(root)
    csv_path = root / "data" / "processed" / "eodhd_daily_universe.csv"
    manifest_path = root / "data" / "manifests" / "daily_universe.json"
    requested_symbols = _normalized_requested_symbols(symbols)
    stored_manifest = validate_cached_eodhd_daily_universe_metadata(root, requested_symbols)

    try:
        panel = pd.read_csv(csv_path, header=[0, 1], index_col=0)
    except Exception as exc:
        raise ValueError(f"cached EODHD CSV is malformed: {csv_path}") from exc
    if panel.empty or not isinstance(panel.columns, pd.MultiIndex) or panel.columns.nlevels != 2:
        raise ValueError("cached EODHD CSV is malformed or empty: expected two-level symbol/OHLCV columns")
    try:
        parsed_index = pd.to_datetime(panel.index, errors="raise", format="mixed")
    except (TypeError, ValueError) as exc:
        raise ValueError("cached EODHD CSV date index is malformed") from exc
    if parsed_index.has_duplicates:
        raise ValueError("cached EODHD CSV date index must be unique")
    if not parsed_index.is_monotonic_increasing:
        raise ValueError("cached EODHD CSV date index must be sorted")
    panel.index = pd.DatetimeIndex(parsed_index)
    panel.columns = pd.MultiIndex.from_tuples(
        [(str(symbol).strip(), str(field).strip().lower()) for symbol, field in panel.columns]
    )
    if panel.columns.has_duplicates:
        raise ValueError("cached EODHD CSV contains duplicate symbol/OHLCV columns")

    selected_columns: list[tuple[str, str]] = []
    for symbol in requested_symbols:
        for field in REQUIRED_OHLCV_FIELDS:
            column = (symbol, field)
            if column not in panel.columns:
                raise ValueError(f"cached EODHD CSV is missing {field} for requested symbol {symbol}")
            original = panel[column]
            numeric = pd.to_numeric(original, errors="coerce")
            numeric_values = numeric.dropna().to_numpy(dtype=float)
            if (
                original.notna().sum() != numeric.notna().sum()
                or numeric_values.size == 0
                or not np.isfinite(numeric_values).all()
            ):
                raise ValueError(f"cached EODHD CSV requires usable numeric {field} values for {symbol}")
            panel[column] = numeric.astype(float)
            selected_columns.append(column)

    selected = panel.loc[:, selected_columns]
    selected.columns = pd.MultiIndex.from_tuples(selected_columns)
    if selected.dropna(how="all").empty:
        raise ValueError("cached EODHD CSV has no usable requested data")
    diagnostics = _cached_symbol_diagnostics(requested_symbols, selected)
    usable = selected.dropna(how="all")
    start = usable.index.min()
    end = usable.index.max()
    years = max((end - start).days / 365.25, 0.0)
    manifest = {
        "name": "daily_universe",
        "source": "eodhd",
        "provider": "eodhd",
        "load_mode": "offline_cache",
        "provider_request_made": False,
        "fallback_used": False,
        "symbols": requested_symbols,
        "requested_symbols": requested_symbols,
        "rows": int(len(selected)),
        "start": str(start),
        "end": str(end),
        "years": years,
        "research_only": True,
        "stored_csv": str(csv_path),
        "source_manifest": str(manifest_path),
        "source_manifest_created_at": stored_manifest.get("created_at", ""),
        "symbol_diagnostics": diagnostics,
    }
    return DataBundle("daily_universe", "1D", selected, manifest)


def validate_cached_eodhd_daily_universe_metadata(root: Path, symbols: list[str]) -> dict:
    """Validate fixed cache paths and provenance without parsing market data."""
    root = Path(root)
    csv_path = root / "data" / "processed" / "eodhd_daily_universe.csv"
    manifest_path = root / "data" / "manifests" / "daily_universe.json"
    if not csv_path.is_file():
        raise ValueError(f"cached EODHD CSV is missing: {csv_path}")
    if not manifest_path.is_file():
        raise ValueError(f"cached EODHD manifest is missing: {manifest_path}")
    if csv_path.stat().st_size <= 0:
        raise ValueError(f"cached EODHD CSV is empty: {csv_path}")
    if manifest_path.stat().st_size <= 0:
        raise ValueError(f"cached EODHD manifest is empty: {manifest_path}")

    try:
        stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cached EODHD manifest is malformed: {manifest_path}") from exc
    if not isinstance(stored_manifest, dict):
        raise ValueError("cached EODHD manifest is malformed: expected a JSON object")
    _validate_cached_eodhd_provenance(stored_manifest, csv_path)

    requested_symbols = _normalized_requested_symbols(symbols)
    manifest_symbols = stored_manifest.get("symbols") or stored_manifest.get("requested_symbols")
    if not isinstance(manifest_symbols, list):
        raise ValueError("cached EODHD manifest is inconsistent: symbols are missing")
    missing_manifest_symbols = [symbol for symbol in requested_symbols if symbol not in manifest_symbols]
    if missing_manifest_symbols:
        raise ValueError(
            "cached EODHD manifest does not contain requested symbols: "
            + ", ".join(missing_manifest_symbols)
        )
    validated = dict(stored_manifest)
    validated["requested_symbols"] = requested_symbols
    return validated


def _normalized_requested_symbols(symbols: list[str]) -> list[str]:
    requested = list(dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip()))
    if not requested:
        raise ValueError("cached EODHD load requires at least one requested symbol")
    return requested


def _validate_cached_eodhd_provenance(manifest: dict, csv_path: Path) -> None:
    source = manifest.get("source")
    provider = manifest.get("provider")
    if type(source) is not str or source != "eodhd" or type(provider) is not str or provider != "eodhd":
        raise ValueError("cached EODHD manifest provenance must canonically prove EODHD")
    if "fallback_used" in manifest and manifest.get("fallback_used") is not False:
        raise ValueError("cached EODHD manifest fallback marker must be the boolean false")
    if any(manifest.get(key) for key in ("fallback_reason", "fallback_source", "fallback_provider")):
        raise ValueError("cached EODHD manifest records an explicit fallback")
    diagnostics = manifest.get("symbol_diagnostics") or []
    if not isinstance(diagnostics, list) or any(
        isinstance(row, dict)
        and "fallback_used" in row
        and row.get("fallback_used") is not False
        for row in diagnostics
    ):
        raise ValueError("cached EODHD manifest symbol provenance records fallback usage")
    if not str(manifest.get("start") or "").strip() or not str(manifest.get("end") or "").strip():
        raise ValueError("cached EODHD manifest provenance requires non-empty start and end")
    if int(manifest.get("rows") or 0) <= 0:
        raise ValueError("cached EODHD manifest is inconsistent: rows must be positive")
    stored_csv = str(manifest.get("stored_csv") or "").strip()
    if not stored_csv or Path(stored_csv).resolve() != csv_path.resolve():
        raise ValueError("cached EODHD manifest is inconsistent with the stored CSV path")


def _cached_symbol_diagnostics(symbols: list[str], panel: pd.DataFrame) -> list[dict]:
    diagnostics = []
    for symbol in symbols:
        frame = panel[symbol].dropna(how="all")
        if frame.empty:
            raise ValueError(f"cached EODHD CSV has no usable requested data for {symbol}")
        start = frame.index.min()
        end = frame.index.max()
        diagnostics.append(
            {
                "requested_symbol": symbol,
                "provider_symbol": _eodhd_symbol(symbol),
                "selected_provider": "eodhd",
                "load_mode": "offline_cache",
                "provider_request_made": False,
                "fallback_used": False,
                "first_date": str(start.date()),
                "last_date": str(end.date()),
                "daily_bars": int(len(frame)),
                "history_years": round(max((end - start).days / 365.25, 0.0), 2),
            }
        )
    return diagnostics


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


def _symbol_diagnostics(
    requested_symbols: list[str],
    provider: str,
    frames: dict[str, pd.DataFrame],
    fallback_used: bool,
    provider_symbols: dict[str, str] | None = None,
) -> list[dict]:
    rows = []
    provider_symbols = provider_symbols or {}
    for symbol in requested_symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            rows.append(
                {
                    "requested_symbol": symbol,
                    "provider_symbol": provider_symbols.get(symbol, symbol),
                    "selected_provider": provider,
                    "fallback_used": fallback_used,
                    "first_date": "",
                    "last_date": "",
                    "daily_bars": 0,
                    "history_years": 0.0,
                }
            )
            continue
        start = frame.index.min()
        end = frame.index.max()
        years = max((end - start).days / 365.25, 0.0) if isinstance(start, pd.Timestamp) and isinstance(end, pd.Timestamp) else 0.0
        rows.append(
            {
                "requested_symbol": symbol,
                "provider_symbol": provider_symbols.get(symbol, symbol),
                "selected_provider": provider,
                "fallback_used": fallback_used,
                "first_date": str(start.date()) if hasattr(start, "date") else str(start),
                "last_date": str(end.date()) if hasattr(end, "date") else str(end),
                "daily_bars": int(len(frame)),
                "history_years": round(years, 2),
            }
        )
    return rows


def _eodhd_symbol(symbol: str) -> str:
    if "." in symbol:
        return symbol
    return f"{symbol}.US"


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
