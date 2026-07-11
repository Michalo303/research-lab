from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "result_review_gate_request_v1"
RESULT_VERSION = "result_review_gate_result_v1"
GATE_VERSION = "result_review_gate_v1"
FINAL_STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
FINAL_STATUS_FAILED_VALIDATION = "FAILED_VALIDATION"
SOURCE_TYPE = "isolated_execution_review"


def build_result_review_gate(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    validation_errors = _validation_errors(validated)
    isolated_execution_result = validated["isolated_execution_result"]
    risk_metrics, drawdown, trade_count, exposure_summary = _optional_metrics(isolated_execution_result)
    candidate_sha256 = _candidate_sha256(validated)
    failed_validation = bool(validation_errors)
    result = {
        "version": RESULT_VERSION,
        "gate_version": GATE_VERSION,
        "candidate_id": f"RESULT_REVIEW_GATE_V1::{candidate_sha256}",
        "candidate_sha256": candidate_sha256,
        "symbol": _symbol(validated),
        "source_type": SOURCE_TYPE,
        "source_provenance": validated["provenance"],
        "adapter_safety_flags": dict(validated["adapter_result"].get("safe_flags") or {}),
        "adapter_result": validated["adapter_result"],
        "strategy_contract_result": validated["strategy_contract_result"],
        "bridge_result": validated["bridge_result"],
        "isolated_execution_result": isolated_execution_result,
        "risk_metrics": risk_metrics,
        "risk_metrics_available": risk_metrics is not None,
        "drawdown": drawdown,
        "drawdown_available": drawdown is not None,
        "trade_count": trade_count,
        "trade_count_available": trade_count is not None,
        "exposure_summary": exposure_summary,
        "exposure_summary_available": exposure_summary is not None,
        "pass_reason": None if failed_validation else "validated_isolated_path_requires_human_review",
        "failure_reason": "; ".join(validation_errors) if failed_validation else None,
        "final_review_status": FINAL_STATUS_FAILED_VALIDATION if failed_validation else FINAL_STATUS_REVIEW_REQUIRED,
        "promotion_performed": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "provider_calls_used": 0,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "input_sha256": input_sha256,
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "adapter_result", "strategy_contract_result", "bridge_result", "isolated_execution_result", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return {
        "version": version,
        "adapter_result": _required_mapping(payload.get("adapter_result"), name="adapter_result"),
        "strategy_contract_result": _required_mapping(payload.get("strategy_contract_result"), name="strategy_contract_result"),
        "bridge_result": _required_mapping(payload.get("bridge_result"), name="bridge_result"),
        "isolated_execution_result": _optional_mapping(payload.get("isolated_execution_result"), name="isolated_execution_result"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validation_errors(validated: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    adapter = validated["adapter_result"]
    strategy_contract = validated["strategy_contract_result"]
    bridge = validated["bridge_result"]
    isolated_execution_result = validated["isolated_execution_result"]

    if str(adapter.get("version") or "") != "isolated_real_data_adapter_contract_result_v1":
        errors.append("adapter_result.version must be isolated_real_data_adapter_contract_result_v1")
    if str(strategy_contract.get("version") or "") != "swing_trend_filtered_pullback_strategy_contract_result_v1":
        errors.append("strategy_contract_result.version must be swing_trend_filtered_pullback_strategy_contract_result_v1")
    if str(bridge.get("version") or "") != "strategy_execution_capability_bridge_result_v1":
        errors.append("bridge_result.version must be strategy_execution_capability_bridge_result_v1")

    adapter_symbol = str(adapter.get("symbol") or "")
    strategy_symbol = str(strategy_contract.get("symbol") or "")
    bridge_symbol = _bridge_symbol(bridge)
    if not adapter_symbol:
        errors.append("adapter_result.symbol must be present")
    if not strategy_symbol:
        errors.append("strategy_contract_result.symbol must be present")
    if not bridge_symbol:
        errors.append("bridge_result.symbol must be present")
    if adapter_symbol and strategy_symbol and adapter_symbol != strategy_symbol:
        errors.append("adapter_result.symbol must match strategy_contract_result.symbol")
    if adapter_symbol and bridge_symbol and adapter_symbol != bridge_symbol:
        errors.append("adapter_result.symbol must match bridge_result.symbol")

    if adapter.get("production_runtime_supported") is not False:
        errors.append("adapter_result.production_runtime_supported must be false")
    if strategy_contract.get("production_runtime_supported") is not False:
        errors.append("strategy_contract_result.production_runtime_supported must be false")
    capability_summary = bridge.get("capability_summary")
    if not isinstance(capability_summary, dict) or capability_summary.get("production_runtime_supported") is not False:
        errors.append("bridge_result.capability_summary.production_runtime_supported must be false")

    if isolated_execution_result is not None:
        if str(isolated_execution_result.get("version") or "") != "risk_overlay_isolated_execution_result_v1":
            errors.append("isolated_execution_result.version must be risk_overlay_isolated_execution_result_v1")
        if str(isolated_execution_result.get("execution_status") or "") != "completed":
            errors.append("isolated_execution_result.execution_status must be completed")
        if isolated_execution_result.get("failure_reason") is not None:
            errors.append("isolated_execution_result.failure_reason must be null")
    return errors


def _symbol(validated: dict[str, Any]) -> str:
    for candidate in (
        str(validated["adapter_result"].get("symbol") or ""),
        str(validated["strategy_contract_result"].get("symbol") or ""),
        _bridge_symbol(validated["bridge_result"]),
    ):
        if candidate:
            return candidate
    return ""


def _bridge_symbol(bridge_result: dict[str, Any]) -> str:
    events = bridge_result.get("strategy_events")
    if not isinstance(events, list) or not events:
        return ""
    first = events[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("symbol") or "")


def _optional_metrics(isolated_execution_result: dict[str, Any] | None) -> tuple[dict[str, float] | None, float | None, int | None, dict[str, float | int | None] | None]:
    if isolated_execution_result is None:
        return None, None, None, None
    metrics = isolated_execution_result.get("metrics")
    final_state = isolated_execution_result.get("final_state")
    if not isinstance(metrics, dict) or not isinstance(final_state, dict):
        return None, None, None, None
    risk_metrics: dict[str, float] | None = None
    if all(key in metrics for key in ("initial_equity", "final_equity", "total_return")):
        risk_metrics = {
            "initial_equity": float(metrics["initial_equity"]),
            "final_equity": float(metrics["final_equity"]),
            "total_return": float(metrics["total_return"]),
        }
    drawdown = float(metrics["max_drawdown"]) if "max_drawdown" in metrics and _is_number(metrics["max_drawdown"]) else None
    trade_count = int(metrics["trade_count"]) if "trade_count" in metrics and _is_number(metrics["trade_count"]) else None
    overlay_state = final_state.get("overlay_state")
    exposure_summary: dict[str, float | int | None] | None = None
    if isinstance(overlay_state, dict) and all(key in final_state for key in ("current_equity", "position_units")):
        gross_exposure = overlay_state.get("current_gross_exposure_multiplier")
        exposure_summary = {
            "current_equity": float(final_state["current_equity"]),
            "position_units": int(final_state["position_units"]),
            "current_gross_exposure_multiplier": float(gross_exposure) if _is_number(gross_exposure) else None,
        }
    return risk_metrics, drawdown, trade_count, exposure_summary


def _candidate_sha256(validated: dict[str, Any]) -> str:
    payload = {
        "adapter_output_payload_sha256": validated["adapter_result"].get("output_payload_sha256"),
        "strategy_output_payload_sha256": validated["strategy_contract_result"].get("output_payload_sha256"),
        "bridge_output_payload_sha256": validated["bridge_result"].get("output_payload_sha256"),
        "isolated_execution_input_sha256": (validated["isolated_execution_result"] or {}).get("input_sha256"),
    }
    return _canonical_sha256(payload)


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


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
