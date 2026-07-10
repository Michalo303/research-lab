from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.risk_overlay_isolated_executor_v1 import (
    run_isolated_risk_overlay_execution,
)
from research_lab.execution.strategy_execution_capability_bridge_v1 import (
    build_strategy_execution_bridge_request,
)


REQUEST_VERSION = "strategy_execution_bridge_synthetic_executor_request_v1"
RESULT_VERSION = "strategy_execution_bridge_synthetic_executor_result_v1"
INTEGRATION_VERSION = "strategy_execution_bridge_synthetic_executor_v1"
EXECUTOR_REQUEST_VERSION = "risk_overlay_isolated_execution_request_v1"


def run_strategy_execution_bridge_synthetic_executor(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    integration_input_sha256 = _canonical_sha256(validated)
    bridge_result = build_strategy_execution_bridge_request(validated["bridge_request"])
    executor_request = _build_executor_request(
        bridge_request=validated["bridge_request"],
        bridge_result=bridge_result,
        executor_config=validated["executor_config"],
        provenance=validated["provenance"],
    )
    executor_request_sha256 = _canonical_sha256(executor_request)
    executor_result = run_isolated_risk_overlay_execution(executor_request)
    return {
        "version": RESULT_VERSION,
        "integration_version": INTEGRATION_VERSION,
        "execution_status": "completed",
        "failure_reason": None,
        "synthetic_data_used": True,
        "real_data_used": False,
        "registry_write_performed": False,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "provider_calls_used": 0,
        "broker_actions_used": 0,
        "hermes_write_performed": False,
        "backtest_run_performed": False,
        "integration_input_sha256": integration_input_sha256,
        "executor_request_sha256": executor_request_sha256,
        "bridge_result": bridge_result,
        "executor_request": executor_request,
        "executor_result": executor_result,
        "provenance": {
            **validated["provenance"],
            "integration_version": INTEGRATION_VERSION,
            "bridge_version": bridge_result["bridge_version"],
            "executor_version": executor_result["executor_version"],
        },
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "bridge_request", "executor_config", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    bridge_request = _required_mapping(payload.get("bridge_request"), name="bridge_request")
    executor_config = _validate_executor_config(payload.get("executor_config"))
    provenance = _validate_provenance(payload.get("provenance"))
    return {
        "version": version,
        "bridge_request": bridge_request,
        "executor_config": executor_config,
        "provenance": provenance,
    }


def _validate_executor_config(value: Any) -> dict[str, Any]:
    config = _required_mapping(value, name="executor_config")
    _reject_unknown_fields(
        config,
        allowed={
            "runtime_contract_version",
            "initial_equity",
            "fixed_fractional_config",
            "strategy_position_cap",
            "portfolio_exposure_cap",
            "circuit_breaker_thresholds",
            "reentry_rule",
            "fractional_units_allowed",
            "output_mode",
        },
        name="executor_config",
    )
    runtime_contract_version = _required_text(config, "runtime_contract_version")
    if runtime_contract_version != "risk_execution_contract_v1":
        raise ValueError("executor_config.runtime_contract_version must be risk_execution_contract_v1.")
    fixed_fractional_config = _required_mapping(config.get("fixed_fractional_config"), name="fixed_fractional_config")
    _reject_unknown_fields(
        fixed_fractional_config,
        allowed={"selected_risk_per_trade_pct"},
        name="fixed_fractional_config",
    )
    output_mode = _required_text(config, "output_mode")
    if output_mode != "full_result":
        raise ValueError("executor_config.output_mode must be full_result.")
    return {
        "runtime_contract_version": runtime_contract_version,
        "initial_equity": _required_positive_number(config, "initial_equity"),
        "fixed_fractional_config": {
            "selected_risk_per_trade_pct": _required_positive_number(
                fixed_fractional_config,
                "selected_risk_per_trade_pct",
            )
        },
        "strategy_position_cap": _required_positive_number(config, "strategy_position_cap"),
        "portfolio_exposure_cap": _required_positive_number(config, "portfolio_exposure_cap"),
        "circuit_breaker_thresholds": _required_list(config.get("circuit_breaker_thresholds"), name="circuit_breaker_thresholds"),
        "reentry_rule": _required_mapping(config.get("reentry_rule"), name="reentry_rule"),
        "fractional_units_allowed": _required_bool(config, "fractional_units_allowed"),
        "output_mode": output_mode,
    }


def _build_executor_request(
    *,
    bridge_request: dict[str, Any],
    bridge_result: dict[str, Any],
    executor_config: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    symbol = _required_text(bridge_request, "symbol").upper()
    synthetic_bars = _required_list(bridge_request.get("synthetic_bars"), name="synthetic_bars")
    return {
        "version": EXECUTOR_REQUEST_VERSION,
        "runtime_contract_version": executor_config["runtime_contract_version"],
        "symbol": symbol,
        "initial_equity": executor_config["initial_equity"],
        "synthetic_price_series": [
            {
                "timestamp": _required_text(_required_mapping(item, name="synthetic bar"), "timestamp"),
                "symbol": symbol,
                "price": _required_positive_number(_required_mapping(item, name="synthetic bar"), "close"),
            }
            for item in synthetic_bars
        ],
        "strategy_events": bridge_result["strategy_events"],
        "protective_exits_by_event_id": bridge_result["protective_exits_by_event_id"],
        "fixed_fractional_config": executor_config["fixed_fractional_config"],
        "strategy_position_cap": executor_config["strategy_position_cap"],
        "portfolio_exposure_cap": executor_config["portfolio_exposure_cap"],
        "circuit_breaker_thresholds": executor_config["circuit_breaker_thresholds"],
        "reentry_rule": executor_config["reentry_rule"],
        "fractional_units_allowed": executor_config["fractional_units_allowed"],
        "output_mode": executor_config["output_mode"],
        "provenance": {
            **provenance,
            "bridge_input_sha256": bridge_result["input_sha256"],
            "bridge_output_payload_sha256": bridge_result["output_payload_sha256"],
            "integration_version": INTEGRATION_VERSION,
        },
    }


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("provenance keys must be non-empty strings.")
        if isinstance(raw, (str, int, float, bool)) or raw is None:
            normalized[key] = raw
            continue
        raise ValueError("provenance values must be scalar JSON values.")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping.")
    return value


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return value


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required.")
    return value.strip()


def _required_positive_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a positive finite number.")
    return float(value)


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    for key in payload:
        if key not in allowed:
            raise ValueError(f"{name} contains unknown field: {key}")
