from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
)
from research_lab.strategies.baselines import _rsi


REQUEST_VERSION = "swing_trend_filtered_pullback_strategy_contract_request_v1"
RESULT_VERSION = "swing_trend_filtered_pullback_strategy_contract_result_v1"
CONTRACT_VERSION = "swing_trend_filtered_pullback_strategy_contract_v1"
STRATEGY_BUILDER = "swing_trend_filtered_pullback"
_FLOAT_TOLERANCE = 1e-9


def build_swing_trend_filtered_pullback_strategy_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    bars = validated["synthetic_bars"]
    plan, contracts, refreshes = _build_signal_plan(
        symbol=validated["symbol"],
        bars=bars,
        parameters=validated["strategy_parameters"],
    )
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "strategy_builder": STRATEGY_BUILDER,
        "symbol": validated["symbol"],
        "synthetic_bars": bars,
        "strategy_signal_plan": plan,
        "signal_contracts": contracts,
        "active_contract_refreshes": refreshes,
        "synthetic_data_used": True,
        "real_data_used": False,
        "production_runtime_supported": False,
        "supported_for_risk_overlay_execution": False,
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
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _build_signal_plan(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    frame = pd.DataFrame(bars).set_index("timestamp")
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)

    if len(frame.index) >= 14:
        lookback = 14
    else:
        lookback = max(2, min(int(parameters["fast_sma"]), len(frame.index) - 1))
    rsi = _rsi(close, window=lookback)
    fast = close.rolling(int(parameters["fast_sma"])).mean()
    slow = close.rolling(int(parameters["slow_sma"])).mean()
    atr = (high - low).rolling(lookback).mean()

    active = False
    entry_price = 0.0
    last_stop_price: float | None = None
    signal_sequence = 0
    strategy_signal_plan: list[dict[str, Any]] = []
    signal_contracts: list[dict[str, Any]] = []
    active_contract_refreshes: list[dict[str, Any]] = []

    for timestamp in close.index:
        close_price = float(close.loc[timestamp])
        if any(math.isnan(value) for value in (rsi.loc[timestamp], fast.loc[timestamp], slow.loc[timestamp], atr.loc[timestamp])):
            continue
        trend_ok = float(fast.loc[timestamp]) > float(slow.loc[timestamp])
        pullback = float(rsi.loc[timestamp]) < float(parameters["rsi_entry"])

        if not active and trend_ok and pullback:
            active = True
            entry_price = close_price
            stop_price = _protective_exit_stop(entry_price=entry_price, atr_value=float(atr.loc[timestamp]), atr_multiple=float(parameters["atr_stop"]))
            signal_sequence += 1
            signal_id = f"stfp-signal-{signal_sequence}"
            strategy_signal_plan.append(
                {
                    "timestamp": timestamp,
                    "signal_id": signal_id,
                    "signal_type": "entry",
                    "direction": "long",
                    "target_direction": "long",
                    "protective_exit": {
                        "type": "fixed_stop",
                        "stop_price": stop_price,
                    },
                }
            )
            contract = _signal_contract(
                timestamp=timestamp,
                signal_id=signal_id,
                signal_type="entry",
                target_exposure=float(parameters["max_exposure"]),
                entry_price=entry_price,
                stop_price=stop_price,
            )
            signal_contracts.append(contract)
            last_stop_price = stop_price
            continue

        if not active:
            continue

        current_stop_price = _protective_exit_stop(
            entry_price=entry_price,
            atr_value=float(atr.loc[timestamp]),
            atr_multiple=float(parameters["atr_stop"]),
        )
        stop_hit = close_price < current_stop_price
        exit_signal = float(rsi.loc[timestamp]) > float(parameters["rsi_exit"]) or close_price < float(slow.loc[timestamp]) or stop_hit
        if exit_signal:
            active = False
            signal_sequence += 1
            signal_id = f"stfp-signal-{signal_sequence}"
            strategy_signal_plan.append(
                {
                    "timestamp": timestamp,
                    "signal_id": signal_id,
                    "signal_type": "exit",
                    "direction": "long",
                    "target_direction": "flat",
                }
            )
            signal_contracts.append(
                {
                    "timestamp": timestamp,
                    "signal_id": signal_id,
                    "signal_type": "exit",
                    "direction": "long",
                    "target_exposure": 0.0,
                    "protective_exit": None,
                }
            )
            last_stop_price = None
            continue

        if last_stop_price is None or not math.isclose(current_stop_price, last_stop_price, rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE):
            active_contract_refreshes.append(
                {
                    "timestamp": timestamp,
                    "refresh_type": "protective_exit_update",
                    "target_exposure": float(parameters["max_exposure"]),
                    "protective_exit": build_protective_exit_contract(
                        {
                            "entry_price": close_price,
                            "protective_exit_price": current_stop_price,
                            "per_unit_loss_to_protective_exit": close_price - current_stop_price,
                            "protective_exit_type": "price_stop",
                            "strategy_provenance": f"refresh:{timestamp}",
                        }
                    ),
                }
            )
            last_stop_price = current_stop_price

    return strategy_signal_plan, signal_contracts, active_contract_refreshes


def _signal_contract(
    *,
    timestamp: str,
    signal_id: str,
    signal_type: str,
    target_exposure: float,
    entry_price: float,
    stop_price: float,
) -> dict[str, Any]:
    protective_exit = build_protective_exit_contract(
        {
            "entry_price": entry_price,
            "protective_exit_price": stop_price,
            "per_unit_loss_to_protective_exit": entry_price - stop_price,
            "protective_exit_type": "price_stop",
            "strategy_provenance": signal_id,
        }
    )
    return {
        "timestamp": timestamp,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "direction": "long",
        "target_exposure": target_exposure,
        "protective_exit": protective_exit,
    }


def _protective_exit_stop(*, entry_price: float, atr_value: float, atr_multiple: float) -> float:
    stop_price = entry_price - (atr_value * atr_multiple)
    if stop_price <= 0:
        raise ValueError("computed protective exit stop must remain positive.")
    return stop_price


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "symbol", "synthetic_bars", "strategy_parameters", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    symbol = _required_text(payload, "symbol").upper()
    if not symbol.startswith("SYNTH"):
        raise ValueError("symbol must start with SYNTH.")
    return {
        "version": version,
        "symbol": symbol,
        "synthetic_bars": _validate_synthetic_bars(payload.get("synthetic_bars")),
        "strategy_parameters": _validate_strategy_parameters(payload.get("strategy_parameters")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_synthetic_bars(value: Any) -> list[dict[str, Any]]:
    bars = _required_list(value, name="synthetic_bars")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for item in bars:
        payload = _required_mapping(item, name="synthetic bar")
        _reject_unknown_fields(payload, allowed={"timestamp", "open", "high", "low", "close"}, name="synthetic bar")
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("synthetic_bars timestamps must be strictly increasing.")
        open_price = _required_positive_number(payload, "open")
        high_price = _required_positive_number(payload, "high")
        low_price = _required_positive_number(payload, "low")
        close_price = _required_positive_number(payload, "close")
        if high_price < max(open_price, low_price, close_price):
            raise ValueError("high must be greater than or equal to open, low, and close.")
        if low_price > min(open_price, high_price, close_price):
            raise ValueError("low must be less than or equal to open, high, and close.")
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


def _validate_strategy_parameters(value: Any) -> dict[str, Any]:
    params = _required_mapping(value, name="strategy_parameters")
    _reject_unknown_fields(
        params,
        allowed={"fast_sma", "slow_sma", "rsi_entry", "rsi_exit", "atr_stop", "max_exposure"},
        name="strategy_parameters",
    )
    fast_sma = _required_positive_int(params, "fast_sma")
    slow_sma = _required_positive_int(params, "slow_sma")
    if fast_sma >= slow_sma:
        raise ValueError("fast_sma must be less than slow_sma.")
    rsi_entry = _required_positive_number(params, "rsi_entry")
    rsi_exit = _required_positive_number(params, "rsi_exit")
    if rsi_entry >= rsi_exit:
        raise ValueError("rsi_entry must be less than rsi_exit.")
    max_exposure = _required_positive_number(params, "max_exposure")
    if max_exposure > 1.0:
        raise ValueError("max_exposure must be less than or equal to 1.0.")
    return {
        "fast_sma": fast_sma,
        "slow_sma": slow_sma,
        "rsi_entry": rsi_entry,
        "rsi_exit": rsi_exit,
        "atr_stop": _required_positive_number(params, "atr_stop"),
        "max_exposure": max_exposure,
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
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


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


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    for key in payload:
        if key not in allowed:
            raise ValueError(f"{name} contains unknown field: {key}")
