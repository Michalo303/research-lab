from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any


ADAPTER_VERSION = "risk_overlay_execution_adapter_v1"
OUTPUT_VERSION = "risk_overlay_execution_spec_artifact_v1"
QUEUE_ENTRY_VERSION = "hypothesis_queue_entry_candidate_v1"
QUEUE_ROW_VERSION = "risk_overlay_hypothesis_queue_row_v1"
QUEUE_FAMILY = "RISK_OVERLAY"
SUPPORTED_BLOCKER = "drawdown_fail"

SUPPORTED_BASE_STRATEGIES: dict[tuple[str, str, str], dict[str, str]] = {
    ("LONGTERM", "ETF", "1D"): {
        "TREND_FILTER": "long_term_trend_filter",
        "TREND_STRICT_CASH": "long_term_strict_cash_filter",
        "TREND_VOL_CAP": "long_term_vol_target_cap",
        "TREND_VOL_CAP_CONSERVATIVE": "long_term_vol_target_cap",
        "TREND_VOL_CAP_STABLE": "long_term_vol_target_cap",
        "QUEUE_VOL_TARGET": "long_term_vol_target",
    },
    ("ROTATION", "ETF", "1D"): {
        "DUAL_MOMENTUM": "active_momentum_rotation",
        "DUAL_MOMENTUM_DD_CB": "rotation_momentum_circuit_breaker",
        "DEFENSIVE_ROTATION": "defensive_asset_rotation",
        "QUEUE_MOM_DD": "rotation_momentum_drawdown_filter",
    },
    ("SWING", "ETF", "1D"): {
        "RSI_PULLBACK": "swing_rsi_pullback",
        "QUEUE_PULLBACK": "swing_trend_filtered_pullback",
    },
}


def build_risk_overlay_execution_spec(
    artifact: Any,
    *,
    source_artifact_path: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_input_artifact(artifact)
    queue_row = normalized["queue_row"]

    blocker = _validated_blocker(normalized["blocker"])
    _validate_queue_row(queue_row, blocker=blocker)

    source_notes = _source_notes(artifact, queue_row)
    source_note_ids = _source_note_ids(queue_row)
    if source_notes is not None:
        note_ids_from_notes = [str(item["note_id"]).strip() for item in source_notes]
        if note_ids_from_notes != source_note_ids:
            raise ValueError("lossy conversion: source_notes.note_id values must exactly match source_note_ids.")

    execution_spec = _execution_spec(queue_row)
    output = {
        "version": OUTPUT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "execution_spec_supported": True,
        "appendable_to_registry": False,
        "requires_human_review": True,
        "source_runtime_supported": False,
        "provenance": {
            "blocker": blocker,
            "source_note_ids": source_note_ids,
            "source_artifact_type": normalized["source_artifact_type"],
            "source_artifact_version": normalized["source_artifact_version"],
            "source_artifact_sha256": _sha256(artifact),
        },
        "execution_spec": execution_spec,
    }
    if source_artifact_path:
        output["provenance"]["source_artifact_path"] = source_artifact_path
    if normalized["candidate_artifact_hash"]:
        output["provenance"]["candidate_artifact_hash"] = normalized["candidate_artifact_hash"]
    if source_notes is not None:
        output["provenance"]["source_notes"] = source_notes
    return output


def _normalize_input_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")

    version = str(artifact.get("version") or "").strip()
    queue_row_version = str(artifact.get("queue_row_version") or "").strip()

    if version == QUEUE_ENTRY_VERSION:
        if artifact.get("runtime_supported") is not False:
            raise ValueError("source candidate must keep runtime_supported=false.")
        if artifact.get("appendable_to_registry") is not False:
            raise ValueError("source candidate must keep appendable_to_registry=false.")
        if artifact.get("compatible") not in (False, None):
            raise ValueError("source candidate must remain non-compatible for direct execution.")
        required_runtime_hook = artifact.get("required_runtime_hook")
        if not isinstance(required_runtime_hook, dict) or str(required_runtime_hook.get("type") or "").strip() != ADAPTER_VERSION:
            raise ValueError(f"source candidate must require runtime hook {ADAPTER_VERSION}.")
        queue_row = artifact.get("queue_row")
        if not isinstance(queue_row, dict):
            raise ValueError("source candidate must contain queue_row.")
        return {
            "blocker": str(artifact.get("target_failure_mode") or queue_row.get("target_failure_mode") or "").strip(),
            "queue_row": deepcopy(queue_row),
            "source_artifact_type": "hypothesis_queue_entry_candidate",
            "source_artifact_version": version,
            "candidate_artifact_hash": _optional_text(artifact.get("candidate_artifact_hash") or artifact.get("artifact_hash")),
        }

    if queue_row_version == QUEUE_ROW_VERSION:
        if artifact.get("runtime_supported") not in (None, False):
            raise ValueError("source queue row must keep runtime_supported=false when present.")
        if artifact.get("appendable_to_registry") not in (None, False):
            raise ValueError("source queue row must keep appendable_to_registry=false when present.")
        return {
            "blocker": str(artifact.get("target_failure_mode") or "").strip(),
            "queue_row": deepcopy(artifact),
            "source_artifact_type": "risk_overlay_hypothesis_queue_row",
            "source_artifact_version": queue_row_version,
            "candidate_artifact_hash": _optional_text(artifact.get("candidate_artifact_hash") or artifact.get("artifact_hash")),
        }

    raise ValueError(
        "artifact must be a hypothesis_queue_entry_candidate_v1 review artifact or risk_overlay_hypothesis_queue_row_v1 queue row"
    )


def _validated_blocker(blocker: str) -> str:
    if blocker != SUPPORTED_BLOCKER:
        raise ValueError(f"unsupported blocker: {blocker or '<missing>'}")
    return blocker


def _validate_queue_row(queue_row: dict[str, Any], *, blocker: str) -> None:
    family = str(queue_row.get("family") or "").strip()
    if family != QUEUE_FAMILY:
        raise ValueError(f"unsupported queue row family: {family or '<missing>'}")

    if str(queue_row.get("target_failure_mode") or "").strip() != blocker:
        raise ValueError("lossy conversion: queue row target_failure_mode must match blocker provenance.")

    _source_note_ids(queue_row)
    _validate_base_strategy_selection(queue_row.get("base_strategy_selection"))
    _validate_base_strategy(queue_row.get("base_strategy"))
    _validate_risk_overlay(queue_row.get("risk_overlay"))
    _validate_validation_plan(queue_row.get("validation_plan"))


def _source_note_ids(queue_row: dict[str, Any]) -> list[str]:
    value = queue_row.get("source_note_ids")
    if not isinstance(value, list):
        raise ValueError("RISK_OVERLAY adapter requires non-empty source_note_ids provenance.")
    note_ids = [str(item).strip() for item in value]
    if not note_ids or any(not item for item in note_ids):
        raise ValueError("RISK_OVERLAY adapter requires non-empty source_note_ids provenance.")
    if len(set(note_ids)) != len(note_ids):
        raise ValueError("lossy conversion: source_note_ids must be unique and ordered.")
    return note_ids


def _source_notes(artifact: dict[str, Any], queue_row: dict[str, Any]) -> list[dict[str, Any]] | None:
    for candidate in (
        artifact.get("source_notes"),
        queue_row.get("source_notes"),
        artifact.get("source", {}).get("source_notes") if isinstance(artifact.get("source"), dict) else None,
    ):
        if candidate is None:
            continue
        if not isinstance(candidate, list) or not candidate:
            raise ValueError("source_notes provenance must be a non-empty list when present.")
        retained: list[dict[str, Any]] = []
        for item in candidate:
            if not isinstance(item, dict):
                raise ValueError("each source note must be a JSON object.")
            note_id = str(item.get("note_id") or "").strip()
            if not note_id:
                raise ValueError("each source note must include note_id.")
            retained.append(deepcopy(item))
        return retained
    return None


def _validate_base_strategy_selection(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("RISK_OVERLAY adapter requires base_strategy_selection.")
    mode = str(value.get("mode") or "").strip()
    if mode not in {"explicit_base_strategy", "near_miss_drawdown"}:
        raise ValueError(f"lossy conversion: unsupported base_strategy_selection.mode {mode or '<missing>'}.")
    for key in ("allowed_to_modify_signals", "allowed_to_modify_entries", "allowed_to_modify_exits"):
        if value.get(key) is not False:
            raise ValueError(f"lossy conversion: {key} must remain false.")


def _validate_base_strategy(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("RISK_OVERLAY adapter requires explicit base_strategy binding.")

    required_keys = ("family", "asset_class", "timeframe", "short_name", "builder", "parameters", "rules")
    for key in required_keys:
        if key not in value:
            raise ValueError(f"RISK_OVERLAY adapter requires base_strategy.{key}.")

    family = str(value.get("family") or "").strip()
    asset_class = str(value.get("asset_class") or "").strip()
    timeframe = str(value.get("timeframe") or "").strip()
    short_name = str(value.get("short_name") or "").strip()
    builder = str(value.get("builder") or "").strip()

    supported = SUPPORTED_BASE_STRATEGIES.get((family, asset_class, timeframe))
    if supported is None:
        raise ValueError(
            f"unsupported strategy family for risk_overlay_execution_adapter_v1: {family or '<missing>'}/{asset_class or '<missing>'}/{timeframe or '<missing>'}"
        )
    expected_builder = supported.get(short_name)
    if expected_builder is None:
        raise ValueError(f"lossy conversion: unsupported base strategy short_name {short_name or '<missing>'}.")
    if builder != expected_builder:
        raise ValueError(
            f"lossy conversion: base strategy short_name {short_name} requires builder {expected_builder}, got {builder or '<missing>'}."
        )

    parameters = value.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("RISK_OVERLAY adapter requires base_strategy.parameters.")
    if not isinstance(value.get("rules"), str) or not str(value.get("rules") or "").strip():
        raise ValueError("RISK_OVERLAY adapter requires base_strategy.rules.")


def _validate_risk_overlay(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("RISK_OVERLAY adapter requires risk_overlay.")

    position_sizing = value.get("position_sizing")
    if not isinstance(position_sizing, dict):
        raise ValueError("RISK_OVERLAY adapter requires risk_overlay.position_sizing.")
    if str(position_sizing.get("type") or "").strip() != "fixed_fractional":
        raise ValueError("RISK_OVERLAY adapter supports only risk_overlay.position_sizing.type=fixed_fractional.")
    candidates = position_sizing.get("risk_per_trade_pct_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("RISK_OVERLAY adapter requires risk_overlay.position_sizing.risk_per_trade_pct_candidates.")
    candidate_values = [_finite_float(item, field="risk_per_trade_pct_candidates item") for item in candidates]
    if any(item <= 0 for item in candidate_values):
        raise ValueError("risk_per_trade_pct_candidates values must be positive.")
    if candidate_values != sorted(candidate_values) or len(set(candidate_values)) != len(candidate_values):
        raise ValueError("risk_per_trade_pct_candidates must be strictly increasing to avoid lossy conversion.")

    circuit_breaker = value.get("portfolio_drawdown_circuit_breaker")
    if not isinstance(circuit_breaker, dict):
        raise ValueError("RISK_OVERLAY adapter requires risk_overlay.portfolio_drawdown_circuit_breaker.")
    if str(circuit_breaker.get("type") or "").strip() != "staged_derisking":
        raise ValueError("RISK_OVERLAY adapter supports only staged_derisking circuit breakers.")
    thresholds = circuit_breaker.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        raise ValueError("RISK_OVERLAY adapter requires non-empty circuit breaker thresholds.")
    previous_drawdown = -math.inf
    previous_exposure = math.inf
    for item in thresholds:
        if not isinstance(item, dict):
            raise ValueError("each circuit breaker threshold must be a JSON object.")
        drawdown_pct = _finite_float(item.get("drawdown_pct"), field="threshold.drawdown_pct")
        exposure = _finite_float(item.get("gross_exposure_multiplier"), field="threshold.gross_exposure_multiplier")
        if drawdown_pct <= previous_drawdown:
            raise ValueError("thresholds must be strictly increasing by drawdown_pct.")
        if not 0.0 <= exposure <= 1.0:
            raise ValueError("gross_exposure_multiplier must be within [0, 1].")
        if exposure > previous_exposure:
            raise ValueError("gross_exposure_multiplier must be non-increasing across thresholds.")
        previous_drawdown = drawdown_pct
        previous_exposure = exposure

    reentry_rule = circuit_breaker.get("reentry_rule")
    if not isinstance(reentry_rule, dict):
        raise ValueError("RISK_OVERLAY adapter requires circuit breaker reentry_rule.")
    if str(reentry_rule.get("type") or "").strip() != "equity_recovery":
        raise ValueError("RISK_OVERLAY adapter supports only equity_recovery reentry_rule.")
    if _finite_float(reentry_rule.get("recovery_from_peak_pct"), field="reentry_rule.recovery_from_peak_pct") < 0:
        raise ValueError("reentry_rule.recovery_from_peak_pct must be non-negative.")
    cooldown_days = reentry_rule.get("cooldown_days")
    if isinstance(cooldown_days, bool):
        raise ValueError("reentry_rule.cooldown_days must not be boolean.")
    if not isinstance(cooldown_days, int) or cooldown_days < 0:
        raise ValueError("reentry_rule.cooldown_days must be a non-negative integer.")

    loser_addition_rule = value.get("loser_addition_rule")
    if not isinstance(loser_addition_rule, dict) or not isinstance(loser_addition_rule.get("add_to_losers_allowed"), bool):
        raise ValueError("RISK_OVERLAY adapter requires loser_addition_rule.add_to_losers_allowed boolean.")


def _validate_validation_plan(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("RISK_OVERLAY adapter requires validation_plan.")
    list_keys = ("primary_metrics", "secondary_metrics", "required_gates")
    for key in list_keys:
        items = value.get(key)
        if not isinstance(items, list) or not items or not all(str(item).strip() for item in items):
            raise ValueError(f"RISK_OVERLAY adapter requires validation_plan.{key}.")
    comparison = str(value.get("comparison") or "").strip()
    if not comparison:
        raise ValueError("RISK_OVERLAY adapter requires validation_plan.comparison.")


def _execution_spec(queue_row: dict[str, Any]) -> dict[str, Any]:
    base_strategy = deepcopy(queue_row["base_strategy"])
    return {
        "family": str(base_strategy["family"]),
        "asset_class": str(base_strategy["asset_class"]),
        "timeframe": str(base_strategy["timeframe"]),
        "short_name": f"{base_strategy['short_name']}_RISK_OVERLAY_V1",
        "hypothesis": f"{queue_row.get('title', 'Risk overlay execution spec')}: {str(queue_row.get('rationale') or '').strip()}",
        "rules": str(base_strategy["rules"]),
        "builder": ADAPTER_VERSION,
        "parameters": {
            "base_strategy": base_strategy,
            "base_strategy_selection": deepcopy(queue_row["base_strategy_selection"]),
            "risk_overlay": deepcopy(queue_row["risk_overlay"]),
            "validation_plan": deepcopy(queue_row["validation_plan"]),
            "source_hypothesis_id": str(queue_row.get("hypothesis_id") or "").strip(),
            "source_title": str(queue_row.get("source_title") or "").strip(),
            "source_note_ids": _source_note_ids(queue_row),
            "target_failure_mode": str(queue_row.get("target_failure_mode") or "").strip(),
            "requires_human_review": True,
            "source_runtime_supported": False,
            "appendable_to_registry": False,
        },
    }


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must not be boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
