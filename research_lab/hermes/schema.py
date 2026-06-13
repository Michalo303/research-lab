from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any


ETF_SYMBOLS = frozenset({"SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SHY", "IEF", "EFA", "EEM"})
CRYPTO_SYMBOLS = frozenset({"BTCUSDT"})
FAMILIES = frozenset({"LONGTERM", "ROTATION", "SWING", "INTRADAY"})
RISK_CONTROL_KEYS = frozenset(
    {
        "volatility_targeting",
        "drawdown_circuit_breakers",
        "cash_defensive_regimes",
        "exposure_caps",
        "correlation_aware_portfolio_risk",
        "crisis_period_diagnostics",
        "cost_slippage_stress",
        "parameter_neighborhood_stability",
    }
)
ALLOWED_HYPOTHESIS_FIELDS = frozenset(
    {
        "title",
        "family",
        "builder",
        "rationale",
        "parameters",
        "risk_controls",
        "tags",
        "source_url",
        "hypothesis_id",
        "asset_class",
        "timeframe",
        "hypothesis",
        "source_title",
        "source_key",
        "status",
        "research_only",
        "llm_generated",
        "hermes_run_id",
        "hermes_provider",
        "risk_management_priority",
        "optimization_objectives",
        "explicit_risk_controls",
        "deprioritize_when",
        "promotion_blocks",
        "logged_at",
        "used_note_ids",
    }
)


@dataclass(frozen=True)
class ParameterRule:
    kind: str
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: frozenset[str] | None = None
    min_items: int = 1
    max_items: int = 10


@dataclass(frozen=True)
class BuilderSchema:
    family: str
    parameters: dict[str, ParameterRule]


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    hypothesis: dict[str, Any] | None
    reasons: list[str]


def _integer(minimum: int, maximum: int, required: bool = True) -> ParameterRule:
    return ParameterRule("int", required=required, minimum=minimum, maximum=maximum)


def _number(minimum: float, maximum: float, required: bool = True) -> ParameterRule:
    return ParameterRule("number", required=required, minimum=minimum, maximum=maximum)


def _symbol(choices: frozenset[str] = ETF_SYMBOLS, required: bool = True) -> ParameterRule:
    return ParameterRule("symbol", required=required, choices=choices)


def _symbols(required: bool = True) -> ParameterRule:
    return ParameterRule("symbols", required=required, choices=ETF_SYMBOLS, min_items=2, max_items=10)


BUILDER_SCHEMAS: dict[str, BuilderSchema] = {
    "long_term_trend_filter": BuilderSchema("LONGTERM", {"symbol": _symbol(), "sma": _integer(50, 400)}),
    "long_term_vol_target": BuilderSchema(
        "LONGTERM",
        {"symbol": _symbol(), "sma": _integer(50, 400), "vol_window": _integer(20, 252), "target_vol": _number(0.03, 0.25)},
    ),
    "long_term_strict_cash_filter": BuilderSchema(
        "LONGTERM",
        {"symbol": _symbol(), "sma": _integer(100, 400), "confirmation_sma": _integer(20, 250)},
    ),
    "long_term_vol_target_cap": BuilderSchema(
        "LONGTERM",
        {
            "symbol": _symbol(),
            "sma": _integer(50, 400),
            "vol_window": _integer(20, 252),
            "target_vol": _number(0.03, 0.25),
            "max_weight": _number(0.05, 1.0),
        },
    ),
    "active_momentum_rotation": BuilderSchema(
        "ROTATION", {"symbols": _symbols(), "lookback": _integer(20, 252), "top_n": _integer(1, 5)}
    ),
    "rotation_momentum_drawdown_filter": BuilderSchema(
        "ROTATION",
        {
            "symbols": _symbols(),
            "lookback": _integer(20, 252),
            "top_n": _integer(1, 5),
            "risk_symbol": _symbol(),
            "risk_sma": _integer(50, 400),
        },
    ),
    "rotation_momentum_circuit_breaker": BuilderSchema(
        "ROTATION",
        {
            "symbols": _symbols(),
            "lookback": _integer(20, 252),
            "top_n": _integer(1, 5),
            "risk_symbol": _symbol(),
            "drawdown_threshold": _number(-0.40, -0.03),
            "recovery_sma": _integer(50, 400),
        },
    ),
    "defensive_asset_rotation": BuilderSchema(
        "ROTATION",
        {
            "risk_assets": _symbols(),
            "defensive_assets": _symbols(),
            "lookback": _integer(20, 252),
            "top_n": _integer(1, 3),
            "risk_symbol": _symbol(),
            "risk_sma": _integer(50, 400),
        },
    ),
    "swing_rsi_pullback": BuilderSchema(
        "SWING",
        {"symbol": _symbol(), "trend_sma": _integer(50, 400), "rsi_entry": _number(10, 50), "rsi_exit": _number(45, 90)},
    ),
    "swing_trend_filtered_pullback": BuilderSchema(
        "SWING",
        {
            "symbol": _symbol(),
            "fast_sma": _integer(20, 150),
            "slow_sma": _integer(100, 400),
            "rsi_entry": _number(10, 50),
            "rsi_exit": _number(45, 90),
            "atr_stop": _number(0.5, 5.0),
            "max_exposure": _number(0.05, 1.0, required=False),
        },
    ),
    "intraday_vwap_rsi_reclaim": BuilderSchema(
        "INTRADAY",
        {"symbol": _symbol(CRYPTO_SYMBOLS), "rsi_washout": _number(5, 45), "rsi_reclaim": _number(30, 70)},
    ),
}


def validate_hypothesis(item: Any) -> ValidationResult:
    if not isinstance(item, dict):
        return ValidationResult(False, None, ["hypothesis_not_object"])
    reasons: list[str] = []
    reasons.extend(f"unknown_field:{key}" for key in item if key not in ALLOWED_HYPOTHESIS_FIELDS)
    title = str(item.get("title", "")).strip()
    rationale = str(item.get("rationale", "")).strip()
    family = str(item.get("family", "")).strip().upper()
    builder = str(item.get("builder", "")).strip()
    if not title:
        reasons.append("missing_field:title")
    if not rationale:
        reasons.append("missing_field:rationale")
    if family not in FAMILIES:
        reasons.append("invalid_family")
    schema = BUILDER_SCHEMAS.get(builder)
    if schema is None:
        reasons.append("builder_not_allowed")
        return ValidationResult(False, None, reasons)
    if family != schema.family:
        reasons.append("family_builder_mismatch")
    parameters, parameter_reasons = _validate_parameters(item.get("parameters"), schema)
    reasons.extend(parameter_reasons)
    reasons.extend(_cross_validate(builder, parameters))
    risk_controls = item.get("risk_controls")
    if not isinstance(risk_controls, dict) or not risk_controls:
        reasons.append("risk_controls_required")
        risk_controls = {}
    else:
        reasons.extend(f"unknown_risk_control:{key}" for key in risk_controls if key not in RISK_CONTROL_KEYS)
        for key in RISK_CONTROL_KEYS:
            if key not in risk_controls:
                reasons.append(f"missing_risk_control:{key}")
            elif not isinstance(risk_controls[key], str) or not risk_controls[key].strip():
                reasons.append(f"invalid_risk_control:{key}")
    if family == "ROTATION" and not _has_strong_rotation_overlay(risk_controls):
        reasons.append("rotation_risk_overlay_required")
    tags = item.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        reasons.append("invalid_tags")
        tags = []
    used_note_ids = item.get("used_note_ids", [])
    if (
        not isinstance(used_note_ids, list)
        or len(used_note_ids) > 5
        or any(
            not isinstance(note_id, str)
            or not re.fullmatch(r"note-[0-9a-fA-F]{16}", note_id)
            for note_id in used_note_ids
        )
    ):
        reasons.append("invalid_used_note_ids")
        used_note_ids = []
    if reasons:
        return ValidationResult(False, None, reasons)
    normalized = {
        "title": title,
        "family": family,
        "builder": builder,
        "rationale": rationale,
        "parameters": parameters,
        "risk_controls": copy.deepcopy(risk_controls),
        "tags": [tag.strip() for tag in tags if tag.strip()],
        "source_url": str(item.get("source_url", "")).strip(),
        "used_note_ids": list(dict.fromkeys(used_note_ids)),
    }
    return ValidationResult(True, normalized, [])


def _validate_parameters(value: Any, schema: BuilderSchema) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, ["parameters_not_object"]
    reasons = [f"unknown_parameter:{key}" for key in value if key not in schema.parameters]
    normalized: dict[str, Any] = {}
    for name, rule in schema.parameters.items():
        if name not in value:
            if rule.required:
                reasons.append(f"missing_parameter:{name}")
            continue
        accepted, clean = _validate_parameter(value[name], rule)
        if not accepted:
            reasons.append(f"invalid_parameter:{name}")
        else:
            normalized[name] = clean
    return normalized, reasons


def _validate_parameter(value: Any, rule: ParameterRule) -> tuple[bool, Any]:
    if rule.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, None
        return _in_range(value, rule), value
    if rule.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return False, None
        clean = float(value)
        return _in_range(clean, rule), clean
    if rule.kind == "symbol":
        clean = str(value).strip().upper()
        return bool(clean and rule.choices and clean in rule.choices), clean
    if rule.kind == "symbols":
        if not isinstance(value, list):
            return False, None
        clean = [str(symbol).strip().upper() for symbol in value]
        if not rule.min_items <= len(clean) <= rule.max_items or len(clean) != len(set(clean)):
            return False, None
        return bool(rule.choices and all(symbol in rule.choices for symbol in clean)), clean
    return False, None


def _in_range(value: float, rule: ParameterRule) -> bool:
    return (rule.minimum is None or value >= rule.minimum) and (rule.maximum is None or value <= rule.maximum)


def _has_strong_rotation_overlay(controls: dict[str, Any]) -> bool:
    required = {
        "volatility_targeting",
        "drawdown_circuit_breakers",
        "cash_defensive_regimes",
        "exposure_caps",
        "correlation_aware_portfolio_risk",
    }
    return all(str(controls.get(key, "")).strip().lower() not in {"", "none", "n/a"} for key in required)


def _cross_validate(builder: str, parameters: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if "top_n" in parameters:
        universe = parameters.get("symbols") or parameters.get("risk_assets") or []
        if universe and parameters["top_n"] > len(universe):
            reasons.append("invalid_parameter:top_n")
    if builder in {"rotation_momentum_drawdown_filter", "rotation_momentum_circuit_breaker"}:
        if parameters.get("risk_symbol") not in parameters.get("symbols", []):
            reasons.append("invalid_parameter:risk_symbol")
    if builder == "defensive_asset_rotation":
        risk_assets = parameters.get("risk_assets", [])
        defensive_assets = parameters.get("defensive_assets", [])
        if parameters.get("risk_symbol") not in risk_assets:
            reasons.append("invalid_parameter:risk_symbol")
        if set(risk_assets) & set(defensive_assets):
            reasons.append("overlapping_rotation_assets")
    if builder == "long_term_strict_cash_filter":
        if parameters.get("confirmation_sma", 0) >= parameters.get("sma", 0):
            reasons.append("confirmation_sma_must_be_below_sma")
    if builder == "swing_trend_filtered_pullback":
        if parameters.get("fast_sma", 0) >= parameters.get("slow_sma", 0):
            reasons.append("fast_sma_must_be_below_slow_sma")
    if builder in {"swing_rsi_pullback", "swing_trend_filtered_pullback"}:
        if parameters.get("rsi_entry", 0) >= parameters.get("rsi_exit", 0):
            reasons.append("rsi_entry_must_be_below_rsi_exit")
    if builder == "intraday_vwap_rsi_reclaim":
        if parameters.get("rsi_washout", 0) >= parameters.get("rsi_reclaim", 0):
            reasons.append("rsi_washout_must_be_below_rsi_reclaim")
    return reasons


def schema_prompt_text() -> str:
    lines = ["Allowed strategy builders and exact parameter schemas:"]
    for builder, schema in BUILDER_SCHEMAS.items():
        parameter_text = ", ".join(f"{name}:{rule.kind}" for name, rule in schema.parameters.items())
        lines.append(f"- {builder} ({schema.family}): {parameter_text}")
    lines.append("Unknown builders, unknown parameters, executable code, and values outside the schema are rejected.")
    return "\n".join(lines)


def execution_fingerprint(hypothesis: dict[str, Any]) -> str:
    payload = {
        "family": hypothesis.get("family"),
        "builder": hypothesis.get("builder"),
        "parameters": hypothesis.get("parameters", {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
