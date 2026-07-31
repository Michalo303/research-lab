from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import platform
from dataclasses import dataclass
from typing import Any

import pandas as pd


RUNTIME_VERSION = "real_qlib_runtime_v1"
PARITY_VERSION = "real_qlib_preparation_parity_v1"
QLIB_RUNTIME_UNAVAILABLE = "QLIB_RUNTIME_UNAVAILABLE"
QLIB_PREPARATION_PARITY_FAILED = "QLIB_PREPARATION_PARITY_FAILED"


class QlibRuntimeUnavailable(RuntimeError):
    """Raised when the pinned genuine-Qlib runtime cannot be used."""


class QlibPreparationParityError(RuntimeError):
    """Raised when Qlib preparation differs from the direct source slice."""


@dataclass(frozen=True)
class _RealQlibRuntime:
    qlib: Any
    data_handler_lp: Any
    dataset_h: Any
    is_real_qlib: bool = True

    def prepare_segments(
        self,
        frame: pd.DataFrame,
        *,
        feature_columns: tuple[str, ...],
        label_column: str,
        segments: dict[str, tuple[str, str]],
    ) -> dict[str, pd.DataFrame]:
        grouped = pd.concat(
            {
                "feature": frame.loc[:, list(feature_columns)],
                "label": frame.loc[:, [label_column]],
            },
            axis=1,
        )
        handler = self.data_handler_lp.from_df(grouped)
        dataset = self.dataset_h(handler=handler, segments=segments)
        prepared: dict[str, pd.DataFrame] = {}
        for name in segments:
            value = dataset.prepare(
                name,
                col_set=["feature", "label"],
                data_key=self.data_handler_lp.DK_L,
            )
            if not isinstance(value, pd.DataFrame):
                raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
            flattened = value.copy()
            if isinstance(flattened.columns, pd.MultiIndex):
                flattened.columns = [str(column[-1]) for column in flattened.columns]
            else:
                flattened.columns = [str(column) for column in flattened.columns]
            prepared[name] = flattened
        return prepared


def build_real_qlib_runtime_metadata_v1() -> dict[str, object]:
    """Return stable availability metadata without importing action modules."""

    if importlib.util.find_spec("qlib") is None:
        result: dict[str, object] = {
            "version": RUNTIME_VERSION,
            "status": QLIB_RUNTIME_UNAVAILABLE,
            "is_real_qlib": False,
            "qlib_version": "UNAVAILABLE",
            "python_version": platform.python_version(),
        }
    else:
        runtime = _load_real_runtime()
        version = getattr(runtime.qlib, "__version__", None)
        if not isinstance(version, str) or not version.strip():
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        result = {
            "version": RUNTIME_VERSION,
            "status": "AVAILABLE",
            "is_real_qlib": True,
            "qlib_version": version.strip(),
            "python_version": platform.python_version(),
        }
    result["runtime_sha256"] = _canonical_sha256(result)
    return result


def prepare_real_qlib_segments_v1(
    frame: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, pd.DataFrame]:
    """Prepare discovery/development through the internally loaded Qlib runtime."""

    validated_segments = _validate_inputs(frame, feature_columns, label_column, segments)
    selected_runtime = _load_real_runtime()
    if getattr(selected_runtime, "is_real_qlib", None) is not True:
        raise ValueError("runtime must identify itself as real Qlib.")
    prepare = getattr(selected_runtime, "prepare_segments", None)
    if not callable(prepare):
        raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
    try:
        raw = prepare(
            frame.copy(deep=True),
            feature_columns=feature_columns,
            label_column=label_column,
            segments=validated_segments,
        )
    except (QlibRuntimeUnavailable, ValueError):
        raise
    except Exception as exc:
        raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE) from exc
    return _validate_prepared_segments(
        raw,
        feature_columns=feature_columns,
        label_column=label_column,
        segments=validated_segments,
    )


def build_real_qlib_preparation_parity_v1(
    source_frame: pd.DataFrame,
    prepared_segments: dict[str, pd.DataFrame],
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, object]:
    """Require exact Qlib/source parity before economic metrics are trusted."""

    validated_segments = _validate_inputs(source_frame, feature_columns, label_column, segments)
    columns = [*feature_columns, label_column]
    if set(prepared_segments) != set(validated_segments):
        raise QlibPreparationParityError(QLIB_PREPARATION_PARITY_FAILED)

    source_hashes: dict[str, str] = {}
    prepared_hashes: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    dates = source_frame.index.get_level_values("datetime")
    for name in sorted(validated_segments):
        start, end = validated_segments[name]
        expected = source_frame.loc[
            (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end)),
            columns,
        ]
        observed = prepared_segments[name]
        try:
            pd.testing.assert_frame_equal(
                observed,
                expected,
                check_dtype=True,
                check_exact=True,
                check_like=False,
                check_names=True,
                check_index_type=True,
                check_column_type=True,
            )
        except AssertionError as exc:
            raise QlibPreparationParityError(QLIB_PREPARATION_PARITY_FAILED) from exc
        source_hashes[name] = _frame_sha256(expected)
        prepared_hashes[name] = _frame_sha256(observed)
        row_counts[name] = len(expected)

    result: dict[str, object] = {
        "version": PARITY_VERSION,
        "status": "PASS",
        "segment_row_counts": row_counts,
        "source_segment_sha256": source_hashes,
        "prepared_segment_sha256": prepared_hashes,
        "source_frame_sha256": _canonical_sha256(source_hashes),
        "prepared_frame_sha256": _canonical_sha256(prepared_hashes),
    }
    result["canonical_parity_sha256"] = _canonical_sha256(result)
    return result


def _load_real_runtime() -> _RealQlibRuntime:
    if importlib.util.find_spec("qlib") is None:
        raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
    try:
        qlib = importlib.import_module("qlib")
        handler_module = importlib.import_module("qlib.data.dataset.handler")
        dataset_module = importlib.import_module("qlib.data.dataset")
        return _RealQlibRuntime(
            qlib=qlib,
            data_handler_lp=handler_module.DataHandlerLP,
            dataset_h=dataset_module.DatasetH,
        )
    except Exception as exc:
        raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE) from exc


def _validate_inputs(
    frame: pd.DataFrame,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("frame must be a DataFrame.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"]:
        raise ValueError("frame index must be datetime and instrument.")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and sorted.")
    if not isinstance(feature_columns, tuple) or not feature_columns or len(set(feature_columns)) != len(feature_columns):
        raise ValueError("feature_columns must be a nonempty unique tuple.")
    if not isinstance(label_column, str) or not label_column or label_column in feature_columns:
        raise ValueError("label_column is invalid.")
    required = {*feature_columns, label_column}
    if not required.issubset(frame.columns):
        raise ValueError("frame is missing required columns.")
    if not isinstance(segments, dict) or not segments:
        raise ValueError("segments must be a nonempty mapping.")
    validated: dict[str, tuple[str, str]] = {}
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for name, interval in segments.items():
        if not isinstance(name, str) or not name or not isinstance(interval, tuple) or len(interval) != 2:
            raise ValueError("segment is invalid.")
        start = pd.Timestamp(interval[0])
        end = pd.Timestamp(interval[1])
        if start.tz is not None or end.tz is not None or start > end:
            raise ValueError("segment interval is invalid.")
        validated[name] = (start.date().isoformat(), end.date().isoformat())
        intervals.append((start, end))
    ordered = sorted(intervals)
    if any(current[0] <= previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("segment intervals must not overlap.")
    return validated


def _validate_prepared_segments(
    raw: object,
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, pd.DataFrame]:
    if not isinstance(raw, dict) or set(raw) != set(segments):
        raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
    expected_columns = [*feature_columns, label_column]
    result: dict[str, pd.DataFrame] = {}
    for name in segments:
        frame = raw[name]
        if not isinstance(frame, pd.DataFrame):
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        value = frame.copy(deep=True)
        if not isinstance(value.index, pd.MultiIndex) or list(value.index.names) != ["datetime", "instrument"]:
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        if value.index.has_duplicates:
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        if list(value.columns) != expected_columns:
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        if not value.index.is_monotonic_increasing:
            value = value.sort_index(kind="mergesort")
        dates = value.index.get_level_values("datetime")
        start, end = (pd.Timestamp(item) for item in segments[name])
        if len(value) and (dates.min() < start or dates.max() > end):
            raise QlibRuntimeUnavailable(QLIB_RUNTIME_UNAVAILABLE)
        result[name] = value
    return result


def _frame_sha256(frame: pd.DataFrame) -> str:
    metadata = json.dumps(
        {
            "columns": [str(column) for column in frame.columns],
            "dtypes": [str(dtype) for dtype in frame.dtypes],
            "index_names": list(frame.index.names),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    values = pd.util.hash_pandas_object(frame, index=True, categorize=False).to_numpy().tobytes()
    return hashlib.sha256(metadata + values).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
