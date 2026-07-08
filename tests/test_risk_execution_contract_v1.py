from __future__ import annotations

import copy
import math

import pytest

from research_lab.execution.risk_execution_contract_v1 import (
    build_circuit_breaker_transition,
    build_fixed_fractional_sizing,
    build_portfolio_overlay_state,
    build_protective_exit_contract,
    build_strategy_event,
)


def _event_payload() -> dict[str, object]:
    return {
        "timestamp": "2026-01-05",
        "event_type": "entry",
        "symbol": "SPY",
        "target_direction": "long",
        "strategy_identity": "SWING_ETF_1D_QUEUE_PULLBACK",
        "event_id": "evt-001",
        "reason_code": "trend_pullback_entry",
    }


def _protective_exit_payload() -> dict[str, object]:
    return {
        "entry_price": 100.0,
        "protective_exit_price": 97.5,
        "per_unit_loss_to_protective_exit": 2.5,
        "protective_exit_type": "atr_stop",
        "strategy_provenance": "spec.parameters.atr_stop",
    }


def _sizing_payload() -> dict[str, object]:
    return {
        "current_equity": 100_000.0,
        "selected_risk_per_trade_pct": 1.0,
        "per_unit_loss_to_protective_exit": 2.5,
        "price": 50.0,
        "available_capital": 100_000.0,
        "strategy_position_cap": 100_000.0,
        "portfolio_exposure_cap": 100_000.0,
        "leverage_allowed": False,
        "fractional_units_allowed": False,
    }


def _overlay_state_payload() -> dict[str, object]:
    return {
        "current_equity": 100_000.0,
        "peak_equity": 100_000.0,
        "current_gross_exposure_multiplier": 1.0,
        "active_circuit_breaker_stage": None,
        "cooldown_remaining": 0,
        "derisked_state": False,
        "recovery_state": "not_applicable",
        "reentry_eligible": True,
    }


def _transition_payload() -> dict[str, object]:
    return {
        "prior_state": build_portfolio_overlay_state(_overlay_state_payload()),
        "current_equity": 92_000.0,
        "thresholds": [
            {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75},
            {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.50},
            {"drawdown_pct": 10.0, "gross_exposure_multiplier": 0.0},
        ],
        "reentry_rule": {
            "type": "equity_recovery",
            "recovery_from_peak_pct": 2.0,
            "cooldown_days": 2,
        },
    }


def test_valid_entry_event_passes():
    payload = build_strategy_event(_event_payload())

    assert payload == {
        "timestamp": "2026-01-05",
        "event_type": "entry",
        "symbol": "SPY",
        "target_direction": "long",
        "strategy_identity": "SWING_ETF_1D_QUEUE_PULLBACK",
        "event_id": "evt-001",
        "reason_code": "trend_pullback_entry",
    }


def test_invalid_event_type_fails():
    payload = _event_payload()
    payload["event_type"] = "hold"

    with pytest.raises(ValueError, match="unknown event_type"):
        build_strategy_event(payload)


def test_missing_event_identity_fails():
    payload = _event_payload()
    payload["event_id"] = "  "

    with pytest.raises(ValueError, match="event_id"):
        build_strategy_event(payload)


def test_protective_exit_computes_exact_per_unit_loss_distance():
    payload = build_protective_exit_contract(_protective_exit_payload())

    assert payload["per_unit_loss_to_protective_exit"] == pytest.approx(2.5)


def test_zero_or_negative_loss_distance_fails():
    payload = _protective_exit_payload()
    payload["protective_exit_price"] = 100.0
    payload["per_unit_loss_to_protective_exit"] = 0.0

    with pytest.raises(ValueError, match="positive"):
        build_protective_exit_contract(payload)


def test_protective_exit_look_ahead_fields_fail():
    payload = _protective_exit_payload()
    payload["future_price"] = 105.0

    with pytest.raises(ValueError, match="unknown field"):
        build_protective_exit_contract(payload)


def test_fixed_fractional_risk_budget_is_correct():
    result = build_fixed_fractional_sizing(_sizing_payload())

    assert result["selected_risk_per_trade_pct"] == 1.0
    assert result["current_equity"] == 100_000.0
    assert result["risk_budget"] == pytest.approx(1_000.0)
    assert result["raw_units"] == 400
    assert result["raw_notional"] == pytest.approx(20_000.0)
    assert result["binding_cap"] == "none"
    assert result["final_units"] == 400
    assert result["final_notional"] == pytest.approx(20_000.0)


def test_integer_units_use_floor():
    payload = _sizing_payload()
    payload["per_unit_loss_to_protective_exit"] = 3.0

    result = build_fixed_fractional_sizing(payload)

    assert result["raw_units"] == 333


def test_fractional_units_remain_fractional_when_allowed():
    payload = _sizing_payload()
    payload["per_unit_loss_to_protective_exit"] = 3.0
    payload["fractional_units_allowed"] = True

    result = build_fixed_fractional_sizing(payload)

    assert result["raw_units"] == pytest.approx(333.3333333333333)


def test_available_capital_cap_binds_correctly():
    payload = _sizing_payload()
    payload["available_capital"] = 10_000.0

    result = build_fixed_fractional_sizing(payload)

    assert result["binding_cap"] == "available_capital"
    assert result["final_units"] == 200
    assert result["final_notional"] == pytest.approx(10_000.0)


def test_strategy_position_cap_binds_correctly():
    payload = _sizing_payload()
    payload["available_capital"] = 100_000.0
    payload["strategy_position_cap"] = 8_000.0

    result = build_fixed_fractional_sizing(payload)

    assert result["binding_cap"] == "strategy_position_cap"
    assert result["final_units"] == 160
    assert result["final_notional"] == pytest.approx(8_000.0)


def test_portfolio_exposure_cap_binds_correctly():
    payload = _sizing_payload()
    payload["portfolio_exposure_cap"] = 7_000.0

    result = build_fixed_fractional_sizing(payload)

    assert result["binding_cap"] == "portfolio_exposure_cap"
    assert result["final_units"] == 140
    assert result["final_notional"] == pytest.approx(7_000.0)


def test_leverage_is_refused_when_not_allowed():
    payload = _sizing_payload()
    payload["selected_risk_per_trade_pct"] = 10.0
    payload["per_unit_loss_to_protective_exit"] = 0.5
    payload["available_capital"] = 2_000_000.0
    payload["strategy_position_cap"] = 2_000_000.0
    payload["portfolio_exposure_cap"] = 2_000_000.0

    result = build_fixed_fractional_sizing(payload)

    assert result["binding_cap"] == "leverage_prohibition"
    assert result["final_notional"] == pytest.approx(100_000.0)


def test_sizing_rejects_look_ahead_fields():
    payload = _sizing_payload()
    payload["future_equity"] = 120_000.0

    with pytest.raises(ValueError, match="unknown field"):
        build_fixed_fractional_sizing(payload)


def test_circuit_breaker_threshold_activation_works():
    result = build_circuit_breaker_transition(_transition_payload())

    assert result["new_gross_exposure_multiplier"] == pytest.approx(0.5)
    assert result["threshold_crossed"] == 8.0
    assert result["transition_reason"] == "threshold_activation"
    assert result["updated_state"]["cooldown_remaining"] == 2


def test_exposure_multiplier_decreases_at_deeper_thresholds():
    first = build_circuit_breaker_transition(_transition_payload())
    second_payload = _transition_payload()
    second_payload["prior_state"] = copy.deepcopy(first["updated_state"])
    second_payload["current_equity"] = 89_000.0

    second = build_circuit_breaker_transition(second_payload)

    assert second["updated_state"]["current_gross_exposure_multiplier"] == pytest.approx(0.0)
    assert second["threshold_crossed"] == 10.0


def test_repeated_identical_active_stage_evaluation_holds_cooldown_and_multiplier():
    activated = build_circuit_breaker_transition(_transition_payload())
    held_payload = _transition_payload()
    held_payload["prior_state"] = copy.deepcopy(activated["updated_state"])
    held_payload["current_equity"] = 92_000.0

    held = build_circuit_breaker_transition(held_payload)

    assert held["updated_state"]["cooldown_remaining"] == 2
    assert held["updated_state"]["current_gross_exposure_multiplier"] == pytest.approx(0.5)
    assert held["transition_reason"] == "threshold_held"
    assert held["cooldown_status"] == "held"


def test_escalation_to_deeper_stage_resets_cooldown():
    activated = build_circuit_breaker_transition(_transition_payload())
    escalation_payload = _transition_payload()
    escalation_payload["prior_state"] = copy.deepcopy(activated["updated_state"])
    escalation_payload["current_equity"] = 89_000.0

    escalated = build_circuit_breaker_transition(escalation_payload)

    assert escalated["updated_state"]["cooldown_remaining"] == 2
    assert escalated["transition_reason"] == "threshold_escalation"


def test_repeated_deeper_stage_evaluation_does_not_reset_cooldown_again():
    activated = build_circuit_breaker_transition(_transition_payload())
    escalated = build_circuit_breaker_transition(
        {
            **_transition_payload(),
            "prior_state": copy.deepcopy(activated["updated_state"]),
            "current_equity": 89_000.0,
        }
    )
    held_payload = _transition_payload()
    held_payload["prior_state"] = copy.deepcopy(escalated["updated_state"])
    held_payload["current_equity"] = 89_000.0

    held = build_circuit_breaker_transition(held_payload)

    assert held["updated_state"]["cooldown_remaining"] == 2
    assert held["transition_reason"] == "threshold_held"


def test_recovery_to_shallower_but_still_active_threshold_does_not_increase_exposure():
    activated = build_circuit_breaker_transition(_transition_payload())
    escalated = build_circuit_breaker_transition(
        {
            **_transition_payload(),
            "prior_state": copy.deepcopy(activated["updated_state"]),
            "current_equity": 89_000.0,
        }
    )
    shallower_payload = _transition_payload()
    shallower_payload["prior_state"] = copy.deepcopy(escalated["updated_state"])
    shallower_payload["current_equity"] = 94_000.0

    held = build_circuit_breaker_transition(shallower_payload)

    assert held["updated_state"]["current_gross_exposure_multiplier"] == pytest.approx(0.0)
    assert held["transition_reason"] == "threshold_held"


def test_cooldown_does_not_decrement_while_any_threshold_remains_active():
    activated = build_circuit_breaker_transition(_transition_payload())
    held_payload = _transition_payload()
    held_payload["prior_state"] = copy.deepcopy(activated["updated_state"])
    held_payload["current_equity"] = 94_000.0

    held = build_circuit_breaker_transition(held_payload)

    assert held["updated_state"]["cooldown_remaining"] == 2


def test_cooldown_decrements_only_after_all_thresholds_clear():
    activated = build_circuit_breaker_transition(_transition_payload())
    cleared_payload = _transition_payload()
    cleared_payload["prior_state"] = copy.deepcopy(activated["updated_state"])
    cleared_payload["current_equity"] = 99_000.0

    result = build_circuit_breaker_transition(cleared_payload)

    assert result["updated_state"]["cooldown_remaining"] == 1
    assert result["reentry_permitted"] is False
    assert result["transition_reason"] == "cooldown_wait"


def test_recovery_without_completed_cooldown_does_not_reenter():
    activated = build_circuit_breaker_transition(_transition_payload())
    payload = _transition_payload()
    payload["prior_state"] = copy.deepcopy(activated["updated_state"])
    payload["current_equity"] = 99_500.0

    result = build_circuit_breaker_transition(payload)

    assert result["recovery_condition_met"] is True
    assert result["reentry_permitted"] is False
    assert result["transition_reason"] == "cooldown_wait"


def test_completed_cooldown_without_recovery_does_not_reenter():
    activated = build_circuit_breaker_transition(_transition_payload())
    state = copy.deepcopy(activated["updated_state"])
    state["cooldown_remaining"] = 0
    payload = _transition_payload()
    payload["prior_state"] = state
    payload["current_equity"] = 97_000.0

    result = build_circuit_breaker_transition(payload)

    assert result["recovery_condition_met"] is False
    assert result["reentry_permitted"] is False
    assert result["transition_reason"] == "recovery_wait"


def test_reentry_requires_both_cooldown_and_recovery():
    activated = build_circuit_breaker_transition(_transition_payload())
    state = copy.deepcopy(activated["updated_state"])
    state["cooldown_remaining"] = 0
    payload = _transition_payload()
    payload["prior_state"] = state
    payload["current_equity"] = 99_500.0

    result = build_circuit_breaker_transition(payload)

    assert result["recovery_condition_met"] is True
    assert result["reentry_permitted"] is True
    assert result["updated_state"]["current_gross_exposure_multiplier"] == pytest.approx(1.0)
    assert result["transition_reason"] == "reentry"


def test_deterministic_repeated_transition_produces_identical_output():
    payload = _transition_payload()

    first = build_circuit_breaker_transition(copy.deepcopy(payload))
    second = build_circuit_breaker_transition(copy.deepcopy(payload))

    assert first == second


def test_unordered_thresholds_fail():
    payload = _transition_payload()
    payload["thresholds"] = [
        {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.5},
        {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75},
    ]

    with pytest.raises(ValueError, match="strictly increasing"):
        build_circuit_breaker_transition(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entry_price", math.nan),
        ("entry_price", math.inf),
        ("entry_price", -math.inf),
        ("entry_price", True),
        ("protective_exit_price", math.nan),
        ("protective_exit_price", math.inf),
        ("protective_exit_price", -math.inf),
        ("protective_exit_price", False),
        ("per_unit_loss_to_protective_exit", math.nan),
        ("per_unit_loss_to_protective_exit", math.inf),
        ("per_unit_loss_to_protective_exit", -math.inf),
        ("per_unit_loss_to_protective_exit", True),
    ],
)
def test_protective_exit_rejects_nan_infinity_and_boolean_numerics(field, value):
    payload = _protective_exit_payload()
    payload[field] = value

    with pytest.raises(ValueError):
        build_protective_exit_contract(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_equity", math.nan),
        ("current_equity", math.inf),
        ("current_equity", -math.inf),
        ("current_equity", True),
        ("selected_risk_per_trade_pct", math.nan),
        ("selected_risk_per_trade_pct", math.inf),
        ("selected_risk_per_trade_pct", -math.inf),
        ("selected_risk_per_trade_pct", False),
        ("price", math.nan),
        ("price", math.inf),
        ("price", -math.inf),
        ("price", True),
        ("available_capital", math.nan),
        ("available_capital", math.inf),
        ("available_capital", -math.inf),
        ("available_capital", False),
        ("strategy_position_cap", math.nan),
        ("strategy_position_cap", math.inf),
        ("strategy_position_cap", -math.inf),
        ("strategy_position_cap", True),
        ("portfolio_exposure_cap", math.nan),
        ("portfolio_exposure_cap", math.inf),
        ("portfolio_exposure_cap", -math.inf),
        ("portfolio_exposure_cap", False),
        ("per_unit_loss_to_protective_exit", math.nan),
        ("per_unit_loss_to_protective_exit", math.inf),
        ("per_unit_loss_to_protective_exit", -math.inf),
        ("per_unit_loss_to_protective_exit", True),
    ],
)
def test_sizing_rejects_nan_infinity_and_boolean_numerics(field, value):
    payload = _sizing_payload()
    payload[field] = value

    with pytest.raises(ValueError):
        build_fixed_fractional_sizing(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_equity", math.nan),
        ("current_equity", math.inf),
        ("current_equity", -math.inf),
        ("current_equity", True),
        ("peak_equity", math.nan),
        ("peak_equity", math.inf),
        ("peak_equity", -math.inf),
        ("peak_equity", False),
    ],
)
def test_overlay_state_rejects_nan_infinity_and_boolean_numerics(field, value):
    payload = _overlay_state_payload()
    payload[field] = value

    with pytest.raises(ValueError):
        build_portfolio_overlay_state(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("drawdown_pct", math.nan),
        ("drawdown_pct", math.inf),
        ("drawdown_pct", -math.inf),
        ("drawdown_pct", True),
        ("gross_exposure_multiplier", math.nan),
        ("gross_exposure_multiplier", math.inf),
        ("gross_exposure_multiplier", -math.inf),
        ("gross_exposure_multiplier", False),
    ],
)
def test_thresholds_reject_nan_infinity_and_boolean_numerics(field, value):
    payload = _transition_payload()
    payload["thresholds"][0][field] = value

    with pytest.raises(ValueError):
        build_circuit_breaker_transition(payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_reentry_rule_recovery_percentage_rejects_nan_infinity_and_boolean_numerics(value):
    payload = _transition_payload()
    payload["reentry_rule"]["recovery_from_peak_pct"] = value

    with pytest.raises(ValueError):
        build_circuit_breaker_transition(payload)
