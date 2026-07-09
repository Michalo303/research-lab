from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_circuit_breaker_transition,
    build_fixed_fractional_sizing,
    build_portfolio_overlay_state,
    build_protective_exit_contract,
    build_strategy_event,
)


REQUEST_VERSION = "risk_overlay_isolated_execution_request_v1"
RESULT_VERSION = "risk_overlay_isolated_execution_result_v1"
EXECUTOR_VERSION = "risk_overlay_isolated_executor_v1"
RUNTIME_CONTRACT_VERSION = "risk_execution_contract_v1"
OUTPUT_MODE = "full_result"
_FLOAT_TOLERANCE = 1e-9


def run_isolated_risk_overlay_execution(request: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    run_configuration_sha256 = _canonical_sha256(_run_configuration_payload(validated))

    state = _initial_state(validated["initial_equity"])
    overlay_threshold_history: list[dict[str, Any]] = []
    event_log: list[dict[str, Any]] = []
    sizing_diagnostics: list[dict[str, Any]] = []
    max_drawdown = 0.0

    prices_by_timestamp = {item["timestamp"]: item for item in validated["synthetic_price_series"]}
    events_by_timestamp = {item["timestamp"]: item for item in validated["strategy_events"]}

    for price_point in validated["synthetic_price_series"]:
        timestamp = price_point["timestamp"]
        price = price_point["price"]
        _mark_to_market(state, price=price)
        max_drawdown = max(max_drawdown, state["overlay_state"]["current_drawdown_pct"])

        transition = build_circuit_breaker_transition(
            {
                "prior_state": state["overlay_state"],
                "current_equity": state["current_equity"],
                "thresholds": validated["circuit_breaker_thresholds"],
                "reentry_rule": validated["reentry_rule"],
            }
        )
        prior_multiplier = float(state["overlay_state"]["current_gross_exposure_multiplier"])
        state["overlay_state"] = transition["updated_state"]
        overlay_threshold_history.append(
            {
                "timestamp": timestamp,
                "transition_reason": transition["transition_reason"],
                "prior_gross_exposure_multiplier": prior_multiplier,
                "new_gross_exposure_multiplier": transition["new_gross_exposure_multiplier"],
                "threshold_crossed": transition["threshold_crossed"],
                "cooldown_status": transition["cooldown_status"],
                "cooldown_remaining": state["overlay_state"]["cooldown_remaining"],
                "recovery_condition_met": transition["recovery_condition_met"],
                "reentry_permitted": transition["reentry_permitted"],
                "current_drawdown_pct": state["overlay_state"]["current_drawdown_pct"],
            }
        )
        max_drawdown = max(max_drawdown, state["overlay_state"]["current_drawdown_pct"])

        if transition["new_gross_exposure_multiplier"] + _FLOAT_TOLERANCE < prior_multiplier:
            _apply_forced_derisk(
                state,
                timestamp=timestamp,
                price=price,
                new_multiplier=float(transition["new_gross_exposure_multiplier"]),
                prior_multiplier=prior_multiplier,
                fractional_units_allowed=validated["fractional_units_allowed"],
                event_log=event_log,
            )

        event = events_by_timestamp.get(timestamp)
        if event is None:
            state["last_processed_timestamp"] = timestamp
            continue

        protective_exit = validated["protective_exits_by_event_id"].get(event["event_id"])
        sizing_request = {
            "current_equity": state["current_equity"],
            "selected_risk_per_trade_pct": validated["fixed_fractional_config"]["selected_risk_per_trade_pct"],
            "price": price,
            "available_capital": state["current_equity"],
            "strategy_position_cap": validated["strategy_position_cap"],
            "portfolio_exposure_cap": validated["portfolio_exposure_cap"],
            "leverage_allowed": False,
            "fractional_units_allowed": validated["fractional_units_allowed"],
        }
        if event["event_type"] == "entry":
            _execute_entry(
                state,
                event=event,
                price=price,
                protective_exit=protective_exit,
                sizing_request=sizing_request,
                event_log=event_log,
                sizing_diagnostics=sizing_diagnostics,
            )
        elif event["event_type"] == "exit":
            _execute_exit(state, event=event, price=price, event_log=event_log)
        else:
            _execute_rebalance(
                state,
                event=event,
                price=price,
                protective_exit=protective_exit,
                sizing_request=sizing_request,
                fractional_units_allowed=validated["fractional_units_allowed"],
                event_log=event_log,
                sizing_diagnostics=sizing_diagnostics,
            )
        state["last_processed_timestamp"] = timestamp

    result = _build_result(
        validated,
        state=state,
        max_drawdown=max_drawdown,
        event_log=event_log,
        overlay_transition_log=overlay_threshold_history,
        sizing_diagnostics=sizing_diagnostics,
        input_sha256=input_sha256,
        run_configuration_sha256=run_configuration_sha256,
        prices_by_timestamp=prices_by_timestamp,
    )
    return result


def _validate_request(request: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(request, name="request")
    _reject_unknown_fields(
        request,
        allowed={
            "version",
            "runtime_contract_version",
            "symbol",
            "initial_equity",
            "synthetic_price_series",
            "strategy_events",
            "protective_exits_by_event_id",
            "fixed_fractional_config",
            "strategy_position_cap",
            "portfolio_exposure_cap",
            "circuit_breaker_thresholds",
            "reentry_rule",
            "fractional_units_allowed",
            "output_mode",
            "provenance",
        },
        name="request",
    )
    version = _required_text(request, "version")
    if version != REQUEST_VERSION:
        raise ValueError("version must be risk_overlay_isolated_execution_request_v1.")
    runtime_contract_version = _required_text(request, "runtime_contract_version")
    if runtime_contract_version != RUNTIME_CONTRACT_VERSION:
        raise ValueError("runtime_contract_version must be risk_execution_contract_v1.")
    symbol = _required_text(request, "symbol").upper()
    initial_equity = _required_positive_number(request, "initial_equity", strictly_positive=True)
    fractional_units_allowed = _required_bool(request, "fractional_units_allowed")
    output_mode = _required_text(request, "output_mode")
    if output_mode != OUTPUT_MODE:
        raise ValueError("output_mode must be full_result.")

    synthetic_price_series = _validate_price_series(request.get("synthetic_price_series"), symbol=symbol)
    strategy_events = _validate_events(request.get("strategy_events"), symbol=symbol)
    price_timestamps = [item["timestamp"] for item in synthetic_price_series]
    event_timestamps = [item["timestamp"] for item in strategy_events]
    for timestamp in event_timestamps:
        if timestamp not in price_timestamps:
            raise ValueError(f"event timestamp {timestamp} is not present in synthetic_price_series.")

    protective_exits_by_event_id = _validate_protective_exits(
        request.get("protective_exits_by_event_id"),
        strategy_events=strategy_events,
    )
    fixed_fractional_config = _validate_fixed_fractional_config(request.get("fixed_fractional_config"))
    strategy_position_cap = _required_positive_number(request, "strategy_position_cap", strictly_positive=True)
    portfolio_exposure_cap = _required_positive_number(request, "portfolio_exposure_cap", strictly_positive=True)
    circuit_breaker_thresholds = _validate_thresholds(request.get("circuit_breaker_thresholds"))
    reentry_rule = _validate_reentry_rule(request.get("reentry_rule"))
    provenance = _validate_provenance(request.get("provenance"))

    return {
        "version": version,
        "runtime_contract_version": runtime_contract_version,
        "symbol": symbol,
        "initial_equity": initial_equity,
        "synthetic_price_series": synthetic_price_series,
        "strategy_events": strategy_events,
        "protective_exits_by_event_id": protective_exits_by_event_id,
        "fixed_fractional_config": fixed_fractional_config,
        "strategy_position_cap": strategy_position_cap,
        "portfolio_exposure_cap": portfolio_exposure_cap,
        "circuit_breaker_thresholds": circuit_breaker_thresholds,
        "reentry_rule": reentry_rule,
        "fractional_units_allowed": fractional_units_allowed,
        "output_mode": output_mode,
        "provenance": provenance,
    }


def _validate_price_series(value: Any, *, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("synthetic_price_series must be a non-empty list.")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for item in value:
        _require_mapping(item, name="synthetic price point")
        _reject_unknown_fields(item, allowed={"timestamp", "symbol", "price"}, name="synthetic price point")
        timestamp = _required_text(item, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("synthetic_price_series timestamps must be strictly ordered.")
        point_symbol = _required_text(item, "symbol").upper()
        if point_symbol != symbol:
            raise ValueError("synthetic_price_series contains unknown symbol.")
        normalized.append(
            {
                "timestamp": timestamp,
                "symbol": point_symbol,
                "price": _required_positive_number(item, "price", strictly_positive=True),
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_events(value: Any, *, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("strategy_events must be a list.")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    seen_ids: set[str] = set()
    seen_timestamps: set[str] = set()
    for raw_event in value:
        event = build_strategy_event(_required_mapping(raw_event, name="strategy event"))
        if event["symbol"] != symbol:
            raise ValueError("strategy_events contains unknown symbol.")
        timestamp = event["timestamp"]
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("strategy_events timestamps must be ordered.")
        if event["event_id"] in seen_ids:
            raise ValueError("duplicate event_id is not allowed.")
        if timestamp in seen_timestamps:
            raise ValueError("at most one event per symbol and timestamp is allowed.")
        previous_timestamp = timestamp
        seen_ids.add(event["event_id"])
        seen_timestamps.add(timestamp)
        normalized.append(event)
    return normalized


def _validate_protective_exits(value: Any, *, strategy_events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _require_mapping(value, name="protective_exits_by_event_id")
    raw_mapping = dict(value)
    events_by_id = {event["event_id"]: event for event in strategy_events}
    normalized: dict[str, dict[str, Any]] = {}
    for event_id, raw_contract in raw_mapping.items():
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("protective_exits_by_event_id keys must be non-empty strings.")
        if event_id not in events_by_id:
            raise ValueError("protective_exits_by_event_id contains unknown event_id.")
        normalized[event_id] = build_protective_exit_contract(_required_mapping(raw_contract, name="protective exit contract"))
    for event in strategy_events:
        event_id = event["event_id"]
        if event["event_type"] == "entry" and event_id not in normalized:
            raise ValueError("protective exit is required for entry.")
        if event["event_type"] == "exit" and event_id in normalized:
            raise ValueError("exit events must not provide protective exits.")
    return normalized


def _validate_fixed_fractional_config(value: Any) -> dict[str, float]:
    _require_mapping(value, name="fixed_fractional_config")
    payload = dict(value)
    _reject_unknown_fields(payload, allowed={"selected_risk_per_trade_pct"}, name="fixed_fractional_config")
    return {
        "selected_risk_per_trade_pct": _required_positive_number(
            payload,
            "selected_risk_per_trade_pct",
            strictly_positive=True,
        )
    }


def _validate_thresholds(value: Any) -> list[dict[str, float]]:
    sample_state = build_portfolio_overlay_state(
        {
            "current_equity": 100.0,
            "peak_equity": 100.0,
            "current_gross_exposure_multiplier": 1.0,
            "active_circuit_breaker_stage": None,
            "cooldown_remaining": 0,
            "derisked_state": False,
            "recovery_state": "not_applicable",
            "reentry_eligible": True,
        }
    )
    result = build_circuit_breaker_transition(
        {
            "prior_state": sample_state,
            "current_equity": 100.0,
            "thresholds": value,
            "reentry_rule": {"type": "equity_recovery", "recovery_from_peak_pct": 0.0, "cooldown_days": 0},
        }
    )
    canonical_thresholds: list[dict[str, float]] = []
    for item in _required_list(value, name="circuit_breaker_thresholds"):
        payload = _required_mapping(item, name="threshold")
        _reject_unknown_fields(
            payload,
            allowed={"drawdown_pct", "gross_exposure_multiplier"},
            name="threshold",
        )
        canonical_thresholds.append(
            {
                "drawdown_pct": _required_positive_number(payload, "drawdown_pct", strictly_positive=True),
                "gross_exposure_multiplier": _required_multiplier(payload, "gross_exposure_multiplier"),
            }
        )
    return canonical_thresholds if result else []


def _validate_reentry_rule(value: Any) -> dict[str, Any]:
    sample_state = build_portfolio_overlay_state(
        {
            "current_equity": 100.0,
            "peak_equity": 100.0,
            "current_gross_exposure_multiplier": 1.0,
            "active_circuit_breaker_stage": None,
            "cooldown_remaining": 0,
            "derisked_state": False,
            "recovery_state": "not_applicable",
            "reentry_eligible": True,
        }
    )
    result = build_circuit_breaker_transition(
        {
            "prior_state": sample_state,
            "current_equity": 100.0,
            "thresholds": [{"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75}],
            "reentry_rule": value,
        }
    )
    payload = _required_mapping(value, name="reentry_rule")
    _reject_unknown_fields(
        payload,
        allowed={"type", "recovery_from_peak_pct", "cooldown_days"},
        name="reentry_rule",
    )
    canonical_rule = {
        "type": _required_text(payload, "type"),
        "recovery_from_peak_pct": _required_positive_number(
            payload,
            "recovery_from_peak_pct",
            strictly_positive=False,
        ),
        "cooldown_days": _required_non_negative_int(payload, "cooldown_days"),
    }
    return canonical_rule if result else {}


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    _require_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("provenance keys must be non-empty strings.")
        if isinstance(raw, bool):
            raise ValueError("provenance values must not be boolean.")
        if isinstance(raw, (int, float)):
            if not math.isfinite(float(raw)):
                raise ValueError("provenance values must be finite.")
            normalized[key] = float(raw) if isinstance(raw, float) else raw
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("provenance values must be non-empty strings or finite numerics.")
        normalized[key.strip()] = raw.strip()
    return normalized


def _initial_state(initial_equity: float) -> dict[str, Any]:
    overlay_state = build_portfolio_overlay_state(
        {
            "current_equity": initial_equity,
            "peak_equity": initial_equity,
            "current_gross_exposure_multiplier": 1.0,
            "active_circuit_breaker_stage": None,
            "cooldown_remaining": 0,
            "derisked_state": False,
            "recovery_state": "not_applicable",
            "reentry_eligible": True,
        }
    )
    return {
        "cash": initial_equity,
        "position_units": 0.0,
        "position_average_price": None,
        "market_value": 0.0,
        "current_equity": initial_equity,
        "peak_equity": initial_equity,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "overlay_state": overlay_state,
        "current_protective_exit": None,
        "last_processed_timestamp": None,
        "event_history": [],
        "overlay_history": [],
    }


def _mark_to_market(state: dict[str, Any], *, price: float) -> None:
    units = float(state["position_units"])
    average_price = state["position_average_price"]
    market_value = units * price
    unrealized_pnl = 0.0 if units <= 0.0 or average_price is None else units * (price - float(average_price))
    current_equity = float(state["cash"]) + market_value
    peak_equity = max(float(state["peak_equity"]), current_equity)
    overlay_state = build_portfolio_overlay_state(
        {
            "current_equity": current_equity,
            "peak_equity": peak_equity,
            "current_gross_exposure_multiplier": state["overlay_state"]["current_gross_exposure_multiplier"],
            "active_circuit_breaker_stage": state["overlay_state"]["active_circuit_breaker_stage"],
            "cooldown_remaining": state["overlay_state"]["cooldown_remaining"],
            "derisked_state": state["overlay_state"]["derisked_state"],
            "recovery_state": state["overlay_state"]["recovery_state"],
            "reentry_eligible": state["overlay_state"]["reentry_eligible"],
        }
    )
    state["market_value"] = market_value
    state["current_equity"] = current_equity
    state["peak_equity"] = peak_equity
    state["unrealized_pnl"] = unrealized_pnl
    state["overlay_state"] = overlay_state
    _assert_equity_identity(state)


def _apply_forced_derisk(
    state: dict[str, Any],
    *,
    timestamp: str,
    price: float,
    new_multiplier: float,
    prior_multiplier: float,
    fractional_units_allowed: bool,
    event_log: list[dict[str, Any]],
) -> None:
    current_units = float(state["position_units"])
    if current_units <= 0.0 or prior_multiplier <= 0.0:
        return
    target_units = current_units * new_multiplier / prior_multiplier
    target_units = _normalize_units(target_units, fractional_units_allowed=fractional_units_allowed)
    if target_units + _FLOAT_TOLERANCE >= current_units:
        return
    sold_units = current_units - target_units
    _sell_units(state, sold_units=sold_units, price=price)
    event_log.append(
        {
            "timestamp": timestamp,
            "action": "overlay_derisk",
            "side": "sell",
            "event_id": None,
            "executed_units": sold_units,
            "executed_notional": sold_units * price,
            "execution_price": price,
            "post_derisk_units": state["position_units"],
            "post_trade_units": state["position_units"],
            "current_equity": state["current_equity"],
        }
    )


def _execute_entry(
    state: dict[str, Any],
    *,
    event: dict[str, Any],
    price: float,
    protective_exit: dict[str, Any] | None,
    sizing_request: dict[str, Any],
    event_log: list[dict[str, Any]],
    sizing_diagnostics: list[dict[str, Any]],
) -> None:
    if float(state["position_units"]) > 0.0:
        raise ValueError("entry event requires a flat position.")
    if protective_exit is None:
        raise ValueError("protective exit is required for entry.")
    target_units, sizing = _build_target_units(
        state,
        price=price,
        protective_exit=protective_exit,
        sizing_request=sizing_request,
    )
    executed_units = target_units
    _buy_units(state, bought_units=executed_units, price=price)
    state["current_protective_exit"] = protective_exit
    sizing_diagnostics.append(_sizing_diagnostic(event, sizing, state))
    event_log.append(
        {
            "timestamp": event["timestamp"],
            "action": "entry",
            "side": "buy",
            "event_id": event["event_id"],
            "executed_units": executed_units,
            "executed_notional": executed_units * price,
            "execution_price": price,
            "post_trade_units": state["position_units"],
            "current_equity": state["current_equity"],
        }
    )


def _execute_exit(state: dict[str, Any], *, event: dict[str, Any], price: float, event_log: list[dict[str, Any]]) -> None:
    if float(state["position_units"]) <= 0.0:
        raise ValueError("exit event requires an open position.")
    executed_units = float(state["position_units"])
    _sell_units(state, sold_units=executed_units, price=price)
    state["current_protective_exit"] = None
    event_log.append(
        {
            "timestamp": event["timestamp"],
            "action": "exit",
            "side": "sell",
            "event_id": event["event_id"],
            "executed_units": executed_units,
            "executed_notional": executed_units * price,
            "execution_price": price,
            "post_trade_units": state["position_units"],
            "current_equity": state["current_equity"],
        }
    )


def _execute_rebalance(
    state: dict[str, Any],
    *,
    event: dict[str, Any],
    price: float,
    protective_exit: dict[str, Any] | None,
    sizing_request: dict[str, Any],
    fractional_units_allowed: bool,
    event_log: list[dict[str, Any]],
    sizing_diagnostics: list[dict[str, Any]],
) -> None:
    current_units = float(state["position_units"])
    contract_for_sizing = protective_exit or state["current_protective_exit"]
    if contract_for_sizing is None:
        raise ValueError("protective exit is required for risk-increasing rebalance.")
    target_units, sizing = _build_target_units(
        state,
        price=price,
        protective_exit=contract_for_sizing,
        sizing_request=sizing_request,
    )
    target_units = _normalize_units(target_units, fractional_units_allowed=fractional_units_allowed)
    if target_units > current_units + _FLOAT_TOLERANCE and protective_exit is None:
        raise ValueError("protective exit is required for risk-increasing rebalance.")
    if target_units > current_units + _FLOAT_TOLERANCE:
        bought_units = target_units - current_units
        _buy_units(state, bought_units=bought_units, price=price)
        state["current_protective_exit"] = protective_exit
        side = "buy"
        executed_units = bought_units
    elif target_units + _FLOAT_TOLERANCE < current_units:
        sold_units = current_units - target_units
        _sell_units(state, sold_units=sold_units, price=price)
        side = "sell"
        executed_units = sold_units
        if protective_exit is not None:
            state["current_protective_exit"] = protective_exit
    else:
        side = "hold"
        executed_units = 0.0
    sizing_diagnostics.append(_sizing_diagnostic(event, sizing, state))
    event_log.append(
        {
            "timestamp": event["timestamp"],
            "action": "rebalance",
            "side": side,
            "event_id": event["event_id"],
            "executed_units": executed_units,
            "executed_notional": executed_units * price,
            "execution_price": price,
            "post_trade_units": state["position_units"],
            "current_equity": state["current_equity"],
        }
    )


def _build_target_units(
    state: dict[str, Any],
    *,
    price: float,
    protective_exit: dict[str, Any],
    sizing_request: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    sizing = build_fixed_fractional_sizing(
        {
            **sizing_request,
            "per_unit_loss_to_protective_exit": protective_exit["per_unit_loss_to_protective_exit"],
        }
    )
    multiplier = float(state["overlay_state"]["current_gross_exposure_multiplier"])
    target_units = float(sizing["final_units"]) * multiplier
    if not sizing_request["fractional_units_allowed"]:
        target_units = math.floor(target_units)
    target_notional = target_units * price
    if target_notional > float(state["current_equity"]) + _FLOAT_TOLERANCE:
        target_units = _normalize_units(
            float(state["current_equity"]) / price,
            fractional_units_allowed=bool(sizing_request["fractional_units_allowed"]),
        )
    return target_units, sizing


def _buy_units(state: dict[str, Any], *, bought_units: float, price: float) -> None:
    current_units = float(state["position_units"])
    if bought_units <= 0.0:
        _mark_to_market(state, price=price)
        return
    current_average = state["position_average_price"]
    total_cost = bought_units * price
    state["cash"] = float(state["cash"]) - total_cost
    new_units = current_units + bought_units
    if current_average is None or current_units <= 0.0:
        new_average = price
    else:
        new_average = ((current_units * float(current_average)) + total_cost) / new_units
    state["position_units"] = new_units
    state["position_average_price"] = new_average
    _mark_to_market(state, price=price)


def _sell_units(state: dict[str, Any], *, sold_units: float, price: float) -> None:
    current_units = float(state["position_units"])
    if sold_units <= 0.0:
        _mark_to_market(state, price=price)
        return
    if sold_units > current_units + _FLOAT_TOLERANCE:
        raise ValueError("cannot sell more units than currently held.")
    average_price = float(state["position_average_price"])
    state["cash"] = float(state["cash"]) + sold_units * price
    state["realized_pnl"] = float(state["realized_pnl"]) + sold_units * (price - average_price)
    remaining_units = current_units - sold_units
    if remaining_units <= _FLOAT_TOLERANCE:
        state["position_units"] = 0.0
        state["position_average_price"] = None
        state["current_protective_exit"] = None
    else:
        state["position_units"] = remaining_units
    _mark_to_market(state, price=price)


def _build_result(
    validated: dict[str, Any],
    *,
    state: dict[str, Any],
    max_drawdown: float,
    event_log: list[dict[str, Any]],
    overlay_transition_log: list[dict[str, Any]],
    sizing_diagnostics: list[dict[str, Any]],
    input_sha256: str,
    run_configuration_sha256: str,
    prices_by_timestamp: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    final_price = validated["synthetic_price_series"][-1]["price"]
    _mark_to_market(state, price=final_price)
    metrics = _metrics(
        validated=validated,
        state=state,
        max_drawdown=max_drawdown,
        event_log=event_log,
        overlay_transition_log=overlay_transition_log,
    )
    final_state = {
        "cash": state["cash"],
        "position_units": _normalize_output_number(state["position_units"]),
        "position_average_price": state["position_average_price"],
        "market_value": state["market_value"],
        "current_equity": state["current_equity"],
        "peak_equity": state["peak_equity"],
        "realized_pnl": state["realized_pnl"],
        "unrealized_pnl": state["unrealized_pnl"],
        "overlay_state": state["overlay_state"],
        "current_protective_exit": state["current_protective_exit"],
        "last_processed_timestamp": state["last_processed_timestamp"],
    }
    provenance = dict(validated["provenance"])
    provenance.update(
        {
            "protective_exit_execution_supported": False,
            "execution_path": "synthetic_isolated_only",
            "price_series_timestamps": [item["timestamp"] for item in validated["synthetic_price_series"]],
            "event_timestamps": [item["timestamp"] for item in validated["strategy_events"]],
            "price_series_symbol": validated["symbol"],
            "pricing_mode": "explicit_synthetic_current_price_only",
            "position_closure_mode": "explicit_exit_or_overlay_derisk_only",
            "prices_by_timestamp": prices_by_timestamp,
        }
    )
    return {
        "version": RESULT_VERSION,
        "executor_version": EXECUTOR_VERSION,
        "runtime_contract_version": validated["runtime_contract_version"],
        "execution_status": "completed",
        "failure_reason": None,
        "synthetic_data_used": True,
        "execution_performed": True,
        "registry_write_performed": False,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "provider_calls_used": 0,
        "broker_actions_used": 0,
        "protective_exit_execution_supported": False,
        "run_configuration_sha256": run_configuration_sha256,
        "input_sha256": input_sha256,
        "final_state": final_state,
        "metrics": metrics,
        "event_log": event_log,
        "overlay_transition_log": overlay_transition_log,
        "sizing_diagnostics": sizing_diagnostics,
        "provenance": provenance,
    }


def _metrics(
    *,
    validated: dict[str, Any],
    state: dict[str, Any],
    max_drawdown: float,
    event_log: list[dict[str, Any]],
    overlay_transition_log: list[dict[str, Any]],
) -> dict[str, Any]:
    entry_count = sum(1 for item in event_log if item["action"] == "entry")
    exit_count = sum(1 for item in event_log if item["action"] == "exit")
    rebalance_count = sum(1 for item in event_log if item["action"] == "rebalance")
    trade_count = sum(
        1
        for item in event_log
        if item["action"] in {"entry", "exit", "rebalance", "overlay_derisk"} and item["executed_units"] > 0
    )
    activation_count = sum(1 for item in overlay_transition_log if item["transition_reason"] == "threshold_activation")
    escalation_count = sum(1 for item in overlay_transition_log if item["transition_reason"] == "threshold_escalation")
    derisking_action_count = sum(1 for item in event_log if item["action"] == "overlay_derisk")
    multipliers = [float(item["new_gross_exposure_multiplier"]) for item in overlay_transition_log]
    return {
        "initial_equity": validated["initial_equity"],
        "final_equity": state["current_equity"],
        "total_return": (state["current_equity"] / validated["initial_equity"]) - 1.0,
        "max_drawdown": max_drawdown / 100.0,
        "realized_pnl": state["realized_pnl"],
        "unrealized_pnl": state["unrealized_pnl"],
        "trade_count": trade_count,
        "entry_count": entry_count,
        "exit_count": exit_count,
        "rebalance_count": rebalance_count,
        "circuit_breaker_activation_count": activation_count,
        "escalation_count": escalation_count,
        "derisking_action_count": derisking_action_count,
        "reentry_permitted_count": sum(1 for item in overlay_transition_log if item["reentry_permitted"]),
        "minimum_gross_exposure_multiplier": min(multipliers) if multipliers else 1.0,
        "average_gross_exposure_multiplier": sum(multipliers) / len(multipliers) if multipliers else 1.0,
    }


def _sizing_diagnostic(event: dict[str, Any], sizing: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": event["timestamp"],
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "binding_cap": sizing["binding_cap"],
        "risk_budget": sizing["risk_budget"],
        "final_units_before_overlay": sizing["final_units"],
        "overlay_multiplier": state["overlay_state"]["current_gross_exposure_multiplier"],
        "post_trade_units": state["position_units"],
    }


def _run_configuration_payload(validated: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in validated.items()
        if key != "provenance"
    }


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_units(units: float, *, fractional_units_allowed: bool) -> float:
    return units if fractional_units_allowed else float(math.floor(units))


def _normalize_output_number(value: float) -> float | int:
    if math.isclose(value, round(value), rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE):
        return int(round(value))
    return value


def _assert_equity_identity(state: dict[str, Any]) -> None:
    expected_equity = float(state["cash"]) + float(state["market_value"])
    if not math.isclose(expected_equity, float(state["current_equity"]), rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError("equity must equal cash plus market_value.")


def _require_mapping(value: Any, *, name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object.")


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    _require_mapping(value, name=name)
    return dict(value)


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    for key in payload:
        if key not in allowed:
            raise ValueError(f"{name} contains unknown field: {key}")


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required.")
    return value.strip()


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean.")
    return value


def _required_positive_number(payload: dict[str, Any], field: str, *, strictly_positive: bool) -> float:
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


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _required_multiplier(payload: dict[str, Any], field: str) -> float:
    value = _required_positive_number(payload, field, strictly_positive=False)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be within [0, 1].")
    return value


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return value
