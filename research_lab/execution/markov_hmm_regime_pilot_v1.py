from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "markov_hmm_regime_pilot_request_v1"
PILOT_VERSION = "markov_hmm_regime_pilot_v1"
MODEL_TYPE = "deterministic_markov_stub"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED_VALIDATION = "FAILED_VALIDATION"


def run_markov_hmm_regime_pilot(request: dict[str, object]) -> dict[str, object]:
    try:
        validated = _validate_request(request)
        bars, source_review_candidate_id = _resolve_input(validated)
        input_hash = _canonical_sha256(
            {
                "bars": bars,
                "parameters": validated["parameters"],
                "source_review_candidate_id": source_review_candidate_id,
            }
        )
        regime_labels = _regime_labels(bars, lookback=int(validated["parameters"]["lookback"]))
        regime_summary = _regime_summary(regime_labels)
        result = _result(
            input_hash=input_hash,
            regime_labels=regime_labels,
            regime_summary=regime_summary,
            drawdown_timing_hint=_drawdown_timing_hint(regime_summary),
            exposure_timing_hint=_exposure_timing_hint(regime_summary),
            final_status=STATUS_COMPLETED,
            failure_reason=None,
            source_review_candidate_id=source_review_candidate_id,
        )
        return result
    except ValueError as exc:
        return _result(
            input_hash=_safe_input_hash(request),
            regime_labels=[],
            regime_summary=None,
            drawdown_timing_hint=None,
            exposure_timing_hint=None,
            final_status=STATUS_FAILED_VALIDATION,
            failure_reason=str(exc),
            source_review_candidate_id=_safe_review_candidate_id(request),
        )


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "input_bars", "review_artifact", "provenance", "parameters"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    input_bars = payload.get("input_bars")
    review_artifact = payload.get("review_artifact")
    if (input_bars is None) == (review_artifact is None):
        raise ValueError("exactly one of input_bars or review_artifact must be provided.")
    return {
        "version": version,
        "input_bars": input_bars,
        "review_artifact": _optional_mapping(review_artifact, name="review_artifact"),
        "provenance": _validate_provenance(payload.get("provenance")),
        "parameters": _validate_parameters(payload.get("parameters")),
    }


def _resolve_input(validated: dict[str, Any]) -> tuple[list[dict[str, float | str]], str | None]:
    if validated["input_bars"] is not None:
        return _validate_input_bars(validated["input_bars"]), None
    review_artifact = validated["review_artifact"]
    if review_artifact is None:
        raise ValueError("review_artifact must be provided when input_bars is absent.")
    if str(review_artifact.get("version") or "") != "result_review_gate_result_v1":
        raise ValueError("review_artifact.version must be result_review_gate_result_v1.")
    candidate_id = _required_text(review_artifact, "candidate_id")
    adapter_result = review_artifact.get("adapter_result")
    if not isinstance(adapter_result, dict):
        raise ValueError("review_artifact.adapter_result must be an object.")
    return _validate_input_bars(adapter_result.get("synthetic_bars")), candidate_id


def _validate_input_bars(value: Any) -> list[dict[str, float | str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("input_bars must be a non-empty list.")
    normalized: list[dict[str, float | str]] = []
    previous_timestamp: str | None = None
    for item in value:
        payload = _required_mapping(item, name="input_bars item")
        _reject_unknown_fields(payload, allowed={"timestamp", "open", "high", "low", "close"}, name="input_bars item")
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("input_bars timestamps must be strictly increasing.")
        open_price = _required_positive_number(payload, "open")
        high_price = _required_positive_number(payload, "high")
        low_price = _required_positive_number(payload, "low")
        close_price = _required_positive_number(payload, "close")
        if high_price < max(open_price, low_price, close_price):
            raise ValueError("input_bars high must be greater than or equal to open, low, and close.")
        if low_price > min(open_price, high_price, close_price):
            raise ValueError("input_bars low must be less than or equal to open, high, and close.")
        normalized.append(
            {
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_parameters(value: Any) -> dict[str, int]:
    if value is None:
        return {"lookback": 3}
    payload = _required_mapping(value, name="parameters")
    _reject_unknown_fields(payload, allowed={"lookback"}, name="parameters")
    lookback = payload.get("lookback")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback <= 0:
        raise ValueError("parameters.lookback must be a positive integer.")
    return {"lookback": lookback}


def _regime_labels(bars: list[dict[str, float | str]], *, lookback: int) -> list[dict[str, Any]]:
    closes = [float(item["close"]) for item in bars]
    returns = [0.0]
    for index in range(1, len(closes)):
        returns.append((closes[index] / closes[index - 1]) - 1.0)
    labels: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        start = max(0, index - lookback + 1)
        window = returns[start : index + 1]
        average_return = sum(window) / len(window)
        if average_return > 0.005:
            regime = "bull"
        elif average_return < -0.005:
            regime = "bear"
        else:
            regime = "sideways"
        labels.append(
            {
                "timestamp": str(bar["timestamp"]),
                "regime_label": regime,
                "window_return_mean": round(average_return, 10),
            }
        )
    return labels


def _regime_summary(regime_labels: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in regime_labels:
        label = str(item["regime_label"])
        counts[label] = counts.get(label, 0) + 1
    return ";".join(f"{label}:{count}" for label, count in sorted(counts.items()))


def _drawdown_timing_hint(regime_summary: str) -> str:
    if "bear:" in regime_summary:
        return "drawdown_risk_tends_to_cluster_in_bear_labels"
    return "no_bear_cluster_detected"


def _exposure_timing_hint(regime_summary: str) -> str:
    if "bull:" in regime_summary and "bear:" in regime_summary:
        return "review_lower_exposure_during_bear_and_higher_during_bull"
    if "bull:" in regime_summary:
        return "bull_only_sample_consider_baseline_exposure"
    return "review_neutral_or_defensive_exposure"


def _result(
    *,
    input_hash: str,
    regime_labels: list[dict[str, Any]],
    regime_summary: str | None,
    drawdown_timing_hint: str | None,
    exposure_timing_hint: str | None,
    final_status: str,
    failure_reason: str | None,
    source_review_candidate_id: str | None,
) -> dict[str, Any]:
    result = {
        "regime_pilot_version": PILOT_VERSION,
        "regime_model_type": MODEL_TYPE,
        "input_hash": input_hash,
        "regime_labels": regime_labels,
        "regime_summary": regime_summary,
        "drawdown_timing_hint": drawdown_timing_hint,
        "exposure_timing_hint": exposure_timing_hint,
        "final_status": final_status,
        "failure_reason": failure_reason,
        "source_review_candidate_id": source_review_candidate_id,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _safe_input_hash(request: Any) -> str:
    try:
        return _canonical_sha256(request)
    except Exception:
        return hashlib.sha256(repr(request).encode("utf-8", errors="replace")).hexdigest()


def _safe_review_candidate_id(request: Any) -> str | None:
    if not isinstance(request, dict):
        return None
    artifact = request.get("review_artifact")
    if not isinstance(artifact, dict):
        return None
    value = artifact.get("candidate_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _optional_mapping(value: Any, *, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _required_mapping(value, name=name)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_positive_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    if number <= 0:
        raise ValueError(f"{field} must be positive.")
    return number


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
