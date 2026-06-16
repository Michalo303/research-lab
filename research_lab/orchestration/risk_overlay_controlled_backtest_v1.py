from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.strategies.baselines import StrategySpec, build_weights


ARTIFACT_VERSION = "risk_overlay_execution_spec_artifact_v1"
ADAPTER_VERSION = "risk_overlay_execution_adapter_v1"
OUTPUT_VERSION = "risk_overlay_controlled_backtest_v1"


def run_risk_overlay_controlled_backtest(
    artifact: dict[str, Any],
    daily_panel: pd.DataFrame,
    *,
    cost_bps: float = 0.0,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Run a pure in-memory controlled backtest for a review-only risk overlay spec."""
    parameters = _validated_parameters(artifact)
    base_spec = _base_strategy_spec(parameters["base_strategy"])
    risk_overlay = parameters["risk_overlay"]

    close = close_frame(daily_panel)
    base_weights = build_weights(base_spec, daily_panel, None).reindex(close.index).fillna(0.0).clip(lower=0.0, upper=1.0)
    base_result = weighted_backtest(close, base_weights, cost_bps, periods_per_year)
    base_stress = cost_stress(close, base_weights, cost_bps, periods_per_year)

    overlay_candidates = []
    for risk_per_trade_pct in risk_overlay["position_sizing"]["risk_per_trade_pct_candidates"]:
        overlay_weights, events = _overlay_weights(base_weights, close, risk_overlay, float(risk_per_trade_pct))
        backtest = weighted_backtest(close, overlay_weights, cost_bps, periods_per_year)
        stress = cost_stress(close, overlay_weights, cost_bps, periods_per_year)
        overlay_candidates.append(
            {
                "risk_per_trade_pct": float(risk_per_trade_pct),
                "metrics": _json_metrics(backtest["metrics"]),
                "split_metrics": _json_nested(backtest["split_metrics"]),
                "cost_stress": _json_nested(stress),
                "average_turnover": float(backtest["average_turnover"]),
                "average_exposure": float(backtest["average_exposure"]),
                "max_drawdown_delta_vs_base": float(
                    backtest["metrics"]["max_drawdown"] - base_result["metrics"]["max_drawdown"]
                ),
                "pre_overlay_weight_records": _weight_records(base_weights),
                "overlay_weight_records": _weight_records(overlay_weights),
                "circuit_breaker_events": events,
            }
        )

    return {
        "version": OUTPUT_VERSION,
        "research_only": True,
        "production_paths": [],
        "file_outputs": [],
        "source_hypothesis_id": str(parameters.get("source_hypothesis_id") or ""),
        "source_note_ids": list(parameters.get("source_note_ids") or []),
        "provenance": deepcopy(artifact.get("provenance") or {}),
        "safety": _locked_safety(),
        "base": {
            "strategy": {
                "family": base_spec.family,
                "asset_class": base_spec.asset_class,
                "timeframe": base_spec.timeframe,
                "short_name": base_spec.short_name,
                "builder": base_spec.builder,
                "parameters": deepcopy(base_spec.parameters),
            },
            "metrics": _json_metrics(base_result["metrics"]),
            "split_metrics": _json_nested(base_result["split_metrics"]),
            "cost_stress": _json_nested(base_stress),
            "average_turnover": float(base_result["average_turnover"]),
            "average_exposure": float(base_result["average_exposure"]),
            "weight_records": _weight_records(base_weights),
        },
        "overlay_candidates": overlay_candidates,
    }


def _validated_parameters(artifact: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    if str(artifact.get("version") or "") != ARTIFACT_VERSION:
        raise ValueError(f"artifact.version must be {ARTIFACT_VERSION}")
    if str(artifact.get("adapter_version") or "") != ADAPTER_VERSION:
        raise ValueError(f"artifact.adapter_version must be {ADAPTER_VERSION}")
    if artifact.get("execution_spec_supported") is not True:
        raise ValueError("execution_spec_supported=true is required")
    if artifact.get("appendable_to_registry") is not False:
        raise ValueError("appendable_to_registry=false is required")
    if artifact.get("requires_human_review") is not True:
        raise ValueError("requires_human_review=true is required")
    if artifact.get("source_runtime_supported") is not False:
        raise ValueError("source_runtime_supported=false is required")

    execution_spec = artifact.get("execution_spec")
    if not isinstance(execution_spec, dict):
        raise ValueError("execution_spec must be a JSON object")
    if str(execution_spec.get("builder") or "") != ADAPTER_VERSION:
        raise ValueError(f"execution_spec.builder must be {ADAPTER_VERSION}")

    parameters = execution_spec.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("execution_spec.parameters must be a JSON object")
    if parameters.get("appendable_to_registry") is not False:
        raise ValueError("parameters.appendable_to_registry=false is required")
    if parameters.get("requires_human_review") is not True:
        raise ValueError("parameters.requires_human_review=true is required")
    if parameters.get("source_runtime_supported") is not False:
        raise ValueError("parameters.source_runtime_supported=false is required")

    _validate_base_strategy_selection(parameters.get("base_strategy_selection"))
    _validate_risk_overlay(parameters.get("risk_overlay"))
    return parameters


def _validate_base_strategy_selection(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("base_strategy_selection is required")
    for key in ("allowed_to_modify_signals", "allowed_to_modify_entries", "allowed_to_modify_exits"):
        if value.get(key) is not False:
            raise ValueError(f"{key}=false is required")


def _validate_risk_overlay(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("risk_overlay is required")
    position_sizing = value.get("position_sizing")
    if not isinstance(position_sizing, dict) or position_sizing.get("type") != "fixed_fractional":
        raise ValueError("risk_overlay.position_sizing.type=fixed_fractional is required")
    candidates = position_sizing.get("risk_per_trade_pct_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("risk_per_trade_pct_candidates are required")
    candidate_values = [_finite_float(item, "risk_per_trade_pct_candidates") for item in candidates]
    if candidate_values != sorted(candidate_values) or len(set(candidate_values)) != len(candidate_values):
        raise ValueError("risk_per_trade_pct_candidates must be strictly increasing")

    circuit = value.get("portfolio_drawdown_circuit_breaker")
    if not isinstance(circuit, dict) or circuit.get("type") != "staged_derisking":
        raise ValueError("staged_derisking circuit breaker is required")
    thresholds = circuit.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        raise ValueError("circuit breaker thresholds are required")
    previous_drawdown = -1.0
    previous_multiplier = 1.0
    for threshold in thresholds:
        if not isinstance(threshold, dict):
            raise ValueError("each threshold must be a JSON object")
        drawdown = _finite_float(threshold.get("drawdown_pct"), "drawdown_pct")
        multiplier = _finite_float(threshold.get("gross_exposure_multiplier"), "gross_exposure_multiplier")
        if drawdown <= previous_drawdown:
            raise ValueError("drawdown thresholds must be strictly increasing")
        if not 0.0 <= multiplier <= 1.0:
            raise ValueError("gross_exposure_multiplier must be within [0, 1]")
        if multiplier > previous_multiplier:
            raise ValueError("gross_exposure_multiplier must be non-increasing")
        previous_drawdown = drawdown
        previous_multiplier = multiplier
    reentry = circuit.get("reentry_rule")
    if not isinstance(reentry, dict) or reentry.get("type") != "equity_recovery":
        raise ValueError("equity_recovery reentry_rule is required")
    recovery_from_peak = reentry.get("recovery_from_peak_pct", 0.0)
    recovery_from_peak = _bounded_real_number(recovery_from_peak, "recovery_from_peak_pct", minimum=0.0, maximum=100.0)
    cooldown = reentry.get("cooldown_days")
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise ValueError("cooldown_days must be a non-negative integer")

    loser_rule = value.get("loser_addition_rule")
    if not isinstance(loser_rule, dict) or loser_rule.get("add_to_losers_allowed") is not False:
        raise ValueError("loser_addition_rule.add_to_losers_allowed=false is required")


def _base_strategy_spec(value: Any) -> StrategySpec:
    if not isinstance(value, dict):
        raise ValueError("base_strategy is required")
    parameters = value.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("base_strategy.parameters is required")
    return StrategySpec(
        family=str(value.get("family") or ""),
        asset_class=str(value.get("asset_class") or ""),
        timeframe=str(value.get("timeframe") or ""),
        short_name=str(value.get("short_name") or ""),
        hypothesis=str(value.get("hypothesis") or "risk overlay controlled base strategy"),
        parameters=deepcopy(parameters),
        rules=str(value.get("rules") or ""),
        builder=str(value.get("builder") or ""),
    )


def _overlay_weights(
    base_weights: pd.DataFrame,
    close: pd.DataFrame,
    risk_overlay: dict[str, Any],
    risk_per_trade_pct: float,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    circuit = risk_overlay["portfolio_drawdown_circuit_breaker"]
    thresholds = [
        {
            "drawdown": float(item["drawdown_pct"]) / 100.0,
            "multiplier": float(item["gross_exposure_multiplier"]),
        }
        for item in circuit["thresholds"]
    ]
    reentry = circuit["reentry_rule"]
    recovery_from_peak = float(reentry.get("recovery_from_peak_pct", 0.0)) / 100.0
    cooldown_days = int(reentry.get("cooldown_days", 0))

    scaled = base_weights * risk_per_trade_pct
    asset_returns = close.pct_change().fillna(0.0)
    active_multiplier = 1.0
    peak = 1.0
    equity = 1.0
    cooldown_remaining = 0
    events: list[dict[str, Any]] = []
    rows = []

    for ts in scaled.index:
        row = scaled.loc[ts] * active_multiplier
        rows.append(row)
        daily_return = float((row * asset_returns.loc[ts]).sum())
        equity *= 1.0 + daily_return
        peak = max(peak, equity)
        drawdown = 1.0 - equity / peak if peak > 0 else 0.0

        next_multiplier = _threshold_multiplier(thresholds, drawdown)
        if next_multiplier < active_multiplier:
            active_multiplier = next_multiplier
            cooldown_remaining = cooldown_days
            events.append(
                {
                    "date": _format_date(ts),
                    "event": "derisk",
                    "drawdown_pct": drawdown * 100.0,
                    "gross_exposure_multiplier": active_multiplier,
                    "cooldown_days": cooldown_days,
                }
            )
        elif active_multiplier < 1.0:
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
            recovered = equity >= peak * (1.0 - recovery_from_peak)
            if cooldown_remaining == 0 and recovered:
                active_multiplier = 1.0
                events.append(
                    {
                        "date": _format_date(ts),
                        "event": "reenter",
                        "drawdown_pct": drawdown * 100.0,
                        "gross_exposure_multiplier": active_multiplier,
                        "cooldown_days": 0,
                    }
                )

    overlay = pd.DataFrame(rows, index=scaled.index, columns=scaled.columns).fillna(0.0).clip(lower=0.0, upper=1.0)
    return overlay, events


def _threshold_multiplier(thresholds: list[dict[str, float]], drawdown: float) -> float:
    multiplier = 1.0
    for threshold in thresholds:
        if drawdown >= threshold["drawdown"]:
            multiplier = threshold["multiplier"]
    return multiplier


def _locked_safety() -> dict[str, bool]:
    return {
        "promotion_allowed": False,
        "deployment_allowed": False,
        "registry_write_allowed": False,
        "leaderboard_write_allowed": False,
        "report_write_allowed": False,
        "daily_research_run_allowed": False,
        "file_write_allowed": False,
        "requires_human_review": True,
    }


def _weight_records(weights: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    for ts, row in weights.iterrows():
        item: dict[str, Any] = {"date": _format_date(ts)}
        for column, value in row.items():
            item[str(column)] = float(value)
        records.append(item)
    return records


def _json_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_scalar(value) for key, value in metrics.items()}


def _json_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_nested(item) for item in value]
    return _json_scalar(value)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must not be boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not pd.notna(number):
        raise ValueError(f"{field} must be finite")
    return number


def _bounded_real_number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a real number")
    number = float(value)
    if not pd.notna(number):
        raise ValueError(f"{field} must be finite")
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be within [{minimum:g}, {maximum:g}]")
    return number


def _format_date(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
