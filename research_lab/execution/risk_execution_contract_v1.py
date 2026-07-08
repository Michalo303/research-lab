from __future__ import annotations

import math
from typing import Any


ALLOWED_EVENT_TYPES = {"entry", "exit", "rebalance"}
ALLOWED_DIRECTIONS = {"long", "flat"}
ALLOWED_PROTECTIVE_EXIT_TYPES = {"atr_stop", "price_stop", "indicator_stop", "manual_stop"}
ALLOWED_RECOVERY_TYPES = {"equity_recovery"}
_FLOAT_TOLERANCE = 1e-9


def build_strategy_event(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(payload, name="strategy event")
    _reject_unknown_fields(
        payload,
        allowed={
            "timestamp",
            "event_type",
            "symbol",
            "target_direction",
            "strategy_identity",
            "event_id",
            "reason_code",
        },
        name="strategy event",
    )
    timestamp = _required_text(payload, "timestamp")
    event_type = _required_enum(payload, "event_type", ALLOWED_EVENT_TYPES)
    symbol = _required_text(payload, "symbol").upper()
    target_direction = _required_enum(payload, "target_direction", ALLOWED_DIRECTIONS)
    strategy_identity = _required_text(payload, "strategy_identity")
    event_id = _required_text(payload, "event_id")
    reason_code = _optional_text(payload.get("reason_code"))
    result = {
        "timestamp": timestamp,
        "event_type": event_type,
        "symbol": symbol,
        "target_direction": target_direction,
        "strategy_identity": strategy_identity,
        "event_id": event_id,
        "reason_code": reason_code,
    }
    return _drop_none(result)


def build_protective_exit_contract(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(payload, name="protective exit contract")
    _reject_unknown_fields(
        payload,
        allowed={
            "entry_price",
            "protective_exit_price",
            "per_unit_loss_to_protective_exit",
            "protective_exit_type",
            "strategy_provenance",
        },
        name="protective exit contract",
    )
    entry_price = _required_positive_number(payload, "entry_price", strictly_positive=True)
    protective_exit_price = _required_positive_number(payload, "protective_exit_price", strictly_positive=True)
    per_unit_loss = _required_positive_number(
        payload,
        "per_unit_loss_to_protective_exit",
        strictly_positive=True,
    )
    protective_exit_type = _required_enum(payload, "protective_exit_type", ALLOWED_PROTECTIVE_EXIT_TYPES)
    strategy_provenance = _required_text(payload, "strategy_provenance")
    computed = abs(entry_price - protective_exit_price)
    if computed <= 0.0:
        raise ValueError("per_unit_loss_to_protective_exit must be positive.")
    if not math.isclose(per_unit_loss, computed, rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError("per_unit_loss_to_protective_exit must exactly match abs(entry_price - protective_exit_price).")
    return {
        "entry_price": entry_price,
        "protective_exit_price": protective_exit_price,
        "per_unit_loss_to_protective_exit": computed,
        "protective_exit_type": protective_exit_type,
        "strategy_provenance": strategy_provenance,
    }


def build_fixed_fractional_sizing(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(payload, name="fixed fractional sizing request")
    _reject_unknown_fields(
        payload,
        allowed={
            "current_equity",
            "selected_risk_per_trade_pct",
            "per_unit_loss_to_protective_exit",
            "price",
            "available_capital",
            "strategy_position_cap",
            "portfolio_exposure_cap",
            "leverage_allowed",
            "fractional_units_allowed",
        },
        name="fixed fractional sizing request",
    )
    current_equity = _required_positive_number(payload, "current_equity", strictly_positive=True)
    selected_risk_per_trade_pct = _required_positive_number(
        payload,
        "selected_risk_per_trade_pct",
        strictly_positive=True,
    )
    per_unit_loss = _required_positive_number(
        payload,
        "per_unit_loss_to_protective_exit",
        strictly_positive=True,
    )
    price = _required_positive_number(payload, "price", strictly_positive=True)
    available_capital = _required_positive_number(payload, "available_capital", strictly_positive=True)
    strategy_position_cap = _required_positive_number(payload, "strategy_position_cap", strictly_positive=True)
    portfolio_exposure_cap = _required_positive_number(payload, "portfolio_exposure_cap", strictly_positive=True)
    leverage_allowed = _required_bool(payload, "leverage_allowed")
    fractional_units_allowed = _required_bool(payload, "fractional_units_allowed")

    risk_budget = current_equity * selected_risk_per_trade_pct / 100.0
    raw_units_float = risk_budget / per_unit_loss
    raw_units = raw_units_float if fractional_units_allowed else math.floor(raw_units_float)
    raw_notional = raw_units * price

    capped_units = raw_units
    capped_notional = raw_notional
    binding_cap = "none"
    for cap_name, cap_notional in (
        ("available_capital", available_capital),
        ("strategy_position_cap", strategy_position_cap),
        ("portfolio_exposure_cap", portfolio_exposure_cap),
    ):
        if capped_notional > cap_notional + _FLOAT_TOLERANCE:
            capped_notional = cap_notional
            capped_units = _units_for_notional(
                capped_notional,
                price=price,
                fractional_units_allowed=fractional_units_allowed,
            )
            capped_notional = capped_units * price
            binding_cap = cap_name

    final_units = capped_units
    final_notional = capped_notional
    if not leverage_allowed and final_notional > current_equity + _FLOAT_TOLERANCE:
        final_notional = current_equity
        final_units = _units_for_notional(
            final_notional,
            price=price,
            fractional_units_allowed=fractional_units_allowed,
        )
        final_notional = final_units * price
        binding_cap = "leverage_prohibition"

    return {
        "selected_risk_per_trade_pct": selected_risk_per_trade_pct,
        "current_equity": current_equity,
        "risk_budget": risk_budget,
        "per_unit_loss_to_protective_exit": per_unit_loss,
        "raw_units": raw_units,
        "raw_notional": raw_notional,
        "capped_units": capped_units,
        "capped_notional": capped_notional,
        "binding_cap": binding_cap,
        "final_units": final_units,
        "final_notional": final_notional,
    }


def build_portfolio_overlay_state(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(payload, name="portfolio overlay state")
    _reject_unknown_fields(
        payload,
        allowed={
            "current_equity",
            "peak_equity",
            "current_drawdown_pct",
            "current_gross_exposure_multiplier",
            "active_circuit_breaker_stage",
            "cooldown_remaining",
            "derisked_state",
            "recovery_state",
            "reentry_eligible",
        },
        name="portfolio overlay state",
    )
    current_equity = _required_positive_number(payload, "current_equity", strictly_positive=True)
    peak_equity = _required_positive_number(payload, "peak_equity", strictly_positive=True)
    if peak_equity + _FLOAT_TOLERANCE < current_equity:
        peak_equity = current_equity
    current_gross_exposure_multiplier = _required_multiplier(payload, "current_gross_exposure_multiplier")
    active_stage = _optional_stage(payload.get("active_circuit_breaker_stage"))
    cooldown_remaining = _required_non_negative_int(payload, "cooldown_remaining")
    derisked_state = _required_bool(payload, "derisked_state")
    recovery_state = _required_text(payload, "recovery_state")
    reentry_eligible = _required_bool(payload, "reentry_eligible")
    current_drawdown_pct = _drawdown_pct(peak_equity=peak_equity, current_equity=current_equity)
    supplied_drawdown = payload.get("current_drawdown_pct")
    if supplied_drawdown is not None:
        supplied_drawdown_value = _required_positive_number(
            {"current_drawdown_pct": supplied_drawdown},
            "current_drawdown_pct",
            strictly_positive=False,
        )
        if not math.isclose(
            supplied_drawdown_value,
            current_drawdown_pct,
            rel_tol=_FLOAT_TOLERANCE,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ValueError("current_drawdown_pct must match the canonical value derived from current_equity and peak_equity.")
    return {
        "current_equity": current_equity,
        "peak_equity": peak_equity,
        "current_drawdown_pct": current_drawdown_pct,
        "current_gross_exposure_multiplier": current_gross_exposure_multiplier,
        "active_circuit_breaker_stage": active_stage,
        "cooldown_remaining": cooldown_remaining,
        "derisked_state": derisked_state,
        "recovery_state": recovery_state,
        "reentry_eligible": reentry_eligible,
    }


def build_circuit_breaker_transition(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mapping(payload, name="circuit breaker transition")
    _reject_unknown_fields(
        payload,
        allowed={"prior_state", "current_equity", "thresholds", "reentry_rule"},
        name="circuit breaker transition",
    )
    prior_state = build_portfolio_overlay_state(_required_mapping(payload.get("prior_state"), name="prior_state"))
    current_equity = _required_positive_number(payload, "current_equity", strictly_positive=True)
    thresholds = _validated_thresholds(payload.get("thresholds"))
    reentry_rule = _validated_reentry_rule(payload.get("reentry_rule"))

    updated_peak = max(prior_state["peak_equity"], current_equity)
    current_drawdown_pct = _drawdown_pct(peak_equity=updated_peak, current_equity=current_equity)
    required_threshold = _active_threshold(current_drawdown_pct, thresholds)
    prior_active_stage = prior_state["active_circuit_breaker_stage"]
    if required_threshold is not None:
        required_stage = required_threshold["drawdown_pct"]
        required_multiplier = required_threshold["gross_exposure_multiplier"]
        if prior_active_stage is None:
            transition_reason = "threshold_activation"
            cooldown_remaining = reentry_rule["cooldown_days"]
            new_multiplier = required_multiplier
        elif required_stage > prior_active_stage + _FLOAT_TOLERANCE:
            transition_reason = "threshold_escalation"
            cooldown_remaining = reentry_rule["cooldown_days"]
            new_multiplier = required_multiplier
        else:
            transition_reason = "threshold_held"
            cooldown_remaining = prior_state["cooldown_remaining"]
            new_multiplier = min(prior_state["current_gross_exposure_multiplier"], required_multiplier)
        updated_state = {
            "current_equity": current_equity,
            "peak_equity": updated_peak,
            "current_drawdown_pct": current_drawdown_pct,
            "current_gross_exposure_multiplier": new_multiplier,
            "active_circuit_breaker_stage": _held_stage(prior_active_stage, required_stage),
            "cooldown_remaining": cooldown_remaining,
            "derisked_state": True,
            "recovery_state": "blocked_by_threshold",
            "reentry_eligible": False,
        }
        return {
            "updated_state": updated_state,
            "new_gross_exposure_multiplier": updated_state["current_gross_exposure_multiplier"],
            "transition_reason": transition_reason,
            "threshold_crossed": required_stage,
            "cooldown_status": _active_cooldown_status(transition_reason),
            "recovery_condition_met": False,
            "reentry_permitted": False,
        }

    cooldown_remaining = max(int(prior_state["cooldown_remaining"]) - 1, 0)
    recovery_floor = updated_peak * (1.0 - reentry_rule["recovery_from_peak_pct"] / 100.0)
    recovery_condition_met = current_equity + _FLOAT_TOLERANCE >= recovery_floor
    if prior_state["derisked_state"]:
        if cooldown_remaining == 0 and recovery_condition_met:
            updated_state = {
                "current_equity": current_equity,
                "peak_equity": updated_peak,
                "current_drawdown_pct": current_drawdown_pct,
                "current_gross_exposure_multiplier": 1.0,
                "active_circuit_breaker_stage": None,
                "cooldown_remaining": 0,
                "derisked_state": False,
                "recovery_state": "recovered",
                "reentry_eligible": True,
            }
            return {
                "updated_state": updated_state,
                "new_gross_exposure_multiplier": 1.0,
                "transition_reason": "reentry",
                "threshold_crossed": None,
                "cooldown_status": "completed",
                "recovery_condition_met": True,
                "reentry_permitted": True,
            }
        recovery_state, transition_reason = _cleared_recovery_status(
            cooldown_remaining=cooldown_remaining,
            recovery_condition_met=recovery_condition_met,
        )
        return {
            "updated_state": {
                "current_equity": current_equity,
                "peak_equity": updated_peak,
                "current_drawdown_pct": current_drawdown_pct,
                "current_gross_exposure_multiplier": prior_state["current_gross_exposure_multiplier"],
                "active_circuit_breaker_stage": None,
                "cooldown_remaining": cooldown_remaining,
                "derisked_state": True,
                "recovery_state": recovery_state,
                "reentry_eligible": False,
            },
            "new_gross_exposure_multiplier": prior_state["current_gross_exposure_multiplier"],
            "transition_reason": transition_reason,
            "threshold_crossed": None,
            "cooldown_status": "decremented" if prior_state["cooldown_remaining"] > 0 else "idle",
            "recovery_condition_met": recovery_condition_met,
            "reentry_permitted": False,
        }

    updated_state = {
        "current_equity": current_equity,
        "peak_equity": updated_peak,
        "current_drawdown_pct": current_drawdown_pct,
        "current_gross_exposure_multiplier": 1.0,
        "active_circuit_breaker_stage": None,
        "cooldown_remaining": 0,
        "derisked_state": False,
        "recovery_state": "not_applicable",
        "reentry_eligible": True,
    }
    return {
        "updated_state": updated_state,
        "new_gross_exposure_multiplier": 1.0,
        "transition_reason": "no_transition",
        "threshold_crossed": None,
        "cooldown_status": "idle",
        "recovery_condition_met": True,
        "reentry_permitted": True,
    }


def _validated_thresholds(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, list) or not value:
        raise ValueError("thresholds must be a non-empty list.")
    normalized: list[dict[str, float]] = []
    previous_drawdown = -math.inf
    previous_multiplier = math.inf
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each threshold must be a JSON object.")
        _reject_unknown_fields(
            item,
            allowed={"drawdown_pct", "gross_exposure_multiplier"},
            name="threshold",
        )
        drawdown_pct = _required_positive_number(item, "drawdown_pct", strictly_positive=True)
        multiplier = _required_multiplier(item, "gross_exposure_multiplier")
        if drawdown_pct <= previous_drawdown:
            raise ValueError("thresholds must be strictly increasing by drawdown_pct.")
        if multiplier > previous_multiplier + _FLOAT_TOLERANCE:
            raise ValueError("gross_exposure_multiplier must be non-increasing across thresholds.")
        previous_drawdown = drawdown_pct
        previous_multiplier = multiplier
        normalized.append(
            {
                "drawdown_pct": drawdown_pct,
                "gross_exposure_multiplier": multiplier,
            }
        )
    return normalized


def _validated_reentry_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("reentry_rule must be a JSON object.")
    _reject_unknown_fields(
        value,
        allowed={"type", "recovery_from_peak_pct", "cooldown_days"},
        name="reentry_rule",
    )
    rule_type = _required_enum(value, "type", ALLOWED_RECOVERY_TYPES)
    recovery_from_peak_pct = _required_positive_number(
        value,
        "recovery_from_peak_pct",
        strictly_positive=False,
    )
    cooldown_days = _required_non_negative_int(value, "cooldown_days")
    return {
        "type": rule_type,
        "recovery_from_peak_pct": recovery_from_peak_pct,
        "cooldown_days": cooldown_days,
    }


def _active_threshold(current_drawdown_pct: float, thresholds: list[dict[str, float]]) -> dict[str, float] | None:
    active: dict[str, float] | None = None
    for threshold in thresholds:
        if current_drawdown_pct + _FLOAT_TOLERANCE >= threshold["drawdown_pct"]:
            active = threshold
    return active


def _held_stage(prior_active_stage: float | None, required_stage: float) -> float:
    if prior_active_stage is None:
        return required_stage
    return max(prior_active_stage, required_stage)


def _active_cooldown_status(transition_reason: str) -> str:
    if transition_reason in {"threshold_activation", "threshold_escalation"}:
        return "started"
    return "held"


def _cleared_recovery_status(*, cooldown_remaining: int, recovery_condition_met: bool) -> tuple[str, str]:
    if cooldown_remaining > 0 and recovery_condition_met:
        return "cooldown_wait", "cooldown_wait"
    if cooldown_remaining > 0 and not recovery_condition_met:
        return "cooldown_and_recovery_wait", "cooldown_and_recovery_wait"
    if not recovery_condition_met:
        return "recovery_wait", "recovery_wait"
    return "reentry", "reentry"


def _drawdown_pct(*, peak_equity: float, current_equity: float) -> float:
    if peak_equity <= 0.0:
        raise ValueError("peak_equity must be positive.")
    return max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)


def _units_for_notional(notional: float, *, price: float, fractional_units_allowed: bool) -> float | int:
    units = notional / price
    return units if fractional_units_allowed else math.floor(units)


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return value


def _require_mapping(value: Any, *, name: str) -> None:
    _required_mapping(value, name=name)


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    for key in payload:
        if key not in allowed:
            raise ValueError(f"{name} contains unknown field: {key}")


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required.")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text fields must be strings when present.")
    text = value.strip()
    return text or None


def _required_enum(payload: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = _required_text(payload, field)
    if value not in allowed:
        raise ValueError(f"unknown {field}: {value}")
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


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean.")
    return value


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


def _optional_stage(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("active_circuit_breaker_stage must not be boolean.")
    try:
        stage = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("active_circuit_breaker_stage must be numeric when present.") from exc
    if not math.isfinite(stage) or stage <= 0.0:
        raise ValueError("active_circuit_breaker_stage must be positive when present.")
    return stage


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
