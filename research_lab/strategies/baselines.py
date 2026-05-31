from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategySpec:
    family: str
    asset_class: str
    timeframe: str
    short_name: str
    hypothesis: str
    parameters: dict
    rules: str
    builder: str

    def strategy_id(self, sequence: int) -> str:
        stamp = date.today().strftime("%Y%m%d")
        return f"{self.family}_{self.asset_class}_{self.timeframe}_{self.short_name}_{stamp}_{sequence:03d}"


def baseline_strategies() -> list[StrategySpec]:
    return [
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_FILTER",
            hypothesis="A long-only equity allocation with a 200-day trend filter should reduce drawdown versus always-on exposure.",
            parameters={"symbol": "SPY", "sma": 200},
            rules="Hold SPY when close is above its 200-day SMA; otherwise hold cash.",
            builder="long_term_trend_filter",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DUAL_MOMENTUM",
            hypothesis="Monthly top-N momentum rotation across equity, bond, gold, and growth ETFs may improve risk-adjusted return.",
            parameters={"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
            rules="At month end rank by 126-day momentum and hold the top two assets equally for the next month.",
            builder="active_momentum_rotation",
        ),
        StrategySpec(
            family="SWING",
            asset_class="ETF",
            timeframe="1D",
            short_name="RSI_PULLBACK",
            hypothesis="Buying oversold pullbacks only inside a rising long-term trend may produce positive expectancy with bounded exposure.",
            parameters={"symbol": "SPY", "trend_sma": 100, "rsi_entry": 35, "rsi_exit": 55},
            rules="Enter long when SPY is above SMA100 and RSI14 is below 35; exit when RSI14 exceeds 55 or price closes below SMA100.",
            builder="swing_rsi_pullback",
        ),
        StrategySpec(
            family="INTRADAY",
            asset_class="BTCUSDT",
            timeframe="15M",
            short_name="VWAP_RSI_RECLAIM",
            hypothesis="A VWAP reclaim after weak RSI can capture short intraday continuation if fills survive realistic costs.",
            parameters={"symbol": "BTCUSDT", "rsi_reclaim": 50, "rsi_washout": 45},
            rules="Enter when close reclaims session VWAP and RSI14 crosses above 50 after sub-45 weakness; exit on VWAP loss or session end.",
            builder="intraday_vwap_rsi_reclaim",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_STRICT_CASH",
            hypothesis="A stricter equity trend filter may reduce long-history ETF drawdowns by requiring both price and intermediate trend confirmation.",
            parameters={"symbol": "SPY", "sma": 200, "confirmation_sma": 50},
            rules="Hold SPY only when close is above SMA200 and SMA50 is above SMA200; otherwise hold cash.",
            builder="long_term_strict_cash_filter",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_VOL_CAP",
            hypothesis="A capped volatility-targeted SPY trend sleeve may reduce drawdown without changing existing promotion gates.",
            parameters={"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.10, "max_weight": 0.75},
            rules="Hold SPY above SMA200 with realized-volatility targeting capped at 75% exposure; otherwise hold cash.",
            builder="long_term_vol_target_cap",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DUAL_MOMENTUM_DD_CB",
            hypothesis="A drawdown circuit breaker on dual momentum may reduce crisis-period losses by forcing cash after deep SPY drawdowns.",
            parameters={
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "lookback": 126,
                "top_n": 2,
                "risk_symbol": "SPY",
                "drawdown_threshold": -0.12,
                "recovery_sma": 200,
            },
            rules="Run monthly top-2 dual momentum, but move fully to cash once SPY is down 12% from its peak until SPY recovers above SMA200.",
            builder="rotation_momentum_circuit_breaker",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DEFENSIVE_ROTATION",
            hypothesis="A simple defensive rotation into TLT, GLD, or cash during equity risk-off periods may reduce ETF drawdowns.",
            parameters={
                "risk_assets": ["SPY", "QQQ"],
                "defensive_assets": ["TLT", "GLD"],
                "lookback": 126,
                "top_n": 1,
                "risk_symbol": "SPY",
                "risk_sma": 200,
            },
            rules="When SPY is above SMA200, hold the top risk asset by 126-day momentum; otherwise hold the stronger of TLT or GLD if its momentum is positive, else cash.",
            builder="defensive_asset_rotation",
        ),
    ]


def queued_hypothesis_strategies(root: Path, limit: int = 4) -> list[StrategySpec]:
    queue_path = root / "registry" / "hypothesis_queue.jsonl"
    if not queue_path.exists():
        return []
    items = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    specs = []
    seen_keys: set[str] = set()
    for item in items:
        key = str(item.get("source_key") or item.get("hypothesis_id") or f"{item.get('family', '')}:{item.get('ticker', '')}:{item.get('title', '')}")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        spec = _spec_from_hypothesis(item)
        if spec:
            specs.append(spec)
        if len(specs) >= limit:
            break
    return specs


def queued_daily_symbols(root: Path, limit: int = 8) -> list[str]:
    queue_path = root / "registry" / "hypothesis_queue.jsonl"
    if not queue_path.exists():
        return []
    symbols = []
    for line in reversed(queue_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        item = json.loads(line)
        ticker = str(item.get("ticker", "")).strip().upper()
        if ticker and ticker not in symbols:
            symbols.append(ticker)
        if len(symbols) >= limit:
            break
    return list(reversed(symbols))


def build_weights(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    builders = {
        "long_term_trend_filter": long_term_trend_filter,
        "long_term_vol_target": long_term_vol_target,
        "long_term_strict_cash_filter": long_term_strict_cash_filter,
        "long_term_vol_target_cap": long_term_vol_target_cap,
        "active_momentum_rotation": active_momentum_rotation,
        "rotation_momentum_drawdown_filter": rotation_momentum_drawdown_filter,
        "rotation_momentum_circuit_breaker": rotation_momentum_circuit_breaker,
        "defensive_asset_rotation": defensive_asset_rotation,
        "swing_rsi_pullback": swing_rsi_pullback,
        "swing_trend_filtered_pullback": swing_trend_filtered_pullback,
        "intraday_vwap_rsi_reclaim": intraday_vwap_rsi_reclaim,
    }
    return builders[spec.builder](spec, daily_panel, intraday)


def long_term_trend_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters["symbol"]
    close = daily_panel[(symbol, "close")]
    sma = close.rolling(spec.parameters["sma"]).mean()
    weights = pd.DataFrame({symbol: (close > sma).astype(float)}, index=close.index)
    return weights


def long_term_vol_target(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    returns = close.pct_change()
    sma = close.rolling(spec.parameters.get("sma", 150)).mean()
    realized_vol = returns.rolling(spec.parameters.get("vol_window", 63)).std() * np.sqrt(252)
    target_vol = spec.parameters.get("target_vol", 0.12)
    raw_weight = (target_vol / realized_vol).clip(lower=0.0, upper=1.0)
    weight = raw_weight.where(close > sma, 0.0).fillna(0.0)
    return pd.DataFrame({symbol: weight}, index=close.index)


def long_term_strict_cash_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    slow = close.rolling(spec.parameters.get("sma", 200)).mean()
    confirmation = close.rolling(spec.parameters.get("confirmation_sma", 50)).mean()
    risk_on = (close > slow) & (confirmation > slow)
    return pd.DataFrame({symbol: risk_on.astype(float).fillna(0.0)}, index=close.index)


def long_term_vol_target_cap(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    returns = close.pct_change()
    sma = close.rolling(spec.parameters.get("sma", 200)).mean()
    realized_vol = returns.rolling(spec.parameters.get("vol_window", 63)).std() * np.sqrt(252)
    target_vol = spec.parameters.get("target_vol", 0.10)
    max_weight = spec.parameters.get("max_weight", 0.75)
    raw_weight = (target_vol / realized_vol).clip(lower=0.0, upper=max_weight)
    weight = raw_weight.where(close > sma, 0.0).fillna(0.0)
    return pd.DataFrame({symbol: weight}, index=close.index)


def active_momentum_rotation(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters["symbols"]
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters["lookback"])
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= spec.parameters["top_n"]).astype(float) / float(spec.parameters["top_n"])
    weights = selected.reindex(close.index, method="ffill").fillna(0.0)
    return weights


def rotation_momentum_drawdown_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters.get("symbols", ["SPY", "QQQ", "TLT", "GLD"])
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters.get("lookback", 126))
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= spec.parameters.get("top_n", 2)).astype(float) / float(spec.parameters.get("top_n", 2))
    weights = selected.reindex(close.index, method="ffill").fillna(0.0)
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_sma = close[risk_symbol].rolling(spec.parameters.get("risk_sma", 200)).mean()
    risk_on = (close[risk_symbol] > risk_sma).astype(float)
    weights = weights.mul(risk_on, axis=0)
    return weights


def rotation_momentum_circuit_breaker(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters.get("symbols", ["SPY", "QQQ", "TLT", "GLD"])
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters.get("lookback", 126))
    weights = _monthly_top_n_weights(momentum, close.index, spec.parameters.get("top_n", 2))
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_close = close[risk_symbol]
    drawdown = risk_close / risk_close.cummax() - 1.0
    recovery = risk_close.rolling(spec.parameters.get("recovery_sma", 200)).mean()
    threshold = spec.parameters.get("drawdown_threshold", -0.12)
    risk_on = []
    circuit_open = False
    for ts in risk_close.index:
        if circuit_open:
            if risk_close.loc[ts] > recovery.loc[ts]:
                circuit_open = False
        elif drawdown.loc[ts] <= threshold:
            circuit_open = True
        risk_on.append(0.0 if circuit_open else 1.0)
    return weights.mul(pd.Series(risk_on, index=close.index), axis=0)


def defensive_asset_rotation(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    risk_assets = spec.parameters.get("risk_assets", ["SPY", "QQQ"])
    defensive_assets = spec.parameters.get("defensive_assets", ["TLT", "GLD"])
    symbols = list(dict.fromkeys([*risk_assets, *defensive_assets]))
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    lookback = spec.parameters.get("lookback", 126)
    momentum = close.pct_change(lookback)
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_sma = close[risk_symbol].rolling(spec.parameters.get("risk_sma", 200)).mean()
    risk_on = close[risk_symbol] > risk_sma
    weights = pd.DataFrame(0.0, index=close.index, columns=symbols)
    top_n = int(spec.parameters.get("top_n", 1))
    for ts in close.index:
        if risk_on.loc[ts]:
            selected = _select_positive_momentum(momentum.loc[ts, risk_assets], top_n, require_positive=False)
        else:
            selected = _select_positive_momentum(momentum.loc[ts, defensive_assets], 1, require_positive=True)
        if selected:
            allocation = 1.0 / len(selected)
            for symbol in selected:
                weights.loc[ts, symbol] = allocation
    return weights


def swing_rsi_pullback(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters["symbol"]
    close = daily_panel[(symbol, "close")]
    rsi = _rsi(close)
    sma = close.rolling(spec.parameters["trend_sma"]).mean()
    position = []
    active = False
    for ts in close.index:
        if not active and close.loc[ts] > sma.loc[ts] and rsi.loc[ts] < spec.parameters["rsi_entry"]:
            active = True
        elif active and (rsi.loc[ts] > spec.parameters["rsi_exit"] or close.loc[ts] < sma.loc[ts]):
            active = False
        position.append(1.0 if active else 0.0)
    return pd.DataFrame({symbol: position}, index=close.index)


def swing_trend_filtered_pullback(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "QQQ")
    close = daily_panel[(symbol, "close")]
    rsi = _rsi(close)
    fast = close.rolling(spec.parameters.get("fast_sma", 50)).mean()
    slow = close.rolling(spec.parameters.get("slow_sma", 150)).mean()
    atr = (daily_panel[(symbol, "high")] - daily_panel[(symbol, "low")]).rolling(14).mean()
    position = []
    entry_price = 0.0
    active = False
    for ts in close.index:
        trend_ok = fast.loc[ts] > slow.loc[ts]
        pullback = rsi.loc[ts] < spec.parameters.get("rsi_entry", 40)
        if not active and trend_ok and pullback:
            active = True
            entry_price = close.loc[ts]
        elif active:
            stop = close.loc[ts] < entry_price - spec.parameters.get("atr_stop", 2.0) * atr.loc[ts]
            exit_signal = rsi.loc[ts] > spec.parameters.get("rsi_exit", 58) or close.loc[ts] < slow.loc[ts] or stop
            if exit_signal:
                active = False
        position.append(1.0 if active else 0.0)
    return pd.DataFrame({symbol: position}, index=close.index)


def intraday_vwap_rsi_reclaim(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    if intraday is None:
        raise ValueError("intraday data is required")
    symbol = spec.parameters["symbol"]
    close = intraday["close"]
    typical = (intraday["high"] + intraday["low"] + intraday["close"]) / 3.0
    session = intraday.index.normalize()
    vwap = (typical * intraday["volume"]).groupby(session).cumsum() / intraday["volume"].groupby(session).cumsum()
    rsi = _rsi(close)
    washed_out = rsi.groupby(session).cummin() < spec.parameters["rsi_washout"]
    reclaim = (close > vwap) & (close.shift(1) <= vwap.shift(1)) & (rsi > spec.parameters["rsi_reclaim"]) & washed_out
    position = []
    active = False
    last_session = None
    for ts in close.index:
        current_session = ts.normalize()
        if last_session is not None and current_session != last_session:
            active = False
        if not active and reclaim.loc[ts]:
            active = True
        elif active and close.loc[ts] < vwap.loc[ts]:
            active = False
        position.append(1.0 if active else 0.0)
        last_session = current_session
    return pd.DataFrame({symbol: position}, index=close.index)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(window).mean()
    loss = -delta.clip(upper=0.0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _monthly_top_n_weights(momentum: pd.DataFrame, index: pd.Index, top_n: int) -> pd.DataFrame:
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= top_n).astype(float) / float(top_n)
    weights = selected.reindex(index, method="ffill").fillna(0.0)
    if weights.sum(axis=1).eq(0.0).all() and len(momentum) > 0:
        ranks = momentum.rank(axis=1, ascending=False, method="first")
        weights = ((ranks <= top_n).astype(float) / float(top_n)).fillna(0.0)
    return weights


def _select_positive_momentum(row: pd.Series, top_n: int, require_positive: bool) -> list[str]:
    clean = row.dropna().sort_values(ascending=False)
    if require_positive:
        clean = clean[clean > 0]
    return [str(symbol) for symbol in clean.head(top_n).index]


def _spec_from_hypothesis(item: dict) -> StrategySpec | None:
    family = item.get("family")
    title = item.get("title", "Queued hypothesis")
    source_title = item.get("source_title", "unknown source")
    hypothesis_id = item.get("hypothesis_id", "")
    if family == "ROTATION":
        return StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_MOM_DD",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "lookback": 126,
                "top_n": 2,
                "risk_symbol": "SPY",
                "risk_sma": 200,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
            },
            rules="Monthly top-2 momentum rotation, but de-risk to cash when SPY is below SMA200.",
            builder="rotation_momentum_drawdown_filter",
        )
    if family == "SWING":
        ticker = str(item.get("ticker", "QQQ")).strip().upper() or "QQQ"
        return StrategySpec(
            family="SWING",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_PULLBACK",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbol": ticker,
                "fast_sma": 50,
                "slow_sma": 150,
                "rsi_entry": 40,
                "rsi_exit": 58,
                "atr_stop": 2.0,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
            },
            rules="Enter QQQ pullbacks in an uptrend; exit on RSI recovery, slow-trend break, or ATR stop.",
            builder="swing_trend_filtered_pullback",
        )
    if family == "LONGTERM":
        return StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_VOL_TARGET",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbol": "SPY",
                "sma": 150,
                "vol_window": 63,
                "target_vol": 0.12,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
            },
            rules="Hold SPY above SMA150 with exposure scaled down when realized volatility exceeds target.",
            builder="long_term_vol_target",
        )
    return None
