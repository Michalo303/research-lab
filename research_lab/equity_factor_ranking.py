from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


ACTIVE_OHLCV_FACTOR_GROUPS = ["momentum", "volatility", "liquidity", "trend"]
SKIPPED_FUNDAMENTAL_FACTOR_GROUPS = ["growth", "quality", "valuation", "capital_allocation"]

# These are OHLCV-first scaffolds. They are intentionally not Fisher/Buffett/GARP/
# CANSLIM/QMJ implementations; those extensions need timestamp-safe fundamentals.
OHLCV_RANKING_PROFILES: dict[str, dict[str, float]] = {
    "OHLCV_MOMENTUM_QUALITY_PROXY": {
        "momentum": 0.40,
        "trend": 0.25,
        "volatility": 0.25,
        "liquidity": 0.10,
    },
    "OHLCV_DEFENSIVE_MOMENTUM": {
        "volatility": 0.35,
        "trend": 0.25,
        "momentum": 0.25,
        "liquidity": 0.15,
    },
    "OHLCV_COMPOSITE_RANKING": {
        "momentum": 0.30,
        "trend": 0.25,
        "volatility": 0.25,
        "liquidity": 0.20,
    },
}

FACTOR_METADATA: dict[str, dict[str, Any]] = {
    "momentum_12m_ex_recent": {"group": "momentum", "higher_is_better": True},
    "momentum_6m": {"group": "momentum", "higher_is_better": True},
    "momentum_3m": {"group": "momentum", "higher_is_better": True},
    "proximity_to_52w_high": {"group": "momentum", "higher_is_better": True},
    "realized_volatility_63d": {"group": "volatility", "higher_is_better": False},
    "drawdown_from_126d_peak": {"group": "volatility", "higher_is_better": False},
    "downside_volatility_63d": {"group": "volatility", "higher_is_better": False},
    "avg_dollar_volume_63d": {"group": "liquidity", "higher_is_better": True},
    "avg_volume_63d": {"group": "liquidity", "higher_is_better": True},
    "close_above_50dma": {"group": "trend", "higher_is_better": True},
    "close_above_150dma": {"group": "trend", "higher_is_better": True},
    "close_above_200dma": {"group": "trend", "higher_is_better": True},
    "dma50_above_dma200": {"group": "trend", "higher_is_better": True},
}


def validate_ohlcv_panel(panel: pd.DataFrame, required_fields: tuple[str, ...] = ("close",)) -> dict[str, Any]:
    if panel.empty:
        raise ValueError("OHLCV panel is empty")
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError("OHLCV panel index must be a DatetimeIndex")
    if panel.index.has_duplicates:
        raise ValueError("OHLCV panel index contains duplicate dates")
    if not panel.index.is_monotonic_increasing:
        raise ValueError("OHLCV panel index must be sorted ascending")

    symbols = _symbols(panel)
    missing_required: dict[str, list[str]] = {}
    missing_optional: dict[str, list[str]] = {}
    for symbol in symbols:
        fields = set(_symbol_frame(panel, symbol).columns)
        required_missing = [field for field in required_fields if field not in fields]
        optional_missing = [field for field in ("open", "high", "low", "close", "volume") if field not in fields]
        if required_missing:
            missing_required[symbol] = required_missing
        if optional_missing:
            missing_optional[symbol] = optional_missing

    if missing_required:
        raise ValueError(f"missing required OHLCV fields: {missing_required}")

    return {
        "valid": True,
        "symbols": symbols,
        "required_fields": list(required_fields),
        "missing_optional_fields": missing_optional,
    }


def calculate_price_factors(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    validate_ohlcv_panel(panel)
    close = _field_frame(panel, "close").astype(float)
    returns = close.pct_change()
    factors: dict[str, pd.DataFrame] = {
        "momentum_12m_ex_recent": close.shift(21) / close.shift(252) - 1.0,
        "momentum_6m": close / close.shift(126) - 1.0,
        "momentum_3m": close / close.shift(63) - 1.0,
        "proximity_to_52w_high": close / close.rolling(252, min_periods=252).max(),
        "realized_volatility_63d": returns.rolling(63, min_periods=63).std() * np.sqrt(252),
        "drawdown_from_126d_peak": close.rolling(126, min_periods=126).max() / close - 1.0,
        "downside_volatility_63d": returns.where(returns < 0.0).rolling(63, min_periods=1).std() * np.sqrt(252),
    }

    dma50 = close.rolling(50, min_periods=50).mean()
    dma150 = close.rolling(150, min_periods=150).mean()
    dma200 = close.rolling(200, min_periods=200).mean()
    factors["close_above_50dma"] = _comparison_factor(close, dma50)
    factors["close_above_150dma"] = _comparison_factor(close, dma150)
    factors["close_above_200dma"] = _comparison_factor(close, dma200)
    factors["dma50_above_dma200"] = _comparison_factor(dma50, dma200)

    if _has_field(panel, "volume"):
        volume = _field_frame(panel, "volume").astype(float)
        if _has_field(panel, "close"):
            factors["avg_dollar_volume_63d"] = (close * volume).rolling(63, min_periods=63).mean()
        else:
            factors["avg_volume_63d"] = volume.rolling(63, min_periods=63).mean()

    return factors


def percentile_rank(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    clean = values.replace([np.inf, -np.inf], np.nan)
    if clean.dropna().empty:
        return pd.Series(np.nan, index=values.index, dtype=float)
    return clean.rank(method="average", pct=True, ascending=higher_is_better)


def score_factor_groups(
    factors: dict[str, pd.DataFrame],
    factor_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, pd.DataFrame]:
    metadata = factor_metadata or FACTOR_METADATA
    ranked_by_group: dict[str, list[pd.DataFrame]] = {}
    for factor_name, frame in factors.items():
        spec = metadata.get(factor_name)
        if not spec:
            continue
        ranked = frame.apply(
            lambda row: percentile_rank(row, higher_is_better=bool(spec["higher_is_better"])),
            axis=1,
        )
        ranked_by_group.setdefault(str(spec["group"]), []).append(ranked)

    group_scores: dict[str, pd.DataFrame] = {}
    for group, frames in ranked_by_group.items():
        stacked = pd.concat(frames, axis=0, keys=range(len(frames)))
        group_scores[group] = stacked.groupby(level=1).mean()
    return group_scores


def combine_factor_groups(group_scores: dict[str, pd.DataFrame], profile: dict[str, float]) -> pd.DataFrame:
    active = {
        group: float(weight)
        for group, weight in profile.items()
        if weight > 0.0 and group in group_scores and not group_scores[group].empty and group_scores[group].notna().any().any()
    }
    if not active:
        return pd.DataFrame()

    total_weight = sum(active.values())
    normalized = {group: weight / total_weight for group, weight in active.items()}
    composite: pd.DataFrame | None = None
    for group, weight in normalized.items():
        contribution = group_scores[group] * weight
        composite = contribution if composite is None else composite.add(contribution, fill_value=0.0)
    return composite if composite is not None else pd.DataFrame()


def missing_fundamentals_diagnostics(fundamental_data_available: bool = False) -> dict[str, Any]:
    return {
        "fundamental_data_available": bool(fundamental_data_available),
        "skipped_factor_groups": [] if fundamental_data_available else SKIPPED_FUNDAMENTAL_FACTOR_GROUPS.copy(),
        "active_factor_groups": ACTIVE_OHLCV_FACTOR_GROUPS.copy(),
    }


def select_top_n(scores: pd.DataFrame, as_of_date: str | pd.Timestamp | None = None, top_n: int = 20) -> pd.Series:
    if scores.empty or top_n <= 0:
        return pd.Series(dtype=float)
    row = _score_row(scores, as_of_date).dropna()
    if row.empty:
        return pd.Series(dtype=float)
    ranked = (
        pd.DataFrame({"symbol": row.index.astype(str), "score": row.astype(float).to_numpy()})
        .sort_values(["score", "symbol"], ascending=[False, True], kind="mergesort")
        .head(top_n)
    )
    return pd.Series(ranked["score"].to_numpy(), index=ranked["symbol"].to_list(), dtype=float)


def construct_equal_weight_portfolio(
    scores: pd.DataFrame,
    as_of_date: str | pd.Timestamp | None = None,
    top_n: int = 20,
    min_assets: int = 10,
    max_position_weight: float = 0.10,
    target_exposure: float = 1.0,
    market_regime: pd.DataFrame | pd.Series | float | None = None,
) -> dict[str, Any]:
    selected = select_top_n(scores, as_of_date=as_of_date, top_n=top_n)
    if len(selected) < min_assets:
        return {
            "status": "rejected_min_assets",
            "weights": pd.Series(dtype=float),
            "selected_symbols": selected.index.to_list(),
            "diagnostics": {
                "reason": "fewer than min_assets eligible symbols",
                "eligible_assets": int(len(selected)),
                "min_assets": int(min_assets),
            },
        }

    exposure = float(target_exposure) * _regime_exposure(market_regime, as_of_date)
    per_asset = min(exposure / float(len(selected)), float(max_position_weight))
    weights = pd.Series(per_asset, index=selected.index, dtype=float)
    return {
        "status": "ok",
        "weights": weights,
        "selected_symbols": selected.index.to_list(),
        "target_exposure": float(target_exposure),
        "applied_exposure": float(weights.sum()),
        "diagnostics": {
            "eligible_assets": int(len(selected)),
            "requested_top_n": int(top_n),
            "max_position_weight": float(max_position_weight),
            "cap_binding": bool(per_asset < exposure / float(len(selected))),
        },
    }


def simple_market_regime_signal(
    benchmark_close: pd.Series,
    window: int = 200,
    risk_on_exposure: float = 1.0,
    risk_off_exposure: float = 0.5,
) -> pd.DataFrame:
    close = benchmark_close.astype(float).sort_index()
    trailing_close = close.shift(1)
    trailing_average = close.rolling(window, min_periods=window).mean().shift(1)
    risk_on_values = (trailing_close > trailing_average).fillna(False)
    risk_on = pd.Series([bool(value) for value in risk_on_values], index=close.index, dtype=object)
    exposure = pd.Series(risk_off_exposure, index=close.index, dtype=float)
    exposure.loc[risk_on_values] = float(risk_on_exposure)
    return pd.DataFrame({"risk_on": risk_on, "target_exposure": exposure}, index=close.index)


def _symbols(panel: pd.DataFrame) -> list[str]:
    if isinstance(panel.columns, pd.MultiIndex):
        return list(dict.fromkeys(str(symbol) for symbol in panel.columns.get_level_values(0)))
    return [""]


def _symbol_frame(panel: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(panel.columns, pd.MultiIndex):
        frame = panel.xs(symbol, axis=1, level=0).copy()
    else:
        frame = panel.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    return frame


def _field_frame(panel: pd.DataFrame, field: str) -> pd.DataFrame:
    frames = {}
    for symbol in _symbols(panel):
        frame = _symbol_frame(panel, symbol)
        if field in frame.columns:
            frames[symbol] = frame[field]
    return pd.DataFrame(frames, index=panel.index)


def _has_field(panel: pd.DataFrame, field: str) -> bool:
    return any(field in _symbol_frame(panel, symbol).columns for symbol in _symbols(panel))


def _comparison_factor(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    return (left > right).where(left.notna() & right.notna()).astype(float)


def _score_row(scores: pd.DataFrame, as_of_date: str | pd.Timestamp | None) -> pd.Series:
    if as_of_date is None:
        return scores.iloc[-1]
    timestamp = pd.Timestamp(as_of_date)
    eligible = scores.loc[scores.index <= timestamp]
    if eligible.empty:
        return pd.Series(dtype=float)
    return eligible.iloc[-1]


def _regime_exposure(regime: pd.DataFrame | pd.Series | float | None, as_of_date: str | pd.Timestamp | None) -> float:
    if regime is None:
        return 1.0
    if isinstance(regime, (int, float)):
        return float(regime)
    if isinstance(regime, pd.DataFrame):
        if "target_exposure" not in regime.columns:
            return 1.0
        row = _score_row(regime[["target_exposure"]], as_of_date)
        return float(row["target_exposure"]) if "target_exposure" in row else 1.0
    row = _score_row(regime.to_frame("target_exposure"), as_of_date)
    return float(row["target_exposure"]) if "target_exposure" in row else 1.0
