from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from typing import Any


REQUEST_VERSION = "qlib_isolated_evaluator_request_v1"
EVALUATOR_VERSION = "qlib_isolated_evaluator_v1"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_COMPLETED_LOCAL_STUB = "COMPLETED_LOCAL_STUB"
STATUS_FAILED_VALIDATION = "FAILED_VALIDATION"


def run_qlib_isolated_evaluator(request: dict[str, object]) -> dict[str, object]:
    qlib_available = importlib.util.find_spec("qlib") is not None
    try:
        validated = _validate_request(request)
        input_hash = _canonical_sha256(validated)
        bars, source_review_candidate_id = _resolve_input_bars(validated)
        if validated["evaluation_mode"] == "availability_check" and not qlib_available:
            return _result(
                qlib_available=qlib_available,
                evaluation_run=False,
                input_hash=input_hash,
                metrics=None,
                failure_reason="qlib_unavailable",
                final_status=STATUS_UNAVAILABLE,
                input_source_type=validated["input_type"],
                source_review_candidate_id=source_review_candidate_id,
            )
        metrics = _deterministic_local_metrics(bars)
        return _result(
            qlib_available=qlib_available,
            evaluation_run=True,
            input_hash=input_hash,
            metrics=metrics,
            failure_reason=None,
            final_status=STATUS_COMPLETED_LOCAL_STUB,
            input_source_type=validated["input_type"],
            source_review_candidate_id=source_review_candidate_id,
        )
    except ValueError as exc:
        return _result(
            qlib_available=qlib_available,
            evaluation_run=False,
            input_hash=_safe_input_hash(request),
            metrics=None,
            failure_reason=str(exc),
            final_status=STATUS_FAILED_VALIDATION,
            input_source_type=_safe_text((request or {}).get("input_type")) if isinstance(request, dict) else None,
            source_review_candidate_id=_safe_review_candidate_id(request),
        )


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "input_type", "symbol", "normalized_bars", "review_artifact", "evaluation_mode", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    input_type = _required_text(payload, "input_type")
    if input_type not in {"normalized_bars", "review_artifact"}:
        raise ValueError("input_type must be normalized_bars or review_artifact.")
    evaluation_mode = _required_text(payload, "evaluation_mode")
    if evaluation_mode not in {"availability_check", "deterministic_local"}:
        raise ValueError("evaluation_mode must be availability_check or deterministic_local.")

    normalized_bars = payload.get("normalized_bars")
    review_artifact = payload.get("review_artifact")
    symbol = _optional_text(payload.get("symbol"))
    if input_type == "normalized_bars":
        if review_artifact is not None:
            raise ValueError("review_artifact must not be provided when input_type=normalized_bars.")
        if not symbol:
            raise ValueError("symbol is required when input_type=normalized_bars.")
    else:
        if normalized_bars is not None or symbol is not None:
            raise ValueError("normalized_bars and symbol must not be provided when input_type=review_artifact.")
    return {
        "version": version,
        "input_type": input_type,
        "symbol": symbol,
        "normalized_bars": _optional_mapping(normalized_bars, name="normalized_bars_container") if False else normalized_bars,
        "review_artifact": _optional_mapping(review_artifact, name="review_artifact"),
        "evaluation_mode": evaluation_mode,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _resolve_input_bars(validated: dict[str, Any]) -> tuple[list[dict[str, float | str]], str | None]:
    if validated["input_type"] == "normalized_bars":
        try:
            return _validate_normalized_bars(validated.get("normalized_bars")), None
        except ValueError as exc:
            raise ValueError(f"normalized_bars validation failed: {exc}") from exc
    review_artifact = validated["review_artifact"]
    if review_artifact is None:
        raise ValueError("review_artifact is required when input_type=review_artifact.")
    if str(review_artifact.get("version") or "") != "result_review_gate_result_v1":
        raise ValueError("review_artifact.version must be result_review_gate_result_v1.")
    candidate_id = _required_text(review_artifact, "candidate_id")
    adapter_result = review_artifact.get("adapter_result")
    if not isinstance(adapter_result, dict):
        raise ValueError("review_artifact.adapter_result must be an object.")
    return _validate_normalized_bars(adapter_result.get("synthetic_bars")), candidate_id


def _validate_normalized_bars(value: Any) -> list[dict[str, float | str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("normalized_bars must be a non-empty list.")
    normalized: list[dict[str, float | str]] = []
    previous_timestamp: str | None = None
    for item in value:
        payload = _required_mapping(item, name="normalized_bars item")
        _reject_unknown_fields(payload, allowed={"timestamp", "open", "high", "low", "close"}, name="normalized_bars item")
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("normalized_bars timestamps must be strictly increasing.")
        open_price = _required_positive_number(payload, "open")
        high_price = _required_positive_number(payload, "high")
        low_price = _required_positive_number(payload, "low")
        close_price = _required_positive_number(payload, "close")
        if high_price < max(open_price, low_price, close_price):
            raise ValueError("normalized_bars high must be greater than or equal to open, low, and close.")
        if low_price > min(open_price, high_price, close_price):
            raise ValueError("normalized_bars low must be less than or equal to open, high, and close.")
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


def _deterministic_local_metrics(bars: list[dict[str, float | str]]) -> dict[str, Any]:
    first = bars[0]
    last = bars[-1]
    first_close = float(first["close"])
    last_close = float(last["close"])
    return {
        "bar_count": len(bars),
        "first_timestamp": str(first["timestamp"]),
        "last_timestamp": str(last["timestamp"]),
        "first_close": first_close,
        "last_close": last_close,
        "simple_return": round((last_close / first_close) - 1.0, 10),
    }


def _result(
    *,
    qlib_available: bool,
    evaluation_run: bool,
    input_hash: str,
    metrics: dict[str, Any] | None,
    failure_reason: str | None,
    final_status: str,
    input_source_type: str | None,
    source_review_candidate_id: str | None,
) -> dict[str, Any]:
    result = {
        "qlib_evaluator_version": EVALUATOR_VERSION,
        "qlib_available": qlib_available,
        "evaluation_run": evaluation_run,
        "input_hash": input_hash,
        "metrics": metrics,
        "failure_reason": failure_reason,
        "final_status": final_status,
        "input_source_type": input_source_type,
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


def _safe_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_review_candidate_id(request: Any) -> str | None:
    if not isinstance(request, dict):
        return None
    artifact = request.get("review_artifact")
    if not isinstance(artifact, dict):
        return None
    return _safe_text(artifact.get("candidate_id"))


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


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be non-empty text.")
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
