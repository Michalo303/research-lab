from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
)


REQUEST_VERSION = "multi_strategy_signal_aggregation_request_v1"
RESULT_VERSION = "multi_strategy_signal_aggregation_contract_v1"
CONFLICT_POLICIES = {
    "UNANIMOUS",
    "MAJORITY",
    "PRIORITY_WEIGHTED",
    "SCORE_WEIGHTED",
    "RISK_FIRST_VETO",
}
_ACTIONS = {"ENTRY", "EXIT", "REBALANCE"}
_TARGET_INTENTS = {"LONG", "FLAT"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FLOAT_TOLERANCE = 1e-9


def build_multi_strategy_signal_aggregation_contract(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate review-only strategy signals without creating execution authority."""
    _require_mapping(request, "aggregation request")
    _reject_unknown(
        request,
        {
            "version",
            "as_of_timestamp",
            "maximum_signal_age_seconds",
            "conflict_policy",
            "priority_weights",
            "allow_short",
            "signals",
            "provenance",
        },
        "aggregation request",
    )
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    as_of_timestamp = _timestamp(request.get("as_of_timestamp"), "as_of_timestamp")
    maximum_age = _non_negative_integer(
        request.get("maximum_signal_age_seconds"), "maximum_signal_age_seconds"
    )
    policy = _required_text(request.get("conflict_policy"), "conflict_policy")
    if policy not in CONFLICT_POLICIES:
        raise ValueError("conflict_policy is unsupported.")
    if request.get("allow_short") is not False:
        raise ValueError("short exposure is not supported; allow_short must be false.")
    provenance = _json_mapping(request.get("provenance"), "provenance")
    priority_weights = _priority_weights(request.get("priority_weights"))
    raw_signals = request.get("signals")
    if not isinstance(raw_signals, list) or not raw_signals:
        raise ValueError("signals must be a non-empty list.")

    normalized = [
        _signal(signal, as_of_timestamp=as_of_timestamp) for signal in raw_signals
    ]
    normalized.sort(key=_signal_sort_key)
    if policy == "PRIORITY_WEIGHTED":
        strategy_ids = {signal["strategy_id"] for signal in normalized}
        missing = sorted(strategy_ids - priority_weights.keys())
        if missing:
            raise ValueError(f"priority weight is required for: {', '.join(missing)}.")
    if policy == "SCORE_WEIGHTED" and any(
        signal["score"] is None for signal in normalized
    ):
        raise ValueError("score is required for SCORE_WEIGHTED.")

    duplicate_counts = Counter(_duplicate_key(signal) for signal in normalized)
    stale: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for signal in normalized:
        duplicate_key = _duplicate_key(signal)
        if duplicate_counts[duplicate_key] > 1:
            rejected = _public_signal(signal)
            rejected["duplicate_identity_sha256"] = _canonical_sha256(duplicate_key)
            duplicates.append(rejected)
            continue
        age_seconds = int((as_of_timestamp - signal["_decision_datetime"]).total_seconds())
        if age_seconds > maximum_age:
            rejected = _public_signal(signal)
            rejected["signal_age_seconds"] = age_seconds
            stale.append(rejected)
            continue
        accepted.append(_public_signal(signal))

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for signal in accepted:
        grouped.setdefault(
            (signal["symbol"], signal["decision_timestamp"]), []
        ).append(signal)

    aggregates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    for (symbol, decision_timestamp), signals in sorted(grouped.items()):
        aggregate, conflict = _aggregate_group(
            symbol=symbol,
            decision_timestamp=decision_timestamp,
            signals=signals,
            policy=policy,
            priority_weights=priority_weights,
        )
        aggregates.append(aggregate)
        resolutions.append(
            {
                "symbol": symbol,
                "decision_timestamp": decision_timestamp,
                "target_intent": aggregate["target_intent"],
                "resolution": aggregate["resolution"],
                "tie_resolved_to_flat": aggregate["tie_resolved_to_flat"],
            }
        )
        if conflict is not None:
            conflicts.append(conflict)

    canonical_request = {
        "version": REQUEST_VERSION,
        "as_of_timestamp": _format_timestamp(as_of_timestamp),
        "maximum_signal_age_seconds": maximum_age,
        "conflict_policy": policy,
        "priority_weights": priority_weights,
        "allow_short": False,
        "signals": [_public_signal(signal) for signal in normalized],
        "provenance": provenance,
    }
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(canonical_request),
        "as_of_timestamp": canonical_request["as_of_timestamp"],
        "conflict_policy": policy,
        "aggregated_target_intents": aggregates,
        "accepted_signals": accepted,
        "rejected_stale_signals": stale,
        "rejected_duplicates": duplicates,
        "conflicts": conflicts,
        "conflict_resolution": resolutions,
        "accepted_signals_sha256": _canonical_sha256(accepted),
        "aggregation_sha256": _canonical_sha256(aggregates),
        "provenance": provenance,
        "broker_orders_emitted": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def _aggregate_group(
    *,
    symbol: str,
    decision_timestamp: str,
    signals: list[dict[str, Any]],
    policy: str,
    priority_weights: dict[str, float],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ordered = sorted(signals, key=_signal_sort_key)
    intents = {signal["target_intent"] for signal in ordered}
    long_signals = [signal for signal in ordered if signal["target_intent"] == "LONG"]
    flat_signals = [signal for signal in ordered if signal["target_intent"] == "FLAT"]
    tie = False
    if policy == "UNANIMOUS":
        if len(intents) == 1:
            target = next(iter(intents))
            resolution = f"UNANIMOUS_{target}"
        else:
            target = "FLAT"
            resolution = "UNANIMOUS_CONFLICT_FLAT"
    elif policy == "MAJORITY":
        target, tie = _weighted_target(float(len(long_signals)), float(len(flat_signals)))
        resolution = "MAJORITY_TIE_FLAT" if tie else f"MAJORITY_{target}"
    elif policy == "PRIORITY_WEIGHTED":
        long_weight = sum(priority_weights[signal["strategy_id"]] for signal in long_signals)
        flat_weight = sum(priority_weights[signal["strategy_id"]] for signal in flat_signals)
        target, tie = _weighted_target(long_weight, flat_weight)
        resolution = "PRIORITY_WEIGHTED_TIE_FLAT" if tie else f"PRIORITY_WEIGHTED_{target}"
    elif policy == "SCORE_WEIGHTED":
        long_weight = sum(float(signal["score"]) for signal in long_signals)
        flat_weight = sum(float(signal["score"]) for signal in flat_signals)
        target, tie = _weighted_target(long_weight, flat_weight)
        resolution = "SCORE_WEIGHTED_TIE_FLAT" if tie else f"SCORE_WEIGHTED_{target}"
    else:
        target = "FLAT" if flat_signals else "LONG"
        resolution = f"RISK_FIRST_VETO_{target}"

    contributing = [
        {
            "strategy_id": signal["strategy_id"],
            "strategy_version": signal["strategy_version"],
            "strategy_builder": signal["strategy_builder"],
            "variant_id": signal["variant_id"],
            "target_intent": signal["target_intent"],
            "source_input_sha256": signal["source_input_sha256"],
        }
        for signal in ordered
    ]
    protective_exits = [
        {
            "strategy_id": signal["strategy_id"],
            "variant_id": signal["variant_id"],
            "protective_exit": signal["protective_exit"],
        }
        for signal in long_signals
    ]
    risk_lineage = [
        {
            "strategy_id": signal["strategy_id"],
            "variant_id": signal["variant_id"],
            "per_unit_loss": signal["per_unit_loss"],
            "source_input_sha256": signal["source_input_sha256"],
        }
        for signal in long_signals
    ]
    aggregate = {
        "symbol": symbol,
        "decision_timestamp": decision_timestamp,
        "target_intent": target,
        "resolution": resolution,
        "tie_resolved_to_flat": tie,
        "contributing_strategies": contributing,
        "protective_exits": protective_exits,
        "per_unit_risk_lineage": risk_lineage,
    }
    aggregate["target_intent_sha256"] = _canonical_sha256(aggregate)
    conflict = None
    if len(intents) > 1:
        conflict = {
            "symbol": symbol,
            "decision_timestamp": decision_timestamp,
            "policy": policy,
            "long_strategy_ids": [signal["strategy_id"] for signal in long_signals],
            "flat_strategy_ids": [signal["strategy_id"] for signal in flat_signals],
            "resolved_target_intent": target,
            "resolution": resolution,
        }
    return aggregate, conflict


def _signal(raw: Any, *, as_of_timestamp: datetime) -> dict[str, Any]:
    _require_mapping(raw, "signal")
    _reject_unknown(
        raw,
        {
            "strategy_id",
            "strategy_version",
            "strategy_builder",
            "variant_id",
            "symbol",
            "signal_timestamp",
            "decision_timestamp",
            "action",
            "target_intent",
            "confidence",
            "score",
            "protective_exit",
            "per_unit_loss",
            "source_input_sha256",
            "provenance",
        },
        "signal",
    )
    signal_timestamp = _timestamp(raw.get("signal_timestamp"), "signal_timestamp")
    decision_timestamp = _timestamp(raw.get("decision_timestamp"), "decision_timestamp")
    if signal_timestamp > decision_timestamp:
        raise ValueError("signal_timestamp must not be after decision_timestamp.")
    if decision_timestamp > as_of_timestamp:
        raise ValueError("decision_timestamp must not be in the future.")
    action = _required_text(raw.get("action"), "action")
    if action not in _ACTIONS:
        raise ValueError("action is unsupported.")
    intent = _required_text(raw.get("target_intent"), "target_intent")
    if intent not in _TARGET_INTENTS:
        raise ValueError("target_intent must be LONG or FLAT; shorting is unsupported.")
    if intent == "LONG" and action == "EXIT":
        raise ValueError("LONG target_intent cannot use EXIT action.")
    if intent == "FLAT" and action != "EXIT":
        raise ValueError("FLAT target_intent must use EXIT action.")
    confidence = _optional_bounded_number(raw.get("confidence"), "confidence")
    score = _optional_bounded_number(raw.get("score"), "score")
    if intent == "LONG":
        protective_exit = build_protective_exit_contract(
            _require_mapping(raw.get("protective_exit"), "protective_exit")
        )
        per_unit_loss = _positive_number(raw.get("per_unit_loss"), "per_unit_loss")
        expected_loss = protective_exit["per_unit_loss_to_protective_exit"]
        if not math.isclose(
            per_unit_loss, expected_loss, rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE
        ):
            raise ValueError("per_unit_loss must exactly match protective_exit risk.")
    else:
        if raw.get("protective_exit") is not None:
            raise ValueError("FLAT target_intent protective_exit must be null.")
        if raw.get("per_unit_loss") is not None:
            raise ValueError("FLAT target_intent per_unit_loss must be null.")
        protective_exit = None
        per_unit_loss = None
    source_hash = _required_text(raw.get("source_input_sha256"), "source_input_sha256")
    if not _SHA256_RE.fullmatch(source_hash):
        raise ValueError("source_input_sha256 must be a lowercase SHA-256.")
    symbol = _required_text(raw.get("symbol"), "symbol").upper()
    normalized = {
        "strategy_id": _required_text(raw.get("strategy_id"), "strategy_id"),
        "strategy_version": _required_text(raw.get("strategy_version"), "strategy_version"),
        "strategy_builder": _required_text(raw.get("strategy_builder"), "strategy_builder"),
        "variant_id": _required_text(raw.get("variant_id"), "variant_id"),
        "symbol": symbol,
        "signal_timestamp": _format_timestamp(signal_timestamp),
        "decision_timestamp": _format_timestamp(decision_timestamp),
        "action": action,
        "target_intent": intent,
        "confidence": confidence,
        "score": score,
        "protective_exit": protective_exit,
        "per_unit_loss": per_unit_loss,
        "source_input_sha256": source_hash,
        "provenance": _json_mapping(raw.get("provenance"), "signal provenance"),
        "_decision_datetime": decision_timestamp,
    }
    return normalized


def _public_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in signal.items() if not key.startswith("_")}


def _duplicate_key(signal: dict[str, Any]) -> tuple[str, ...]:
    return (
        signal["strategy_id"],
        signal["strategy_version"],
        signal["variant_id"],
        signal["symbol"],
        signal["signal_timestamp"],
        signal["decision_timestamp"],
    )


def _signal_sort_key(signal: dict[str, Any]) -> tuple[str, ...]:
    return (
        signal["symbol"],
        signal["decision_timestamp"],
        signal["strategy_id"],
        signal["strategy_version"],
        signal["variant_id"],
        signal["source_input_sha256"],
        signal["target_intent"],
    )


def _weighted_target(long_weight: float, flat_weight: float) -> tuple[str, bool]:
    if math.isclose(long_weight, flat_weight, rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE):
        return "FLAT", True
    return ("LONG", False) if long_weight > flat_weight else ("FLAT", False)


def _priority_weights(raw: Any) -> dict[str, float]:
    _require_mapping(raw, "priority_weights")
    result: dict[str, float] = {}
    for strategy_id, weight in raw.items():
        key = _required_text(strategy_id, "priority weight strategy_id")
        result[key] = _positive_number(weight, f"priority weight for {key}")
    return dict(sorted(result.items()))


def _timestamp(raw: Any, name: str) -> datetime:
    text = _required_text(raw, name)
    if not text.endswith("Z"):
        raise ValueError(f"{name} must be an explicit UTC timestamp ending in Z.")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UTC timestamp.") from exc
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _optional_bounded_number(raw: Any, name: str) -> float | None:
    if raw is None:
        return None
    value = _number(raw, name)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be between 0 and 1.")
    return value


def _positive_number(raw: Any, name: str) -> float:
    value = _number(raw, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return value


def _number(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value


def _non_negative_integer(raw: Any, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return raw


def _required_text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise ValueError(f"{name} must be non-empty text without outer whitespace.")
    return raw


def _require_mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object.")
    return raw


def _json_mapping(raw: Any, name: str) -> dict[str, Any]:
    mapping = _require_mapping(raw, name)
    try:
        return json.loads(json.dumps(mapping, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be canonical JSON data.") from exc


def _reject_unknown(payload: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}.")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
