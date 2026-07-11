from __future__ import annotations

from typing import Any


KNOWN_BUILDERS = (
    "active_momentum_rotation",
    "defensive_asset_rotation",
    "intraday_vwap_rsi_reclaim",
    "long_term_strict_cash_filter",
    "long_term_trend_filter",
    "long_term_vol_target",
    "long_term_vol_target_cap",
    "rotation_momentum_circuit_breaker",
    "rotation_momentum_drawdown_filter",
    "swing_rsi_pullback",
    "swing_trend_filtered_pullback",
)

CAPABILITY_FIELDS = (
    "emits_entry_events",
    "emits_exit_events",
    "emits_rebalance_events",
    "exposes_protective_exit",
    "exposes_per_unit_loss_distance",
    "supports_fractional_units",
    "supports_position_caps",
    "supports_portfolio_overlay",
)


def get_strategy_execution_capability(builder: str) -> dict[str, Any]:
    normalized_builder = str(builder or "").strip()
    if not normalized_builder:
        raise ValueError("builder is required.")
    reason = "builder is not explicitly supported"
    if normalized_builder == "swing_trend_filtered_pullback":
        payload = _unsupported_capability(
            normalized_builder,
            reason=(
                "A synthetic-only contract helper exists for entry, exit, protective-exit, per-unit loss, "
                "and stop-refresh derivation, but production runtime and live risk-overlay execution remain unsupported."
            ),
        )
        payload["emits_entry_events"] = True
        payload["emits_exit_events"] = True
        payload["emits_rebalance_events"] = False
        payload["exposes_protective_exit"] = True
        payload["exposes_per_unit_loss_distance"] = True
        return validate_strategy_execution_capability(payload)
    return _unsupported_capability(normalized_builder, reason=reason)


def supported_strategy_execution_builders() -> list[dict[str, Any]]:
    return [get_strategy_execution_capability(builder) for builder in KNOWN_BUILDERS]


def validate_strategy_execution_capability(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("strategy execution capability must be a JSON object.")
    allowed = {
        "builder",
        *CAPABILITY_FIELDS,
        "supports_fractional_units",
        "supports_position_caps",
        "supports_portfolio_overlay",
        "supported_for_risk_overlay_execution",
        "unsupported_reason",
    }
    for key in payload:
        if key not in allowed:
            raise ValueError(f"strategy execution capability contains unknown field: {key}")
    builder = payload.get("builder")
    if not isinstance(builder, str) or not builder.strip():
        raise ValueError("builder is required.")
    normalized: dict[str, Any] = {"builder": builder.strip()}
    for field in CAPABILITY_FIELDS:
        value = payload.get(field)
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be boolean.")
        normalized[field] = value
    supported = payload.get("supported_for_risk_overlay_execution")
    if not isinstance(supported, bool):
        raise ValueError("supported_for_risk_overlay_execution must be boolean.")
    normalized["supported_for_risk_overlay_execution"] = supported
    reason = payload.get("unsupported_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("unsupported_reason is required.")
    normalized["unsupported_reason"] = reason.strip()
    if supported:
        raise ValueError("supported_for_risk_overlay_execution=true is not permitted until explicitly proven.")
    return normalized


def _unsupported_capability(builder: str, *, reason: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "builder": builder,
        "supported_for_risk_overlay_execution": False,
        "unsupported_reason": reason,
    }
    for field in CAPABILITY_FIELDS:
        payload[field] = False
    return validate_strategy_execution_capability(payload)
