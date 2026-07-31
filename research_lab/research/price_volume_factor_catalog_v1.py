from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import numpy as np
import pandas as pd


FACTOR_CATALOG_VERSION = "price_volume_factor_catalog_v1"
LABEL_ID = "forward_return_5d"
SOURCE_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "raw_close",
    "dollar_volume",
    "eligible",
}

FACTOR_DEFINITIONS_V1: dict[str, dict[str, object]] = {
    "MOM_12_1": {
        "factor_id": "MOM_12_1",
        "family_id": "MOMENTUM",
        "description": "Adjusted-close return from 252 sessions ago through 21 sessions ago.",
        "higher_is_better": True,
    },
    "MOM_6_1": {
        "factor_id": "MOM_6_1",
        "family_id": "MOMENTUM",
        "description": "Adjusted-close return from 126 sessions ago through 21 sessions ago.",
        "higher_is_better": True,
    },
    "TREND_200": {
        "factor_id": "TREND_200",
        "family_id": "TREND",
        "description": "Adjusted close relative to its trailing 200-session mean.",
        "higher_is_better": True,
    },
    "HIGH_252": {
        "factor_id": "HIGH_252",
        "family_id": "TREND",
        "description": "Adjusted close relative to the trailing 252-session adjusted high.",
        "higher_is_better": True,
    },
    "LOW_VOL_60": {
        "factor_id": "LOW_VOL_60",
        "family_id": "RISK",
        "description": "Negative annualized trailing 60-session adjusted-close volatility.",
        "higher_is_better": True,
    },
    "DRAWDOWN_252": {
        "factor_id": "DRAWDOWN_252",
        "family_id": "RISK",
        "description": "Adjusted close relative to the trailing 252-session adjusted-close maximum.",
        "higher_is_better": True,
    },
    "VOLUME_CONFIRM_20": {
        "factor_id": "VOLUME_CONFIRM_20",
        "family_id": "VOLUME",
        "description": "Raw dollar volume relative to its trailing 20-session mean.",
        "higher_is_better": True,
    },
    "SHORT_REVERSAL_5": {
        "factor_id": "SHORT_REVERSAL_5",
        "family_id": "REVERSAL",
        "description": "Negative adjusted-close return over the trailing five sessions.",
        "higher_is_better": True,
    },
}


def compute_price_volume_factor_frame_v1(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the eight features, forward label, and eligibility on the source index."""

    _validate_source_frame(frame)
    close = frame["close"].astype("float64")
    high = frame["high"].astype("float64")
    open_price = frame["open"].astype("float64")
    dollar_volume = frame["dollar_volume"].astype("float64")
    close_group = close.groupby(level="instrument", sort=False)
    open_group = open_price.groupby(level="instrument", sort=False)

    result = pd.DataFrame(index=frame.index.copy())
    result["MOM_12_1"] = close_group.shift(21) / close_group.shift(252) - 1.0
    result["MOM_6_1"] = close_group.shift(21) / close_group.shift(126) - 1.0
    result["TREND_200"] = close / _transform(close, lambda values: values.rolling(200, min_periods=200).mean()) - 1.0
    result["HIGH_252"] = close / _transform(high, lambda values: values.rolling(252, min_periods=252).max()) - 1.0
    result["LOW_VOL_60"] = -_transform(
        close,
        lambda values: values.pct_change(fill_method=None).rolling(60, min_periods=60).std()
        * np.sqrt(252.0),
    )
    result["DRAWDOWN_252"] = close / _transform(
        close, lambda values: values.rolling(252, min_periods=252).max()
    ) - 1.0
    result["VOLUME_CONFIRM_20"] = dollar_volume / _transform(
        dollar_volume, lambda values: values.rolling(20, min_periods=20).mean()
    ) - 1.0
    result["SHORT_REVERSAL_5"] = -(close / close_group.shift(5) - 1.0)
    result[LABEL_ID] = close_group.shift(-5) / open_group.shift(-1) - 1.0
    result["eligible"] = frame["eligible"].astype(bool)
    return result


def build_price_volume_factor_catalog_metadata_v1() -> dict[str, object]:
    """Return canonical ordered metadata for the frozen factor catalog."""

    result: dict[str, object] = {
        "version": FACTOR_CATALOG_VERSION,
        "factor_count": len(FACTOR_DEFINITIONS_V1),
        "ordered_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "definitions": [dict(FACTOR_DEFINITIONS_V1[factor_id]) for factor_id in FACTOR_DEFINITIONS_V1],
        "label": {
            "label_id": LABEL_ID,
            "definition": "close.shift(-5) / open.shift(-1) - 1.0 per instrument",
            "execution_timing": "next_session_open_to_fifth_future_close",
        },
    }
    result["canonical_catalog_sha256"] = _canonical_sha256(result)
    return result


def _transform(series: pd.Series, operation: Callable[[pd.Series], pd.Series]) -> pd.Series:
    return series.groupby(level="instrument", sort=False, group_keys=False).transform(operation)


def _validate_source_frame(frame: Any) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("frame must be a DataFrame.")
    if set(frame.columns) != SOURCE_COLUMNS:
        raise ValueError("source columns are invalid.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"]:
        raise ValueError("source index must be datetime and instrument.")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("source index must be unique and sorted.")
    datetimes = frame.index.get_level_values("datetime")
    if not isinstance(datetimes, pd.DatetimeIndex) or datetimes.tz is not None:
        raise ValueError("datetime index must be timezone-naive.")
    numeric_columns = sorted(SOURCE_COLUMNS - {"eligible"})
    try:
        numeric = frame[numeric_columns].to_numpy(dtype="float64")
    except (TypeError, ValueError) as exc:
        raise ValueError("source numeric values are invalid.") from exc
    if not np.isfinite(numeric).all():
        raise ValueError("source numeric values must be finite.")
    if not pd.api.types.is_bool_dtype(frame["eligible"]):
        raise ValueError("eligible must be boolean.")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
