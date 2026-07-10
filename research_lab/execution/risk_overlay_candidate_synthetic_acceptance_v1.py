from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
)
from research_lab.execution.risk_overlay_isolated_executor_v1 import (
    run_isolated_risk_overlay_execution,
)


REQUEST_VERSION = "risk_overlay_candidate_synthetic_acceptance_request_v1"
RESULT_VERSION = "risk_overlay_candidate_synthetic_acceptance_result_v1"
BRIDGE_VERSION = "risk_overlay_candidate_synthetic_acceptance_v1"
RUNTIME_CONTRACT_VERSION = "risk_execution_contract_v1"
EXECUTOR_REQUEST_VERSION = "risk_overlay_isolated_execution_request_v1"
OUTPUT_MODE = "full_result"
EXPECTED_CANDIDATE_VERSION = "risk_overlay_execution_spec_artifact_v1"
EXPECTED_ADAPTER_VERSION = "risk_overlay_execution_adapter_v1"
EXPECTED_CANDIDATE_BUILDER = "risk_overlay_execution_adapter_v1"
EXPECTED_BLOCKER = "drawdown_fail"
EXPECTED_OVERLAY_TYPE = "fixed_fractional"
SYNTHETIC_STRATEGY_IDENTITY = "RISK_OVERLAY_CANDIDATE_SYNTHETIC_ACCEPTANCE_V1"


def run_candidate_synthetic_acceptance(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    acceptance_input_sha256 = _canonical_sha256(validated)
    executor_request = _build_executor_request(validated)
    executor_request_sha256 = _canonical_sha256(executor_request)
    executor_result = run_isolated_risk_overlay_execution(executor_request)

    return {
        "version": RESULT_VERSION,
        "acceptance_bridge_version": BRIDGE_VERSION,
        "execution_status": "completed",
        "failure_reason": None,
        "synthetic_data_used": True,
        "real_data_used": False,
        "registry_write_performed": False,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "provider_calls_used": 0,
        "broker_actions_used": 0,
        "hermes_write_performed": False,
        "backtest_run_performed": False,
        "candidate_summary": validated["candidate_summary"],
        "acceptance_input_sha256": acceptance_input_sha256,
        "executor_request_sha256": executor_request_sha256,
        "executor_request": executor_request,
        "executor_result": executor_result,
        "acceptance_metrics": _acceptance_metrics(executor_result),
        "provenance": {
            **validated["provenance"],
            "acceptance_bridge_version": BRIDGE_VERSION,
            "candidate_schema_mode": "adapter_artifact_v1",
            "synthetic_scenario_symbol_policy": "must_start_with_SYNTH",
            "executor_cap_mapping": "initial_equity_times_cap_fraction",
            "risk_selection_policy": "single_reviewed_candidate_required",
        },
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "candidate", "synthetic_scenario", "executor_config", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    candidate, candidate_summary = _validate_candidate(payload.get("candidate"))
    synthetic_scenario = _validate_synthetic_scenario(payload.get("synthetic_scenario"), candidate_summary=candidate_summary)
    executor_config = _validate_executor_config(payload.get("executor_config"), initial_equity=synthetic_scenario["initial_equity"])
    provenance = _validate_provenance(payload.get("provenance"))
    return {
        "version": version,
        "candidate": candidate,
        "candidate_summary": candidate_summary,
        "synthetic_scenario": synthetic_scenario,
        "executor_config": executor_config,
        "provenance": provenance,
    }


def _validate_candidate(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = _required_mapping(value, name="candidate")
    _reject_unknown_fields(
        candidate,
        allowed={
            "version",
            "adapter_version",
            "execution_spec_supported",
            "appendable_to_registry",
            "requires_human_review",
            "source_runtime_supported",
            "provenance",
            "execution_spec",
        },
        name="candidate",
    )
    version = _required_text(candidate, "version")
    if version != EXPECTED_CANDIDATE_VERSION:
        raise ValueError(f"candidate.version must be {EXPECTED_CANDIDATE_VERSION}.")
    adapter_version = _required_text(candidate, "adapter_version")
    if adapter_version != EXPECTED_ADAPTER_VERSION:
        raise ValueError(f"candidate.adapter_version must be {EXPECTED_ADAPTER_VERSION}.")
    if candidate.get("execution_spec_supported") is not True:
        raise ValueError("candidate.execution_spec_supported must be true.")
    if candidate.get("appendable_to_registry") is not False:
        raise ValueError("candidate.appendable_to_registry must be false.")
    if candidate.get("requires_human_review") is not True:
        raise ValueError("candidate.requires_human_review must be true.")
    if candidate.get("source_runtime_supported") is not False:
        raise ValueError("candidate.source_runtime_supported must be false.")

    candidate_provenance = _required_mapping(candidate.get("provenance"), name="candidate.provenance")
    _reject_unknown_fields(
        candidate_provenance,
        allowed={
            "blocker",
            "source_note_ids",
            "source_artifact_type",
            "source_artifact_version",
            "source_artifact_sha256",
            "source_artifact_path",
            "candidate_artifact_hash",
            "source_notes",
        },
        name="candidate.provenance",
    )
    blocker = _canonical_blocker(candidate_provenance.get("blocker"))

    execution_spec = _required_mapping(candidate.get("execution_spec"), name="candidate.execution_spec")
    _reject_unknown_fields(
        execution_spec,
        allowed={"family", "asset_class", "timeframe", "short_name", "hypothesis", "rules", "builder", "parameters"},
        name="candidate.execution_spec",
    )
    if _required_text(execution_spec, "builder") != EXPECTED_CANDIDATE_BUILDER:
        raise ValueError(f"candidate.execution_spec.builder must be {EXPECTED_CANDIDATE_BUILDER}.")

    parameters = _required_mapping(execution_spec.get("parameters"), name="candidate.execution_spec.parameters")
    _reject_unknown_fields(
        parameters,
        allowed={
            "base_strategy",
            "base_strategy_selection",
            "risk_overlay",
            "validation_plan",
            "source_hypothesis_id",
            "source_title",
            "source_note_ids",
            "target_failure_mode",
            "requires_human_review",
            "source_runtime_supported",
            "appendable_to_registry",
        },
        name="candidate.execution_spec.parameters",
    )
    if _canonical_blocker(
        parameters.get("target_failure_mode"),
        field_name="candidate.execution_spec.parameters.target_failure_mode",
    ) != EXPECTED_BLOCKER:
        raise ValueError(f"candidate.execution_spec.parameters.target_failure_mode must canonicalize to {EXPECTED_BLOCKER}.")
    if parameters.get("requires_human_review") is not True:
        raise ValueError("candidate.execution_spec.parameters.requires_human_review must be true.")
    if parameters.get("source_runtime_supported") is not False:
        raise ValueError("candidate.execution_spec.parameters.source_runtime_supported must be false.")
    if parameters.get("appendable_to_registry") is not False:
        raise ValueError("candidate.execution_spec.parameters.appendable_to_registry must be false.")

    risk_overlay = _required_mapping(parameters.get("risk_overlay"), name="candidate.execution_spec.parameters.risk_overlay")
    _reject_unknown_fields(
        risk_overlay,
        allowed={"position_sizing", "portfolio_drawdown_circuit_breaker", "loser_addition_rule"},
        name="candidate.execution_spec.parameters.risk_overlay",
    )
    position_sizing = _required_mapping(risk_overlay.get("position_sizing"), name="candidate risk overlay position sizing")
    _reject_unknown_fields(
        position_sizing,
        allowed={"type", "risk_per_trade_pct_candidates"},
        name="candidate risk overlay position sizing",
    )
    overlay_type = _required_text(position_sizing, "type")
    if overlay_type != EXPECTED_OVERLAY_TYPE:
        raise ValueError("candidate risk overlay position sizing must be fixed_fractional.")
    risk_candidates = position_sizing.get("risk_per_trade_pct_candidates")
    if not isinstance(risk_candidates, list) or len(risk_candidates) != 1:
        raise ValueError("risk_per_trade_pct_candidates must contain exactly one reviewed value.")
    normalized_risk_candidates = [
        _required_finite_number({"value": item}, "value", strictly_positive=True) for item in risk_candidates
    ]

    circuit_breaker = _required_mapping(
        risk_overlay.get("portfolio_drawdown_circuit_breaker"),
        name="candidate circuit breaker",
    )
    _reject_unknown_fields(
        circuit_breaker,
        allowed={"type", "thresholds", "reentry_rule"},
        name="candidate circuit breaker",
    )
    thresholds = _validate_thresholds(circuit_breaker.get("thresholds"))
    reentry_rule = _validate_reentry_rule(circuit_breaker.get("reentry_rule"))
    source_candidate_id = _required_text(parameters, "source_hypothesis_id")

    normalized_candidate = {
        "version": version,
        "selected_risk_per_trade_pct": normalized_risk_candidates[0],
        "source_candidate_id": source_candidate_id,
        "blocker": blocker,
        "overlay_type": overlay_type,
        "circuit_breaker_thresholds": thresholds,
        "reentry_rule": reentry_rule,
    }
    summary = {
        "candidate_version": version,
        "candidate_builder": execution_spec["builder"],
        "source_candidate_id": source_candidate_id,
        "blocker": blocker,
        "overlay_type": overlay_type,
        "selected_risk_per_trade_pct": normalized_candidate["selected_risk_per_trade_pct"],
        "risk_selection_policy": "single_reviewed_candidate_required",
        "circuit_breaker_thresholds": thresholds,
        "reentry_rule": reentry_rule,
    }
    return normalized_candidate, summary


def _validate_synthetic_scenario(value: Any, *, candidate_summary: dict[str, Any]) -> dict[str, Any]:
    scenario = _required_mapping(value, name="synthetic_scenario")
    _reject_unknown_fields(
        scenario,
        allowed={"symbol", "initial_equity", "price_series", "events"},
        name="synthetic_scenario",
    )
    symbol = _required_text(scenario, "symbol").upper()
    if not symbol.startswith("SYNTH"):
        raise ValueError("synthetic_scenario.symbol must be synthetic and start with SYNTH.")
    initial_equity = _required_finite_number(scenario, "initial_equity", strictly_positive=True)
    price_series = _validate_price_series(scenario.get("price_series"))
    prices_by_timestamp = {item["timestamp"]: item["price"] for item in price_series}
    events = _validate_events(
        scenario.get("events"),
        symbol=symbol,
        prices_by_timestamp=prices_by_timestamp,
        source_candidate_id=candidate_summary["source_candidate_id"],
    )
    return {
        "symbol": symbol,
        "initial_equity": initial_equity,
        "price_series": price_series,
        "events": events,
    }


def _validate_executor_config(value: Any, *, initial_equity: float) -> dict[str, Any]:
    config = {} if value is None else _required_mapping(value, name="executor_config")
    _reject_unknown_fields(
        config,
        allowed={
            "runtime_contract_version",
            "fractional_units_allowed",
            "strategy_position_cap_fraction",
            "portfolio_exposure_cap_fraction",
            "output_mode",
        },
        name="executor_config",
    )
    runtime_contract_version = str(config.get("runtime_contract_version") or RUNTIME_CONTRACT_VERSION).strip()
    if runtime_contract_version != RUNTIME_CONTRACT_VERSION:
        raise ValueError(f"executor_config.runtime_contract_version must be {RUNTIME_CONTRACT_VERSION}.")
    fractional_units_allowed = config.get("fractional_units_allowed", False)
    if not isinstance(fractional_units_allowed, bool):
        raise ValueError("executor_config.fractional_units_allowed must be boolean.")
    output_mode = str(config.get("output_mode") or OUTPUT_MODE).strip()
    if output_mode != OUTPUT_MODE:
        raise ValueError("executor_config.output_mode must be full_result.")

    strategy_position_cap_fraction = _required_finite_number(
        config,
        "strategy_position_cap_fraction",
        strictly_positive=True,
    )
    portfolio_exposure_cap_fraction = _required_finite_number(
        config,
        "portfolio_exposure_cap_fraction",
        strictly_positive=True,
    )
    if strategy_position_cap_fraction > 1.0:
        raise ValueError("strategy_position_cap_fraction must be within (0, 1].")
    if portfolio_exposure_cap_fraction > 1.0:
        raise ValueError("portfolio_exposure_cap_fraction must be within (0, 1].")
    return {
        "runtime_contract_version": runtime_contract_version,
        "fractional_units_allowed": fractional_units_allowed,
        "strategy_position_cap_fraction": strategy_position_cap_fraction,
        "portfolio_exposure_cap_fraction": portfolio_exposure_cap_fraction,
        "strategy_position_cap_notional": initial_equity * strategy_position_cap_fraction,
        "portfolio_exposure_cap_notional": initial_equity * portfolio_exposure_cap_fraction,
        "output_mode": output_mode,
    }


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


def _validate_price_series(value: Any) -> list[dict[str, Any]]:
    price_series = _required_list(value, name="price_series")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for item in price_series:
        payload = _required_mapping(item, name="price series point")
        _reject_unknown_fields(payload, allowed={"timestamp", "price"}, name="price series point")
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("price_series timestamps must be strictly ordered.")
        normalized.append(
            {
                "timestamp": timestamp,
                "price": _required_finite_number(payload, "price", strictly_positive=True),
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_events(
    value: Any,
    *,
    symbol: str,
    prices_by_timestamp: dict[str, float],
    source_candidate_id: str,
) -> list[dict[str, Any]]:
    events = _required_list(value, name="events")
    normalized: list[dict[str, Any]] = []
    seen_timestamps: set[str] = set()
    seen_event_ids: set[str] = set()
    previous_timestamp: str | None = None
    for item in events:
        payload = _required_mapping(item, name="event")
        _reject_unknown_fields(
            payload,
            allowed={"timestamp", "event_id", "event_type", "direction", "protective_exit"},
            name="event",
        )
        timestamp = _required_text(payload, "timestamp")
        if timestamp in seen_timestamps:
            raise ValueError("at most one event may exist at the same timestamp.")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("events timestamps must be ordered.")
        if timestamp not in prices_by_timestamp:
            raise ValueError(f"event timestamp {timestamp} is not present in price_series.")
        event_id = _required_text(payload, "event_id")
        if event_id in seen_event_ids:
            raise ValueError("duplicate event_id is not allowed.")
        event_type = _required_text(payload, "event_type")
        if event_type not in {"entry", "exit", "rebalance"}:
            raise ValueError("event_type must be entry, exit, or rebalance.")
        direction = _required_text(payload, "direction")
        if direction != "long":
            raise ValueError("direction must be long.")
        protective_exit = _validate_protective_exit(
            payload.get("protective_exit"),
            event_type=event_type,
            entry_price=prices_by_timestamp[timestamp],
            source_candidate_id=source_candidate_id,
        )
        normalized.append(
            {
                "timestamp": timestamp,
                "event_id": event_id,
                "event_type": event_type,
                "direction": direction,
                "entry_price": prices_by_timestamp[timestamp],
                "protective_exit": protective_exit,
                "symbol": symbol,
            }
        )
        seen_timestamps.add(timestamp)
        seen_event_ids.add(event_id)
        previous_timestamp = timestamp
    return normalized


def _validate_protective_exit(
    value: Any,
    *,
    event_type: str,
    entry_price: float,
    source_candidate_id: str,
) -> dict[str, Any] | None:
    if value is None:
        if event_type == "entry":
            raise ValueError("protective_exit is required for entry.")
        return None
    if event_type == "exit":
        raise ValueError("exit events must not provide protective exits.")
    payload = _required_mapping(value, name="protective_exit")
    _reject_unknown_fields(payload, allowed={"type", "stop_price"}, name="protective_exit")
    stop_type = _required_text(payload, "type")
    if stop_type != "fixed_stop":
        raise ValueError("protective_exit.type must be fixed_stop.")
    stop_price = _required_finite_number(payload, "stop_price", strictly_positive=True)
    if stop_price >= entry_price:
        raise ValueError("protective_exit.stop_price must be below entry price for long.")
    return build_protective_exit_contract(
        {
            "entry_price": entry_price,
            "protective_exit_price": stop_price,
            "per_unit_loss_to_protective_exit": entry_price - stop_price,
            "protective_exit_type": "price_stop",
            "strategy_provenance": source_candidate_id,
        }
    )


def _validate_thresholds(value: Any) -> list[dict[str, float]]:
    thresholds = _required_list(value, name="thresholds")
    normalized: list[dict[str, float]] = []
    previous_drawdown = -math.inf
    previous_multiplier = math.inf
    for item in thresholds:
        payload = _required_mapping(item, name="threshold")
        _reject_unknown_fields(
            payload,
            allowed={"drawdown_pct", "gross_exposure_multiplier"},
            name="threshold",
        )
        drawdown_pct = _required_finite_number(payload, "drawdown_pct", strictly_positive=True)
        gross_exposure_multiplier = _required_finite_number(
            payload,
            "gross_exposure_multiplier",
            strictly_positive=False,
        )
        if not 0.0 <= gross_exposure_multiplier <= 1.0:
            raise ValueError("gross_exposure_multiplier must be within [0, 1].")
        if drawdown_pct <= previous_drawdown:
            raise ValueError("thresholds must be strictly increasing by drawdown_pct.")
        if gross_exposure_multiplier > previous_multiplier:
            raise ValueError("gross_exposure_multiplier must be non-increasing across thresholds.")
        normalized.append(
            {
                "drawdown_pct": drawdown_pct,
                "gross_exposure_multiplier": gross_exposure_multiplier,
            }
        )
        previous_drawdown = drawdown_pct
        previous_multiplier = gross_exposure_multiplier
    return normalized


def _validate_reentry_rule(value: Any) -> dict[str, Any]:
    reentry_rule = _required_mapping(value, name="reentry_rule")
    _reject_unknown_fields(
        reentry_rule,
        allowed={"type", "recovery_from_peak_pct", "cooldown_days"},
        name="reentry_rule",
    )
    rule_type = _required_text(reentry_rule, "type")
    if rule_type != "equity_recovery":
        raise ValueError("reentry_rule.type must be equity_recovery.")
    cooldown_days = reentry_rule.get("cooldown_days")
    if isinstance(cooldown_days, bool) or not isinstance(cooldown_days, int) or cooldown_days < 0:
        raise ValueError("cooldown_days must be a non-negative integer.")
    return {
        "type": rule_type,
        "recovery_from_peak_pct": _required_finite_number(
            reentry_rule,
            "recovery_from_peak_pct",
            strictly_positive=False,
        ),
        "cooldown_days": cooldown_days,
    }


def _build_executor_request(validated: dict[str, Any]) -> dict[str, Any]:
    scenario = validated["synthetic_scenario"]
    candidate = validated["candidate"]
    executor_config = validated["executor_config"]
    return {
        "version": EXECUTOR_REQUEST_VERSION,
        "runtime_contract_version": executor_config["runtime_contract_version"],
        "symbol": scenario["symbol"],
        "initial_equity": scenario["initial_equity"],
        "synthetic_price_series": [
            {
                "timestamp": item["timestamp"],
                "symbol": scenario["symbol"],
                "price": item["price"],
            }
            for item in scenario["price_series"]
        ],
        "strategy_events": [
            {
                "timestamp": item["timestamp"],
                "event_type": item["event_type"],
                "symbol": scenario["symbol"],
                "target_direction": "flat" if item["event_type"] == "exit" else "long",
                "strategy_identity": SYNTHETIC_STRATEGY_IDENTITY,
                "event_id": item["event_id"],
                "reason_code": f"synthetic_acceptance_{item['event_type']}",
            }
            for item in scenario["events"]
        ],
        "protective_exits_by_event_id": {
            item["event_id"]: item["protective_exit"]
            for item in scenario["events"]
            if item["protective_exit"] is not None
        },
        "fixed_fractional_config": {
            "selected_risk_per_trade_pct": candidate["selected_risk_per_trade_pct"],
        },
        "strategy_position_cap": executor_config["strategy_position_cap_notional"],
        "portfolio_exposure_cap": executor_config["portfolio_exposure_cap_notional"],
        "circuit_breaker_thresholds": candidate["circuit_breaker_thresholds"],
        "reentry_rule": candidate["reentry_rule"],
        "fractional_units_allowed": executor_config["fractional_units_allowed"],
        "output_mode": executor_config["output_mode"],
        "provenance": {
            "source_candidate_id": candidate["source_candidate_id"],
            "acceptance_bridge_version": BRIDGE_VERSION,
            "candidate_version": candidate["version"],
            "risk_selection_policy": "single_reviewed_candidate_required",
        },
    }


def _acceptance_metrics(executor_result: dict[str, Any]) -> dict[str, Any]:
    metrics = _required_mapping(executor_result.get("metrics"), name="executor_result.metrics")
    return {
        "final_equity": metrics["final_equity"],
        "total_return": metrics["total_return"],
        "max_drawdown": metrics["max_drawdown"],
        "trade_count": metrics["trade_count"],
        "entry_count": metrics["entry_count"],
        "exit_count": metrics["exit_count"],
        "rebalance_count": metrics["rebalance_count"],
        "circuit_breaker_activation_count": metrics["circuit_breaker_activation_count"],
        "derisking_action_count": metrics["derisking_action_count"],
    }


def _canonical_blocker(value: Any, *, field_name: str = "blocker") -> str:
    blocker = str(value or "").strip().lower()
    if blocker != EXPECTED_BLOCKER:
        raise ValueError(f"{field_name} must canonicalize to {EXPECTED_BLOCKER}.")
    return blocker


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
