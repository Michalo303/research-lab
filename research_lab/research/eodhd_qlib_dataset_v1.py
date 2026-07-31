from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REQUEST_VERSION = "eodhd_qlib_development_frame_request_v1"
MANIFEST_VERSION = "eodhd_qlib_dataset_manifest_v1"
METADATA_VERSION = "eodhd_qlib_dataset_metadata_v1"
PROVENANCE_SOURCE = "operator_approved_local_snapshot"
CSV_COLUMNS = ("timestamp", "open", "high", "low", "close", "adjusted_close", "volume")
ALLOWED_EXCHANGE_MICS = frozenset({"XNAS", "XNYS", "XASE"})

_REQUEST_FIELDS = {
    "version",
    "manifest_path",
    "expected_manifest_sha256",
    "discovery_interval",
    "development_interval",
    "sealed_oos_interval",
    "universe",
    "provenance",
}
_MANIFEST_FIELDS = {"version", "dataset_id", "created_utc", "instruments", "provenance"}
_INSTRUMENT_FIELDS = {
    "instrument_id",
    "symbol",
    "qlib_instrument",
    "instrument_type",
    "exchange_mic",
    "listing_start",
    "listing_end",
    "ohlcv_path",
    "ohlcv_sha256",
}
_UNIVERSE_FIELDS = {
    "minimum_price",
    "minimum_history_sessions",
    "minimum_median_dollar_volume",
    "maximum_instruments",
}


def load_eodhd_qlib_development_frame_v1(
    request: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Load hash-bound local files and return development-visible rows only."""

    validated = _validate_request(request)
    manifest_path = Path(validated["manifest_path"])
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("manifest_path must identify a regular non-symlink file.")
    manifest_bytes = manifest_path.read_bytes()
    if _bytes_sha256(manifest_bytes) != validated["expected_manifest_sha256"]:
        raise ValueError("expected_manifest_sha256 mismatch.")
    try:
        manifest_raw = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest is not valid UTF-8 JSON.") from exc
    manifest = _validate_manifest(manifest_raw)

    root = manifest_path.parent.resolve()
    frames: list[pd.DataFrame] = []
    file_hashes: dict[str, str] = {}
    discovery_start = pd.Timestamp(validated["discovery_interval"]["start"])
    development_end = pd.Timestamp(validated["development_interval"]["end"])
    sealed_start = pd.Timestamp(validated["sealed_oos_interval"]["start"])

    for instrument in manifest["instruments"]:
        relative = Path(instrument["ohlcv_path"])
        if relative.is_absolute():
            raise ValueError("ohlcv_path must be relative.")
        unresolved = root / relative
        if unresolved.is_symlink():
            raise ValueError("ohlcv_path must not be a symlink.")
        resolved = unresolved.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("ohlcv_path escapes manifest directory.") from exc
        if not resolved.is_file():
            raise ValueError("ohlcv_path must identify a regular file.")
        body = resolved.read_bytes()
        observed_sha256 = _bytes_sha256(body)
        if observed_sha256 != instrument["ohlcv_sha256"]:
            raise ValueError("ohlcv_sha256 mismatch.")
        file_hashes[instrument["instrument_id"]] = observed_sha256
        frame = _read_instrument_csv(body)
        if (frame["timestamp"] >= sealed_start).any():
            raise ValueError("sealed OOS row exposed")
        if (frame["timestamp"] > development_end).any():
            raise ValueError("row exceeds development interval.")
        frame = frame.loc[frame["timestamp"] >= discovery_start].copy()
        if frame.empty:
            raise ValueError("instrument has no discovery or development rows.")
        frame["instrument"] = instrument["qlib_instrument"]
        frame["instrument_id"] = instrument["instrument_id"]
        frame["instrument_type"] = instrument["instrument_type"]
        frame["exchange_mic"] = instrument["exchange_mic"]
        frame["listing_start"] = pd.Timestamp(instrument["listing_start"])
        frame["listing_end"] = (
            pd.NaT if instrument["listing_end"] is None else pd.Timestamp(instrument["listing_end"])
        )
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["instrument", "timestamp"], kind="mergesort").reset_index(drop=True)
    combined["prior_session_count"] = combined.groupby("instrument", sort=False).cumcount()
    combined["trailing_median_dollar_volume"] = combined.groupby("instrument", sort=False)[
        "dollar_volume"
    ].transform(lambda values: values.rolling(63, min_periods=63).median())
    universe = validated["universe"]
    within_listing = (combined["timestamp"] >= combined["listing_start"]) & (
        combined["listing_end"].isna() | (combined["timestamp"] <= combined["listing_end"])
    )
    preliminary = (
        (combined["raw_close"] >= universe["minimum_price"])
        & (combined["prior_session_count"] >= universe["minimum_history_sessions"])
        & (combined["trailing_median_dollar_volume"] >= universe["minimum_median_dollar_volume"])
        & within_listing
        & (combined["instrument_type"] == "COMMON_STOCK")
        & combined["exchange_mic"].isin(ALLOWED_EXCHANGE_MICS)
    )
    combined["eligible"] = False
    for _, indexes in combined.loc[preliminary].groupby("timestamp", sort=True).groups.items():
        selected = combined.loc[indexes].sort_values(
            ["trailing_median_dollar_volume", "instrument"],
            ascending=[False, True],
            kind="mergesort",
        )
        selected = selected.head(universe["maximum_instruments"])
        combined.loc[selected.index, "eligible"] = True

    output = combined.set_index(["timestamp", "instrument"])[
        ["open", "high", "low", "close", "volume", "raw_close", "dollar_volume", "eligible"]
    ]
    output.index = output.index.set_names(["datetime", "instrument"])
    output = output.sort_index(kind="mergesort")
    output["eligible"] = output["eligible"].astype(bool)

    instruments = manifest["instruments"]
    metadata: dict[str, object] = {
        "version": METADATA_VERSION,
        "dataset_id": manifest["dataset_id"],
        "dataset_manifest_sha256": validated["expected_manifest_sha256"],
        "instrument_count": len(instruments),
        "active_instrument_count": sum(item["listing_end"] is None for item in instruments),
        "delisted_instrument_count": sum(item["listing_end"] is not None for item in instruments),
        "minimum_timestamp": output.index.get_level_values("datetime").min().date().isoformat(),
        "maximum_timestamp": output.index.get_level_values("datetime").max().date().isoformat(),
        "row_count": len(output),
        "eligible_row_count": int(output["eligible"].sum()),
        "input_file_sha256": dict(sorted(file_hashes.items())),
        "provider_calls_used": 0,
        "sealed_oos_rows_read": 0,
    }
    metadata["canonical_metadata_sha256"] = _canonical_sha256(metadata)
    return output, metadata


def _validate_request(raw: Any) -> dict[str, Any]:
    value = _closed_mapping(raw, _REQUEST_FIELDS, "request")
    if _text(value, "version") != REQUEST_VERSION:
        raise ValueError("unsupported request version.")
    manifest_path = Path(_text(value, "manifest_path"))
    if not manifest_path.is_absolute():
        raise ValueError("manifest_path must be absolute.")
    expected_sha256 = _sha256(value.get("expected_manifest_sha256"), "expected_manifest_sha256")
    discovery = _interval(value.get("discovery_interval"), "discovery_interval")
    development = _interval(value.get("development_interval"), "development_interval")
    sealed = _sealed_interval(value.get("sealed_oos_interval"))
    if pd.Timestamp(discovery["end"]) >= pd.Timestamp(development["start"]):
        raise ValueError("discovery and development intervals overlap or are out of order.")
    if pd.Timestamp(development["end"]) >= pd.Timestamp(sealed["start"]):
        raise ValueError("development and sealed intervals overlap or are out of order.")
    universe_raw = _closed_mapping(value.get("universe"), _UNIVERSE_FIELDS, "universe")
    universe = {
        "minimum_price": _positive_number(universe_raw, "minimum_price"),
        "minimum_history_sessions": _positive_integer(universe_raw, "minimum_history_sessions"),
        "minimum_median_dollar_volume": _positive_number(
            universe_raw, "minimum_median_dollar_volume"
        ),
        "maximum_instruments": _positive_integer(universe_raw, "maximum_instruments"),
    }
    provenance = _provenance(value.get("provenance"))
    return {
        "version": REQUEST_VERSION,
        "manifest_path": str(manifest_path),
        "expected_manifest_sha256": expected_sha256,
        "discovery_interval": discovery,
        "development_interval": development,
        "sealed_oos_interval": sealed,
        "universe": universe,
        "provenance": provenance,
    }


def _validate_manifest(raw: Any) -> dict[str, Any]:
    value = _closed_mapping(raw, _MANIFEST_FIELDS, "manifest")
    if _text(value, "version") != MANIFEST_VERSION:
        raise ValueError("unsupported manifest version.")
    dataset_id = _text(value, "dataset_id")
    created_utc = _text(value, "created_utc")
    if not created_utc.endswith("Z"):
        raise ValueError("created_utc must be UTC.")
    raw_instruments = value.get("instruments")
    if not isinstance(raw_instruments, list) or not raw_instruments:
        raise ValueError("instruments must be a nonempty list.")
    instruments: list[dict[str, Any]] = []
    for raw_instrument in raw_instruments:
        item = _closed_mapping(raw_instrument, _INSTRUMENT_FIELDS, "instrument")
        listing_start = _date_text(item.get("listing_start"), "listing_start")
        listing_end_raw = item.get("listing_end")
        listing_end = None if listing_end_raw is None else _date_text(listing_end_raw, "listing_end")
        if listing_end is not None and pd.Timestamp(listing_end) < pd.Timestamp(listing_start):
            raise ValueError("listing interval is invalid.")
        instruments.append(
            {
                "instrument_id": _text(item, "instrument_id"),
                "symbol": _text(item, "symbol"),
                "qlib_instrument": _text(item, "qlib_instrument"),
                "instrument_type": _text(item, "instrument_type"),
                "exchange_mic": _text(item, "exchange_mic"),
                "listing_start": listing_start,
                "listing_end": listing_end,
                "ohlcv_path": _text(item, "ohlcv_path"),
                "ohlcv_sha256": _sha256(item.get("ohlcv_sha256"), "ohlcv_sha256"),
            }
        )
    for field in ("instrument_id", "symbol", "qlib_instrument"):
        identities = [item[field] for item in instruments]
        if len(identities) != len(set(identities)):
            raise ValueError(f"duplicate {field}.")
    return {
        "version": MANIFEST_VERSION,
        "dataset_id": dataset_id,
        "created_utc": created_utc,
        "instruments": instruments,
        "provenance": _provenance(value.get("provenance")),
    }


def _read_instrument_csv(body: bytes) -> pd.DataFrame:
    try:
        from io import BytesIO

        frame = pd.read_csv(BytesIO(body))
    except Exception as exc:
        raise ValueError("OHLCV CSV cannot be parsed.") from exc
    if tuple(frame.columns) != CSV_COLUMNS:
        raise ValueError("OHLCV CSV columns are invalid.")
    if frame.empty:
        raise ValueError("OHLCV CSV must not be empty.")
    raw_timestamps = frame["timestamp"].copy()
    try:
        timestamps = pd.to_datetime(raw_timestamps, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp is invalid.") from exc
    if timestamps.duplicated().any() or not timestamps.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and strictly increasing.")
    numeric_columns = [column for column in CSV_COLUMNS if column != "timestamp"]
    for column in numeric_columns:
        try:
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{column} is invalid.") from exc
    numeric = frame[numeric_columns].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all():
        raise ValueError("OHLCV values must be finite.")
    if (frame[["open", "high", "low", "close", "adjusted_close"]] <= 0.0).any().any():
        raise ValueError("OHLC values must be positive.")
    if (frame["volume"] < 0.0).any():
        raise ValueError("volume must be non-negative.")
    if (
        (frame["high"] < frame[["open", "close"]].max(axis=1)).any()
        or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
        or (frame["high"] < frame["low"]).any()
    ):
        raise ValueError("OHLC relationship is invalid.")
    raw_close = frame["close"].copy()
    ratio = frame["adjusted_close"] / raw_close
    frame["open"] = frame["open"] * ratio
    frame["high"] = frame["high"] * ratio
    frame["low"] = frame["low"] * ratio
    frame["close"] = frame["adjusted_close"]
    frame["raw_close"] = raw_close
    frame["dollar_volume"] = raw_close * frame["volume"]
    frame["timestamp"] = timestamps
    return frame[["timestamp", "open", "high", "low", "close", "volume", "raw_close", "dollar_volume"]]


def _closed_mapping(raw: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a mapping.")
    if set(raw) != fields:
        raise ValueError(f"{name} fields are invalid.")
    return dict(raw)


def _interval(raw: Any, name: str) -> dict[str, str]:
    value = _closed_mapping(raw, {"start", "end"}, name)
    start = _date_text(value.get("start"), f"{name}.start")
    end = _date_text(value.get("end"), f"{name}.end")
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError(f"{name} is out of order.")
    return {"start": start, "end": end}


def _sealed_interval(raw: Any) -> dict[str, str]:
    value = _closed_mapping(raw, {"dataset_version", "start", "end"}, "sealed_oos_interval")
    result = _interval({"start": value["start"], "end": value["end"]}, "sealed_oos_interval")
    return {"dataset_version": _text(value, "dataset_version"), **result}


def _provenance(raw: Any) -> dict[str, str]:
    value = _closed_mapping(raw, {"source"}, "provenance")
    if _text(value, "source") != PROVENANCE_SOURCE:
        raise ValueError("provenance source is invalid.")
    return {"source": PROVENANCE_SOURCE}


def _date_text(raw: Any, name: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{name} must be a date string.")
    try:
        value = pd.Timestamp(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid.") from exc
    if value.tz is not None or value.date().isoformat() != raw:
        raise ValueError(f"{name} must use YYYY-MM-DD.")
    return raw


def _text(value: dict[str, Any], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be nonempty text.")
    return raw.strip()


def _sha256(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"{name} must be lowercase SHA-256.")
    return raw


def _positive_number(value: dict[str, Any], name: str) -> float:
    raw = value.get(name)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)) or raw <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return float(raw)


def _positive_integer(value: dict[str, Any], name: str) -> int:
    raw = value.get(name)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return raw


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
