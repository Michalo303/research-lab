from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
    build_strategy_event,
)
from research_lab.execution.strategy_execution_capabilities_v1 import (
    get_strategy_execution_capability,
)


REQUEST_VERSION = "strategy_execution_capability_bridge_request_v1"
RESULT_VERSION = "strategy_execution_capability_bridge_result_v1"
BRIDGE_VERSION = "strategy_execution_capability_bridge_v1"
SUPPORTED_STRATEGY_BUILDER = "swing_trend_filtered_pullback"
SUPPORTED_SIGNAL_TYPES = {"entry", "exit", "rebalance"}
SUPPORTED_DIRECTIONS = {"long"}
SUPPORTED_PROTECTIVE_EXIT_TYPES = {"fixed_stop", "atr_stop"}
BRIDGE_STRATEGY_IDENTITY_PREFIX = "STRATEGY_EXECUTION_CAPABILITY_BRIDGE_V1::"
REASON_CODES = {
    "entry": "synthetic_strategy_bridge_entry",
    "exit": "synthetic_strategy_bridge_exit",
    "rebalance": "synthetic_strategy_bridge_rebalance",
}


def build_strategy_execution_bridge_request(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    payload: dict[str, object] = {
        "version": RESULT_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "strategy_builder": validated["strategy_builder"],
        "strategy_runtime_supported": False,
        "synthetic_data_used": True,
        "real_data_used": False,
        "strategy_events": validated["strategy_events"],
        "protective_exits_by_event_id": validated["protective_exits_by_event_id"],
        "capability_summary": validated["capability_summary"],
        "safe_flags": {
            "provider_calls_used": 0,
            "broker_actions_used": 0,
            "registry_write_performed": False,
            "deployment_gate_run": False,
            "hermes_write_performed": False,
            "backtest_run_performed": False,
        },
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    payload["output_payload_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "strategy_builder", "symbol", "synthetic_bars", "strategy_signal_plan", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")

    strategy_builder = _required_text(payload, "strategy_builder")
    if strategy_builder != SUPPORTED_STRATEGY_BUILDER:
        raise ValueError(f"unsupported strategy_builder: {strategy_builder}")

    capability = get_strategy_execution_capability(strategy_builder)
    symbol = _required_text(payload, "symbol").upper()
    if not symbol.startswith("SYNTH"):
        raise ValueError("symbol must start with SYNTH.")
    synthetic_bars = _validate_synthetic_bars(payload.get("synthetic_bars"))
    prices_by_timestamp = {item["timestamp"]: item["close"] for item in synthetic_bars}
    strategy_events, protective_exits_by_event_id = _validate_signal_plan(
        payload.get("strategy_signal_plan"),
        symbol=symbol,
        strategy_builder=strategy_builder,
        prices_by_timestamp=prices_by_timestamp,
    )
    provenance = _validate_provenance(payload.get("provenance"))
    capability_summary = {
        "builder": strategy_builder,
        "bridge_version": BRIDGE_VERSION,
        "builder_exists": True,
        "synthetic_signal_plan_supported": True,
        "production_runtime_supported": False,
        "protective_exit_required": True,
        "supported_protective_exit_types": ["fixed_stop", "atr_stop"],
        "supported_for_risk_overlay_execution": capability["supported_for_risk_overlay_execution"],
        "unsupported_reason": capability["unsupported_reason"],
    }
    return {
        "version": version,
        "strategy_builder": strategy_builder,
        "symbol": symbol,
        "synthetic_bars": synthetic_bars,
        "strategy_events": strategy_events,
        "protective_exits_by_event_id": protective_exits_by_event_id,
        "capability_summary": capability_summary,
        "provenance": provenance,
    }


def _validate_synthetic_bars(value: Any) -> list[dict[str, float | str]]:
    bars = _required_list(value, name="synthetic_bars")
    normalized: list[dict[str, float | str]] = []
    previous_timestamp: str | None = None
    for item in bars:
        payload = _required_mapping(item, name="synthetic bar")
        _reject_unknown_fields(payload, allowed={"timestamp", "open", "high", "low", "close"}, name="synthetic bar")
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("synthetic_bars timestamps must be strictly increasing.")
        open_price = _required_finite_number(payload, "open", strictly_positive=True)
        high_price = _required_finite_number(payload, "high", strictly_positive=True)
        low_price = _required_finite_number(payload, "low", strictly_positive=True)
        close_price = _required_finite_number(payload, "close", strictly_positive=True)
        if high_price < max(open_price, close_price, low_price):
            raise ValueError("high must be greater than or equal to open, close, and low.")
        if low_price > min(open_price, close_price, high_price):
            raise ValueError("low must be less than or equal to open, close, and high.")
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


def _validate_signal_plan(
    value: Any,
    *,
    symbol: str,
    strategy_builder: str,
    prices_by_timestamp: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    signal_plan = _required_list(value, name="strategy_signal_plan")
    strategy_events: list[dict[str, Any]] = []
    protective_exits_by_event_id: dict[str, dict[str, Any]] = {}
    seen_signal_ids: set[str] = set()
    seen_timestamps: set[str] = set()
    previous_timestamp: str | None = None
    strategy_identity = f"{BRIDGE_STRATEGY_IDENTITY_PREFIX}{strategy_builder}"
    for item in signal_plan:
        payload = _required_mapping(item, name="signal")
        _reject_unknown_fields(
            payload,
            allowed={"timestamp", "signal_id", "signal_type", "direction", "protective_exit"},
            name="signal",
        )
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("strategy_signal_plan timestamps must be ordered.")
        if timestamp in seen_timestamps:
            raise ValueError("at most one signal may exist at the same timestamp.")
        if timestamp not in prices_by_timestamp:
            raise ValueError(f"signal timestamp {timestamp} is not present in synthetic_bars.")
        signal_id = _required_text(payload, "signal_id")
        if signal_id in seen_signal_ids:
            raise ValueError("duplicate signal_id is not allowed.")
        signal_type = _required_text(payload, "signal_type")
        if signal_type not in SUPPORTED_SIGNAL_TYPES:
            raise ValueError(f"unsupported signal_type: {signal_type}")
        direction = _required_text(payload, "direction")
        if direction not in SUPPORTED_DIRECTIONS:
            raise ValueError("direction must be long.")
        protective_exit = _validate_protective_exit(
            payload.get("protective_exit"),
            signal_type=signal_type,
            event_price=prices_by_timestamp[timestamp],
            signal_id=signal_id,
        )
        strategy_events.append(
            build_strategy_event(
                {
                    "timestamp": timestamp,
                    "event_type": signal_type,
                    "symbol": symbol,
                    "target_direction": "flat" if signal_type == "exit" else "long",
                    "strategy_identity": strategy_identity,
                    "event_id": signal_id,
                    "reason_code": REASON_CODES[signal_type],
                }
            )
        )
        if protective_exit is not None:
            protective_exits_by_event_id[signal_id] = protective_exit
        seen_signal_ids.add(signal_id)
        seen_timestamps.add(timestamp)
        previous_timestamp = timestamp
    return strategy_events, protective_exits_by_event_id


def _validate_protective_exit(
    value: Any,
    *,
    signal_type: str,
    event_price: float,
    signal_id: str,
) -> dict[str, Any] | None:
    if value is None:
        if signal_type in {"entry", "rebalance"}:
            raise ValueError("protective_exit is required for entry and rebalance signals.")
        return None
    if signal_type == "exit":
        raise ValueError("exit signals must not provide protective_exit.")
    payload = _required_mapping(value, name="protective_exit")
    exit_type = _required_text(payload, "type")
    if exit_type not in SUPPORTED_PROTECTIVE_EXIT_TYPES:
        raise ValueError(f"unsupported protective_exit.type: {exit_type}")
    if exit_type == "fixed_stop":
        _reject_unknown_fields(payload, allowed={"type", "stop_price"}, name="protective_exit")
        stop_price = _required_finite_number(payload, "stop_price", strictly_positive=True)
        per_unit_loss = event_price - stop_price
        if stop_price >= event_price:
            raise ValueError("protective_exit.stop_price must be below event price for long.")
        protective_exit_type = "price_stop"
    else:
        _reject_unknown_fields(payload, allowed={"type", "atr", "atr_multiple"}, name="protective_exit")
        atr = _required_finite_number(payload, "atr", strictly_positive=True)
        atr_multiple = _required_finite_number(payload, "atr_multiple", strictly_positive=True)
        stop_price = event_price - (atr * atr_multiple)
        per_unit_loss = atr * atr_multiple
        if stop_price >= event_price:
            raise ValueError("protective_exit.stop_price must be below event price for long.")
        protective_exit_type = "atr_stop"
    return build_protective_exit_contract(
        {
            "entry_price": event_price,
            "protective_exit_price": stop_price,
            "per_unit_loss_to_protective_exit": per_unit_loss,
            "protective_exit_type": protective_exit_type,
            "strategy_provenance": signal_id,
        }
    )


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    provenance = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw_value in provenance.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("provenance keys must be non-empty strings.")
        normalized[key.strip()] = _json_scalar(raw_value, name=f"provenance.{key}")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    for key in payload:
        if key not in allowed:
            raise ValueError(f"{name} contains unknown field: {key}")


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required.")
    return value.strip()


def _required_finite_number(payload: dict[str, Any], field: str, *, strictly_positive: bool) -> float:
    value = payload.get(field)
    if isinstance(value, bool):
        raise ValueError(f"{field} must not be boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    if strictly_positive and number <= 0.0:
        raise ValueError(f"{field} must be positive.")
    if not strictly_positive and number < 0.0:
        raise ValueError(f"{field} must be non-negative.")
    return number


def _json_scalar(value: Any, *, name: str) -> str | int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be boolean.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, finite number, or null.")
    return value.strip()
