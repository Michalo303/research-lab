from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MinerviniCoreConfigV1:
    minimum_price: float = 5.0
    minimum_dollar_volume_20: float = 10_000_000.0
    relative_strength_percentile: float = 0.80
    vcp_block_sessions: int = 20
    vcp_final_to_first_max: float = 0.60
    dry_up_sessions: int = 10
    dry_up_reference_sessions: int = 50
    dry_up_max_ratio: float = 0.70
    breakout_volume_multiple: float = 1.50
    maximum_gap_above_pivot: float = 0.02
    atr_sessions: int = 20
    minimum_stop_atr: float = 2.0
    maximum_stop_fraction: float = 0.07


def build_minervini_signals_v1(
    daily_panel: pd.DataFrame,
    *,
    instrument_types: Mapping[str, str],
    config: MinerviniCoreConfigV1 = MinerviniCoreConfigV1(),
) -> pd.DataFrame:
    """Build close-known Trend Template and VCP signals without future data."""
    symbols = _validate_panel(daily_panel, instrument_types)
    close = _field(daily_panel, symbols, "close")
    high = _field(daily_panel, symbols, "high")
    low = _field(daily_panel, symbols, "low")
    volume = _field(daily_panel, symbols, "volume")

    sma50 = close.rolling(50, min_periods=50).mean()
    sma150 = close.rolling(150, min_periods=150).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    dollar_volume20 = (close * volume).rolling(20, min_periods=20).mean()
    low252 = close.rolling(252, min_periods=252).min()
    high252 = close.rolling(252, min_periods=252).max()
    rs_return = close / close.shift(252) - 1.0

    type_ok = pd.DataFrame(
        {
            symbol: instrument_types[symbol].casefold() == "common stock"
            for symbol in symbols
        },
        index=close.index,
    )
    price_ok = close >= config.minimum_price
    liquidity_ok = dollar_volume20 >= config.minimum_dollar_volume_20
    trend_ok = (
        (close > sma50)
        & (sma50 > sma150)
        & (sma50 > sma200)
        & (sma150 > sma200)
        & (sma200 > sma200.shift(20))
    )
    range52_ok = (close >= low252 * 1.30) & (close >= high252 * 0.75)
    pre_rs_eligible = (
        type_ok & price_ok & liquidity_ok & trend_ok & range52_ok
    )
    rs_percentile = rs_return.where(pre_rs_eligible).rank(axis=1, pct=True)
    rs_ok = rs_percentile >= config.relative_strength_percentile
    eligible = pre_rs_eligible & rs_ok

    outputs: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        past_high = high[symbol].shift(1)
        past_low = low[symbol].shift(1)
        block = config.vcp_block_sessions
        first_range = _rolling_range(
            past_high.shift(block * 2), past_low.shift(block * 2), block
        )
        second_range = _rolling_range(
            past_high.shift(block), past_low.shift(block), block
        )
        final_range = _rolling_range(past_high, past_low, block)
        contraction_ok = (
            (first_range > second_range)
            & (second_range > final_range)
            & (final_range <= first_range * config.vcp_final_to_first_max)
        )
        prior_volume = volume[symbol].shift(1)
        dry_reference = prior_volume.rolling(
            config.dry_up_reference_sessions,
            min_periods=config.dry_up_reference_sessions,
        ).mean()
        dry_recent = prior_volume.rolling(
            config.dry_up_sessions,
            min_periods=config.dry_up_sessions,
        ).mean()
        dry_up_ok = dry_recent <= dry_reference * config.dry_up_max_ratio
        pivot = past_high.rolling(block, min_periods=block).max()
        breakout_ok = close[symbol] > pivot
        breakout_volume_ok = (
            volume[symbol] >= dry_reference * config.breakout_volume_multiple
        )
        vcp = contraction_ok & dry_up_ok
        signal = (
            eligible[symbol] & vcp & breakout_ok & breakout_volume_ok
        ).fillna(False)
        structural_stop = past_low.rolling(
            config.dry_up_sessions,
            min_periods=config.dry_up_sessions,
        ).min()
        atr20 = _atr(
            high[symbol],
            low[symbol],
            close[symbol],
            config.atr_sessions,
        )
        reasons = _rejection_reasons(
            type_ok=type_ok[symbol],
            price_ok=price_ok[symbol],
            liquidity_ok=liquidity_ok[symbol],
            trend_ok=trend_ok[symbol] & range52_ok[symbol],
            rs_ok=rs_ok[symbol],
            contraction_ok=contraction_ok,
            dry_up_ok=dry_up_ok,
            breakout_ok=breakout_ok,
            breakout_volume_ok=breakout_volume_ok,
        )
        outputs[symbol] = pd.DataFrame(
            {
                "eligible": eligible[symbol].fillna(False),
                "rs_percentile": rs_percentile[symbol],
                "vcp": vcp.fillna(False),
                "pivot": pivot,
                "structural_stop": structural_stop,
                "atr20": atr20,
                "r_multiple_price": close[symbol]
                + 2.0 * (close[symbol] - structural_stop),
                "signal": signal,
                "rejection_reasons": reasons,
            },
            index=daily_panel.index,
        )
    return pd.concat(outputs, axis=1)


def _validate_panel(
    panel: pd.DataFrame, instrument_types: Mapping[str, str]
) -> list[str]:
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        raise ValueError("daily_panel must be a non-empty DataFrame.")
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError("daily_panel index must be a DatetimeIndex.")
    if not panel.index.is_monotonic_increasing or not panel.index.is_unique:
        raise ValueError("daily_panel index must be monotonic and unique.")
    if not isinstance(panel.columns, pd.MultiIndex):
        raise ValueError("daily_panel must have MultiIndex columns.")
    symbols = sorted({str(value) for value in panel.columns.get_level_values(0)})
    required = {"open", "high", "low", "close", "volume"}
    for symbol in symbols:
        available = {
            str(field)
            for candidate, field in panel.columns
            if str(candidate) == symbol
        }
        if not required.issubset(available):
            raise ValueError(f"{symbol} is missing required OHLCV fields.")
        if symbol not in instrument_types:
            raise ValueError(f"{symbol} is missing instrument type.")
    numeric = panel.loc[
        :,
        [
            column
            for column in panel.columns
            if str(column[1]) in required
        ],
    ]
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values[~np.isnan(values)]).all():
        raise ValueError("daily_panel contains non-finite OHLCV values.")
    if (_field(panel, symbols, "volume") < 0).any().any():
        raise ValueError("daily_panel volume must be non-negative.")
    return symbols


def _field(
    panel: pd.DataFrame, symbols: list[str], field: str
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            symbol: panel.loc[:, (symbol, field)].astype(float)
            for symbol in symbols
        },
        index=panel.index,
    )


def _rolling_range(
    high: pd.Series, low: pd.Series, sessions: int
) -> pd.Series:
    maximum = high.rolling(sessions, min_periods=sessions).max()
    minimum = low.rolling(sessions, min_periods=sessions).min()
    return maximum / minimum - 1.0


def _atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    sessions: int,
) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(sessions, min_periods=sessions).mean()


def _rejection_reasons(
    *,
    type_ok: pd.Series,
    price_ok: pd.Series,
    liquidity_ok: pd.Series,
    trend_ok: pd.Series,
    rs_ok: pd.Series,
    contraction_ok: pd.Series,
    dry_up_ok: pd.Series,
    breakout_ok: pd.Series,
    breakout_volume_ok: pd.Series,
) -> pd.Series:
    checks = (
        ("NOT_COMMON_STOCK", type_ok),
        ("MINIMUM_PRICE", price_ok),
        ("MINIMUM_DOLLAR_VOLUME", liquidity_ok),
        ("TREND_TEMPLATE", trend_ok),
        ("RELATIVE_STRENGTH", rs_ok),
        ("VCP_CONTRACTION", contraction_ok),
        ("VOLUME_DRY_UP", dry_up_ok),
        ("PIVOT_BREAKOUT", breakout_ok),
        ("BREAKOUT_VOLUME", breakout_volume_ok),
    )
    return pd.Series(
        [
            tuple(
                name
                for name, check in checks
                if not bool(check.iloc[position])
            )
            for position in range(len(type_ok))
        ],
        index=type_ok.index,
        dtype=object,
    )
