from __future__ import annotations

import hashlib
import json
import math
from statistics import mean, pstdev
from typing import Any


REQUEST_VERSION = "macro_feature_set_contract_request_v1"
RESULT_VERSION = "macro_feature_set_contract_result_v1"
CONTRACT_VERSION = "macro_feature_set_contract_v1"
_OPERATIONS = {
    "level",
    "first_difference",
    "percentage_change",
    "rolling_mean",
    "rolling_stddev",
    "z_score",
    "slope",
    "spread",
    "threshold_state",
    "bounded_categorical_state",
}


def build_macro_feature_set_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    feature_rows = _build_feature_rows(validated)
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "status": "SUCCESS",
        "feature_observations": feature_rows,
        "feature_definitions": validated["feature_definitions"],
        "feature_availability_timestamps": {
            row["timestamp"]: row["feature_availability_timestamps_utc"] for row in feature_rows
        },
        "source_lineage": validated["source_series_identities"],
        "warm_up_periods": {
            feature["feature_id"]: max(0, int(feature["minimum_observations"]) - 1)
            for feature in validated["feature_definitions"]
        },
        "missing_indicators": {
            row["timestamp"]: row["missing_indicators"] for row in feature_rows
        },
        "deterministic_feature_hash": _canonical_sha256(feature_rows),
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
        "input_sha256": _canonical_sha256(validated["hashable_request"]),
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _build_feature_rows(validated: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    aligned_bars = validated["aligned_macro_result"]["aligned_bars"]
    for index, bar in enumerate(aligned_bars):
        feature_values: dict[str, float | str | None] = {}
        feature_availability: dict[str, str | None] = {}
        missing_indicators: dict[str, bool] = {}
        for feature in validated["feature_definitions"]:
            value, availability, missing = _compute_feature(
                feature,
                aligned_bars=aligned_bars,
                index=index,
                missing_data_policy=validated["missing_data_policy"],
            )
            feature_values[feature["feature_id"]] = value
            feature_availability[feature["feature_id"]] = availability
            missing_indicators[feature["feature_id"]] = missing
        rows.append(
            {
                "timestamp": bar["timestamp"],
                "feature_values": feature_values,
                "feature_availability_timestamps_utc": feature_availability,
                "missing_indicators": missing_indicators,
            }
        )
    return rows


def _compute_feature(
    feature: dict[str, Any],
    *,
    aligned_bars: list[dict[str, Any]],
    index: int,
    missing_data_policy: str,
) -> tuple[float | str | None, str | None, bool]:
    operation = feature["operation"]
    if operation == "spread":
        series_ids = [feature["left_source_series_id"], feature["right_source_series_id"]]
    else:
        series_ids = [feature["source_series_id"]]
    current_bar = aligned_bars[index]
    histories = {series_id: _history(aligned_bars, index=index, series_id=series_id) for series_id in series_ids}
    availability = _feature_availability(current_bar, series_ids)

    minimum_observations = int(feature["minimum_observations"])
    if any(len(values) < minimum_observations for values in histories.values()):
        if missing_data_policy == "MARK_MISSING":
            return None, availability, True
        raise ValueError(f"insufficient history for feature {feature['feature_id']}.")

    try:
        value = _evaluate_operation(feature, histories)
    except ValueError:
        raise
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite result for feature {feature['feature_id']}.")
    return value, availability, False


def _evaluate_operation(feature: dict[str, Any], histories: dict[str, list[float]]) -> float | str:
    operation = feature["operation"]
    if operation not in _OPERATIONS:
        raise ValueError(f"unknown operation: {operation}")
    if operation == "level":
        return histories[feature["source_series_id"]][-1]
    if operation == "first_difference":
        values = histories[feature["source_series_id"]]
        return values[-1] - values[-2]
    if operation == "percentage_change":
        values = histories[feature["source_series_id"]]
        if values[-2] == 0:
            raise ValueError("percentage_change requires a non-zero prior value.")
        return (values[-1] - values[-2]) / values[-2]
    if operation in {"rolling_mean", "rolling_stddev", "z_score", "slope"}:
        values = _window(histories[feature["source_series_id"]], feature)
        if operation == "rolling_mean":
            return mean(values)
        if operation == "rolling_stddev":
            return pstdev(values)
        if operation == "z_score":
            stddev = pstdev(values)
            if stddev == 0:
                raise ValueError("zero variance is not allowed for z_score.")
            return (values[-1] - mean(values)) / stddev
        return _slope(values)
    if operation == "spread":
        left = histories[feature["left_source_series_id"]][-1]
        right = histories[feature["right_source_series_id"]][-1]
        return left - right
    if operation == "threshold_state":
        return 1.0 if histories[feature["source_series_id"]][-1] >= float(feature["threshold"]) else 0.0
    values = histories[feature["source_series_id"]]
    current = values[-1]
    bounds = [float(item) for item in feature["bounds"]]
    labels = list(feature["labels"])
    for idx, bound in enumerate(bounds):
        if current < bound:
            return labels[idx]
    return labels[-1]


def _window(values: list[float], feature: dict[str, Any]) -> list[float]:
    lookback = int(feature["lookback_window"])
    if len(values) < lookback:
        raise ValueError(f"insufficient history for feature {feature['feature_id']}.")
    return values[-lookback:]


def _slope(values: list[float]) -> float:
    n = len(values)
    x_mean = (n - 1) / 2
    y_mean = mean(values)
    numerator = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values))
    denominator = sum((idx - x_mean) ** 2 for idx in range(n))
    if denominator == 0:
        raise ValueError("insufficient history for slope.")
    return numerator / denominator


def _history(aligned_bars: list[dict[str, Any]], *, index: int, series_id: str) -> list[float]:
    values: list[float] = []
    for bar in aligned_bars[: index + 1]:
        value = bar["macro_values"].get(series_id)
        if value is None:
            continue
        values.append(float(value))
    return values


def _feature_availability(bar: dict[str, Any], series_ids: list[str]) -> str | None:
    timestamps = [bar["availability_timestamps_utc"].get(series_id) for series_id in series_ids]
    available = [item for item in timestamps if item is not None]
    if not available:
        return None
    return max(available)


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "aligned_macro_result", "feature_definitions", "missing_data_policy", "clipping_policy", "provenance"},
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    aligned = _validate_aligned_result(payload.get("aligned_macro_result"))
    missing_data_policy = _required_text(payload, "missing_data_policy")
    if missing_data_policy not in {"MARK_MISSING", "ERROR"}:
        raise ValueError("missing_data_policy must be MARK_MISSING or ERROR.")
    _validate_clipping_policy(payload.get("clipping_policy"))
    feature_definitions = _validate_feature_definitions(
        payload.get("feature_definitions"),
        source_series_identities=set(aligned["source_series_identities"]),
    )
    hashable_request = {
        "version": REQUEST_VERSION,
        "aligned_macro_result": aligned,
        "feature_definitions": feature_definitions,
        "missing_data_policy": missing_data_policy,
        "clipping_policy": {"mode": "NONE"},
        "provenance": _validate_provenance(payload.get("provenance")),
    }
    return {
        **hashable_request,
        "source_series_identities": aligned["source_series_identities"],
        "hashable_request": hashable_request,
    }


def _validate_aligned_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="aligned_macro_result")
    if payload.get("version") != "macro_market_asof_alignment_contract_result_v1":
        raise ValueError("aligned_macro_result.version must be macro_market_asof_alignment_contract_result_v1.")
    if payload.get("contract_version") != "macro_market_asof_alignment_contract_v1":
        raise ValueError("aligned_macro_result.contract_version must be macro_market_asof_alignment_contract_v1.")
    if payload.get("status") != "SUCCESS":
        raise ValueError("aligned_macro_result.status must be SUCCESS.")
    rows = _required_list(payload.get("aligned_bars"), name="aligned_macro_result.aligned_bars")
    normalized_rows: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for raw in rows:
        row = _required_mapping(raw, name="aligned bar")
        timestamp = _required_text(row, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("aligned_macro_result.aligned_bars must be strictly ordered.")
        previous_timestamp = timestamp
        normalized_rows.append(row)
    identities = list(_required_list(payload.get("source_series_identities"), name="source_series_identities"))
    return {
        "version": payload["version"],
        "contract_version": payload["contract_version"],
        "status": payload["status"],
        "aligned_bars": normalized_rows,
        "source_series_identities": identities,
        "output_payload_sha256": _required_text(payload, "output_payload_sha256"),
    }


def _validate_feature_definitions(value: Any, *, source_series_identities: set[str]) -> list[dict[str, Any]]:
    definitions = _required_list(value, name="feature_definitions")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in definitions:
        payload = _required_mapping(raw, name="feature_definition")
        feature_id = _required_text(payload, "feature_id")
        if feature_id in seen_ids:
            raise ValueError("duplicate feature_id is not allowed.")
        seen_ids.add(feature_id)
        operation = _required_text(payload, "operation")
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown operation: {operation}")
        minimum_observations = _required_positive_int(payload, "minimum_observations")
        normalized_payload = {
            "feature_id": feature_id,
            "operation": operation,
            "minimum_observations": minimum_observations,
        }
        if operation == "spread":
            left = _required_text(payload, "left_source_series_id")
            right = _required_text(payload, "right_source_series_id")
            if left not in source_series_identities or right not in source_series_identities:
                raise ValueError("identity mismatch in feature definition.")
            normalized_payload["left_source_series_id"] = left
            normalized_payload["right_source_series_id"] = right
        else:
            source = _required_text(payload, "source_series_id")
            if source not in source_series_identities:
                raise ValueError("identity mismatch in feature definition.")
            normalized_payload["source_series_id"] = source
        if operation in {"rolling_mean", "rolling_stddev", "z_score", "slope"}:
            normalized_payload["lookback_window"] = _required_positive_int(payload, "lookback_window")
        if operation == "threshold_state":
            normalized_payload["threshold"] = _required_finite_number(payload, "threshold")
        if operation == "bounded_categorical_state":
            bounds = _required_list(payload.get("bounds"), name="bounds")
            labels = _required_list(payload.get("labels"), name="labels")
            if len(labels) != len(bounds) + 1:
                raise ValueError("labels length must equal bounds length plus one.")
            normalized_payload["bounds"] = [_required_number_value(item, name="bound") for item in bounds]
            normalized_payload["labels"] = [_required_text_value(item, name="label") for item in labels]
        normalized.append(normalized_payload)
    return normalized


def _validate_clipping_policy(value: Any) -> None:
    payload = _required_mapping(value, name="clipping_policy")
    _reject_unknown_fields(payload, allowed={"mode"}, name="clipping_policy")
    if _required_text(payload, "mode") != "NONE":
        raise ValueError("only clipping_policy.mode=NONE is supported.")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_provenance(value: Any) -> dict[str, str | int | float | bool | None]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    return _required_number_value(payload.get(field), name=field)


def _required_number_value(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _required_text_value(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    return value.strip()


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
