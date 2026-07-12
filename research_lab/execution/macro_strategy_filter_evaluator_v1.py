from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any


REQUEST_VERSION = "macro_strategy_filter_evaluator_request_v1"
RESULT_VERSION = "macro_strategy_filter_evaluator_result_v1"
EVALUATOR_VERSION = "macro_strategy_filter_evaluator_v1"
VARIANT_BASELINE = "BASELINE_NO_FILTER"
VARIANT_CANDIDATE = "MACRO_FILTER_CANDIDATE"
VARIANT_DISABLED = "DISABLED_FILTER_ABLATION"
VARIANT_INVERSE = "INVERSE_FILTER_ABLATION"
ACTION_ALLOW = "ALLOW_ENTRY"
ACTION_BLOCK = "BLOCK_ENTRY"
ACTION_REDUCE = "REDUCE_EXPOSURE"
ACTION_LEAVE = "LEAVE_UNCHANGED"
CLASSIFICATION_RISK = "CANDIDATE_IMPROVES_RISK"
CLASSIFICATION_RETURN = "CANDIDATE_IMPROVES_RETURN"
CLASSIFICATION_MIXED = "CANDIDATE_MIXED"
CLASSIFICATION_NO_VALUE = "CANDIDATE_NO_VALUE"
CLASSIFICATION_UNSTABLE = "CANDIDATE_UNSTABLE"
CLASSIFICATION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


def build_macro_strategy_filter_evaluator(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    baseline = _simulate_variant(
        variant_id=VARIANT_BASELINE,
        bars=validated["market_bars"],
        signals=validated["baseline_signal_sequence"],
        execution_policy=validated["execution_policy"],
        transaction_cost=validated["transaction_cost"],
        slippage_cost=validated["slippage_cost"],
        regime_action_map=None,
        regime_observations=validated["regime_observations"],
    )
    candidate = _simulate_variant(
        variant_id=VARIANT_CANDIDATE,
        bars=validated["market_bars"],
        signals=validated["baseline_signal_sequence"],
        execution_policy=validated["execution_policy"],
        transaction_cost=validated["transaction_cost"],
        slippage_cost=validated["slippage_cost"],
        regime_action_map=validated["filter_policy"]["regime_action_map"],
        regime_observations=validated["regime_observations"],
    )
    disabled = _simulate_variant(
        variant_id=VARIANT_DISABLED,
        bars=validated["market_bars"],
        signals=validated["baseline_signal_sequence"],
        execution_policy=validated["execution_policy"],
        transaction_cost=validated["transaction_cost"],
        slippage_cost=validated["slippage_cost"],
        regime_action_map={label: {"action": ACTION_LEAVE} for label in validated["regime_labels"]},
        regime_observations=validated["regime_observations"],
    )
    inverse = None
    if validated["inverse_regime_action_map"] is not None:
        inverse = _simulate_variant(
            variant_id=VARIANT_INVERSE,
            bars=validated["market_bars"],
            signals=validated["baseline_signal_sequence"],
            execution_policy=validated["execution_policy"],
            transaction_cost=validated["transaction_cost"],
            slippage_cost=validated["slippage_cost"],
            regime_action_map=validated["inverse_regime_action_map"],
            regime_observations=validated["regime_observations"],
        )

    baseline_metrics = baseline["metrics"]
    candidate_metrics = _augment_candidate_metrics(candidate["metrics"], baseline["metrics"])
    disabled_metrics = _augment_candidate_metrics(disabled["metrics"], baseline["metrics"])
    inverse_metrics = _augment_candidate_metrics(inverse["metrics"], baseline["metrics"]) if inverse is not None else None

    fold_results = _fold_results(
        candidate["daily_records"],
        validated["chronological_folds"],
    )
    fold_pass_rate = (
        sum(1 for fold in fold_results if fold["passed"]) / len(fold_results)
        if fold_results
        else 0.0
    )
    candidate_metrics["chronological_fold_results"] = fold_results
    candidate_metrics["fold_pass_rate"] = round(fold_pass_rate, 10)
    candidate_metrics["loss_periods_avoided"] = _count_period_outcomes(
        baseline["daily_records"],
        candidate["daily_records"],
        positive=False,
    )
    candidate_metrics["profitable_periods_removed"] = _count_period_outcomes(
        baseline["daily_records"],
        candidate["daily_records"],
        positive=True,
    )
    candidate_metrics["regime_transition_sensitivity"] = candidate["transition_sensitive_activations"]

    metric_deltas = _metric_deltas(candidate_metrics, baseline_metrics)
    classification, reasons, instability_reasons, insufficient_reasons = _classify(
        candidate_metrics=candidate_metrics,
        metric_deltas=metric_deltas,
        classification_policy=validated["classification_policy"],
        minimum_evidence_policy=validated["minimum_evidence_policy"],
    )

    variant_results: dict[str, Any] = {
        VARIANT_BASELINE: {
            "metrics": baseline_metrics,
            "normalized_signal_sequence": baseline["normalized_signal_sequence"],
        },
        VARIANT_CANDIDATE: {
            "metrics": candidate_metrics,
            "normalized_signal_sequence": candidate["normalized_signal_sequence"],
        },
        VARIANT_DISABLED: {
            "metrics": disabled_metrics,
            "normalized_signal_sequence": disabled["normalized_signal_sequence"],
        },
    }
    if inverse is not None and inverse_metrics is not None:
        variant_results[VARIANT_INVERSE] = {
            "metrics": inverse_metrics,
            "normalized_signal_sequence": inverse["normalized_signal_sequence"],
        }

    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluation_id": validated["evaluation_id"],
        "strategy_identity": validated["strategy_identity"],
        "baseline_variant_identity": validated["baseline_variant_identity"],
        "market_data_identity": validated["market_data_identity"],
        "macro_lineage": validated["macro_lineage"],
        "variant_results": variant_results,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "disabled_ablation_metrics": disabled_metrics,
        "inverse_ablation_metrics": inverse_metrics,
        "metric_deltas": metric_deltas,
        "chronological_fold_results": fold_results,
        "classification": classification,
        "classification_reasons": reasons,
        "instability_reasons": instability_reasons,
        "insufficient_evidence_reasons": insufficient_reasons,
        "protective_exits_preserved": baseline["protective_exits_preserved"] and candidate["protective_exits_preserved"] and disabled["protective_exits_preserved"] and (inverse is None or inverse["protective_exits_preserved"]),
        "baseline_unchanged": validated["baseline_signal_sequence_original"] == validated["baseline_signal_sequence"],
        "candidate_only": True,
        "automatic_strategy_application_performed": False,
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "promotion_performed": False,
        "generated_code_executed": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result["input_sha256"] = _canonical_sha256(validated["hashable_request"])
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _simulate_variant(
    *,
    variant_id: str,
    bars: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    execution_policy: dict[str, Any],
    transaction_cost: float,
    slippage_cost: float,
    regime_action_map: dict[str, dict[str, Any]] | None,
    regime_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_plans: list[dict[str, Any]] = []
    transition_sensitive_activations = 0
    for signal in signals:
        normalized = dict(signal)
        action_details = {"action": ACTION_LEAVE}
        if regime_action_map is not None and signal["signal_type"] in {"entry", "rebalance"}:
            regime = _regime_for_signal(signal_timestamp=signal["timestamp"], regime_observations=regime_observations)
            action_details = regime_action_map[regime["regime_label"]]
            normalized["matched_regime_label"] = regime["regime_label"]
            if regime["is_transition"]:
                transition_sensitive_activations += int(_material_filter_activation(signal, action_details))
            _apply_filter_action(normalized, action_details)
        signal_plans.append(normalized)

    pending_fills: dict[int, list[dict[str, Any]]] = {}
    for signal in signal_plans:
        signal_index = _index_for_timestamp(bars, signal["timestamp"])
        fill_index = signal_index + int(execution_policy["decision_to_fill_delay_bars"])
        if execution_policy["allow_same_bar_fill"] and int(execution_policy["decision_to_fill_delay_bars"]) == 0:
            fill_index = signal_index
        if fill_index >= len(bars):
            raise ValueError("signal fill would require future market bars.")
        pending_fills.setdefault(fill_index, []).append(signal)

    equity = float(execution_policy["initial_capital"])
    peak_equity = equity
    max_drawdown = 0.0
    current_exposure = 0.0
    trade_count = 0
    turnover = 0.0
    transaction_costs = 0.0
    slippage_costs = 0.0
    blocked_entry_count = 0
    reduced_exposure_count = 0
    filter_activation_count = 0
    daily_records: list[dict[str, Any]] = []
    exposures: list[float] = []
    normalized_signal_sequence: list[dict[str, Any]] = []

    for index, bar in enumerate(bars):
        for signal in pending_fills.get(index, []):
            new_exposure = float(signal["target_exposure"])
            if signal["signal_type"] == "exit":
                new_exposure = 0.0
            delta = abs(new_exposure - current_exposure)
            if signal.get("filter_action") == ACTION_BLOCK and signal["signal_type"] == "entry":
                blocked_entry_count += 1
                filter_activation_count += 1
            elif signal.get("filter_action") == ACTION_REDUCE and new_exposure < float(signal["baseline_target_exposure"]):
                reduced_exposure_count += 1
                filter_activation_count += 1
            if delta > 0:
                turnover += delta
                transaction_fee = equity * delta * transaction_cost
                slippage_fee = equity * delta * slippage_cost
                equity -= transaction_fee + slippage_fee
                transaction_costs += transaction_fee
                slippage_costs += slippage_fee
                current_exposure = new_exposure
                trade_count += 1
            normalized_signal_sequence.append(signal)
        if index > 0:
            previous_close = float(bars[index - 1]["close"])
            current_close = float(bar["close"])
            bar_return = (current_close / previous_close) - 1.0
            equity *= 1.0 + (current_exposure * bar_return)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, (equity / peak_equity) - 1.0)
        exposures.append(current_exposure)
        daily_records.append(
            {
                "timestamp": bar["timestamp"],
                "equity": equity,
                "exposure": current_exposure,
            }
        )

    average_exposure = sum(exposures) / len(exposures)
    time_in_market = sum(1 for item in exposures if item > 0.0) / len(exposures)
    gross_exposure = max(abs(item) for item in exposures)
    protective_preserved = all(
        signal.get("protective_exit") == next(
            original["protective_exit"]
            for original in signals
            if original["signal_id"] == signal["signal_id"]
        )
        for signal in normalized_signal_sequence
    )
    metrics = {
        "total_return": round((equity / float(execution_policy["initial_capital"])) - 1.0, 10),
        "maximum_drawdown": round(max_drawdown, 10),
        "trade_count": trade_count,
        "turnover": round(turnover, 10),
        "average_exposure": round(average_exposure, 10),
        "gross_exposure": round(gross_exposure, 10),
        "time_in_market": round(time_in_market, 10),
        "loss_periods_avoided": 0,
        "profitable_periods_removed": 0,
        "regime_transition_sensitivity": 0,
        "filter_activation_count": filter_activation_count,
        "blocked_entry_count": blocked_entry_count,
        "reduced_exposure_count": reduced_exposure_count,
        "transaction_costs": round(transaction_costs, 10),
        "slippage_costs": round(slippage_costs, 10),
        "net_performance": round((equity / float(execution_policy["initial_capital"])) - 1.0, 10),
        "chronological_fold_results": [],
        "fold_pass_rate": 0.0,
    }
    return {
        "variant_id": variant_id,
        "metrics": metrics,
        "daily_records": daily_records,
        "normalized_signal_sequence": normalized_signal_sequence,
        "protective_exits_preserved": protective_preserved,
        "transition_sensitive_activations": transition_sensitive_activations,
    }


def _regime_for_signal(*, signal_timestamp: str, regime_observations: list[dict[str, Any]]) -> dict[str, Any]:
    matched_index = -1
    for index, observation in enumerate(regime_observations):
        if observation["timestamp"] <= signal_timestamp:
            matched_index = index
        else:
            break
    if matched_index < 0:
        raise ValueError("future regime observation would be required for signal decision.")
    matched = regime_observations[matched_index]
    latest_availability = max(str(value) for value in matched["feature_availability_timestamps_utc"].values() if value is not None)
    if _parse_timestamp(latest_availability) > _decision_boundary(signal_timestamp):
        raise ValueError("regime availability must precede the decision boundary.")
    return {
        "regime_label": matched["regime_label"],
        "is_transition": matched_index > 0 and regime_observations[matched_index - 1]["regime_label"] != matched["regime_label"],
    }


def _apply_filter_action(signal: dict[str, Any], action_details: dict[str, Any]) -> None:
    signal["baseline_target_exposure"] = float(signal["target_exposure"])
    action = action_details["action"]
    signal["filter_action"] = action
    if action in {ACTION_ALLOW, ACTION_LEAVE}:
        return
    if action == ACTION_BLOCK:
        signal["target_exposure"] = 0.0
        return
    factor = float(action_details["factor"])
    signal["target_exposure"] = float(signal["target_exposure"]) * factor


def _material_filter_activation(signal: dict[str, Any], action_details: dict[str, Any]) -> bool:
    action = action_details["action"]
    if action == ACTION_BLOCK:
        return float(signal["target_exposure"]) > 0.0
    if action == ACTION_REDUCE:
        return float(action_details["factor"]) < 1.0 and float(signal["target_exposure"]) > 0.0
    return False


def _fold_results(daily_records: list[dict[str, Any]], folds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for fold in folds:
        fold_records = [
            record
            for record in daily_records
            if fold["start_timestamp"] <= record["timestamp"] <= fold["end_timestamp"]
        ]
        if not fold_records:
            raise ValueError("chronological fold produced no matching records.")
        start_equity = fold_records[0]["equity"]
        end_equity = fold_records[-1]["equity"]
        peak = start_equity
        max_drawdown = 0.0
        trade_like_records = 0
        for record in fold_records:
            peak = max(peak, record["equity"])
            max_drawdown = min(max_drawdown, (record["equity"] / peak) - 1.0)
            if record["exposure"] > 0:
                trade_like_records += 1
        total_return = (end_equity / start_equity) - 1.0 if start_equity > 0 else 0.0
        passed = (
            total_return >= fold["min_total_return"]
            and abs(max_drawdown) <= fold["max_drawdown_limit"]
            and trade_like_records >= fold["min_trade_count"]
        )
        results.append(
            {
                "fold_id": fold["fold_id"],
                "start_timestamp": fold["start_timestamp"],
                "end_timestamp": fold["end_timestamp"],
                "total_return": round(total_return, 10),
                "maximum_drawdown": round(max_drawdown, 10),
                "trade_count": trade_like_records,
                "passed": passed,
            }
        )
    return results


def _count_period_outcomes(
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    positive: bool,
) -> int:
    count = 0
    for index in range(1, min(len(baseline_records), len(candidate_records))):
        baseline = baseline_records[index]
        previous = baseline_records[index - 1]
        if previous["equity"] <= 0:
            continue
        bar_return = (baseline["equity"] / previous["equity"]) - 1.0
        candidate_exposure = candidate_records[index]["exposure"]
        baseline_exposure = baseline["exposure"]
        if candidate_exposure >= baseline_exposure:
            continue
        if positive and bar_return > 0:
            count += 1
        if not positive and bar_return < 0:
            count += 1
    return count


def _augment_candidate_metrics(candidate_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    augmented = dict(candidate_metrics)
    augmented["loss_periods_avoided"] = 0
    augmented["profitable_periods_removed"] = 0
    augmented["regime_transition_sensitivity"] = 0
    augmented["chronological_fold_results"] = []
    augmented["fold_pass_rate"] = 0.0
    return augmented


def _metric_deltas(candidate_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, float]:
    return {
        "total_return_delta": round(candidate_metrics["total_return"] - baseline_metrics["total_return"], 10),
        "maximum_drawdown_delta": round(candidate_metrics["maximum_drawdown"] - baseline_metrics["maximum_drawdown"], 10),
        "trade_count_delta": round(float(candidate_metrics["trade_count"] - baseline_metrics["trade_count"]), 10),
        "turnover_delta": round(candidate_metrics["turnover"] - baseline_metrics["turnover"], 10),
        "net_performance_delta": round(candidate_metrics["net_performance"] - baseline_metrics["net_performance"], 10),
    }


def _classify(
    *,
    candidate_metrics: dict[str, Any],
    metric_deltas: dict[str, float],
    classification_policy: dict[str, Any],
    minimum_evidence_policy: dict[str, Any],
) -> tuple[str, list[str], list[str], list[str]]:
    reasons: list[str] = []
    instability_reasons: list[str] = []
    insufficient_reasons: list[str] = []
    drawdown_improvement = abs(candidate_metrics["maximum_drawdown"]) < math.inf and (
        abs(metric_deltas["maximum_drawdown_delta"])
        if metric_deltas["maximum_drawdown_delta"] > 0
        else -metric_deltas["maximum_drawdown_delta"]
    )
    return_improvement = metric_deltas["total_return_delta"]
    if candidate_metrics["trade_count"] < minimum_evidence_policy["min_candidate_trade_count"]:
        insufficient_reasons.append("candidate_trade_count_below_minimum")
    if minimum_evidence_policy["min_regime_observations"] > 0 and candidate_metrics["trade_count"] == 0:
        insufficient_reasons.append("no_executed_candidate_trades")
    if insufficient_reasons:
        return CLASSIFICATION_INSUFFICIENT, insufficient_reasons, instability_reasons, insufficient_reasons

    if candidate_metrics["fold_pass_rate"] < max(
        minimum_evidence_policy["min_fold_pass_rate"],
        classification_policy["unstable"]["min_fold_pass_rate"],
    ):
        instability_reasons.append("fold_pass_rate_below_threshold")
        return CLASSIFICATION_UNSTABLE, instability_reasons, instability_reasons, insufficient_reasons

    if (
        drawdown_improvement >= classification_policy["mixed"]["min_drawdown_improvement"]
        and return_improvement >= classification_policy["mixed"]["min_return_improvement"]
    ):
        reasons.append("return_and_drawdown_improved")
        return CLASSIFICATION_MIXED, reasons, instability_reasons, insufficient_reasons
    if (
        drawdown_improvement >= classification_policy["risk"]["min_drawdown_improvement"]
        and return_improvement >= -classification_policy["risk"]["max_return_degradation"]
    ):
        reasons.append("drawdown_improved_within_return_tolerance")
        return CLASSIFICATION_RISK, reasons, instability_reasons, insufficient_reasons
    if (
        return_improvement >= classification_policy["return"]["min_return_improvement"]
        and -metric_deltas["maximum_drawdown_delta"] <= classification_policy["return"]["max_drawdown_degradation"]
    ):
        reasons.append("return_improved_within_drawdown_tolerance")
        return CLASSIFICATION_RETURN, reasons, instability_reasons, insufficient_reasons
    if (
        abs(metric_deltas["total_return_delta"]) <= classification_policy["no_value"]["max_abs_return_delta"]
        and abs(metric_deltas["maximum_drawdown_delta"]) <= classification_policy["no_value"]["max_abs_drawdown_delta"]
    ):
        reasons.append("deltas_within_no_value_band")
        return CLASSIFICATION_NO_VALUE, reasons, instability_reasons, insufficient_reasons
    reasons.append("defaulted_to_no_value")
    return CLASSIFICATION_NO_VALUE, reasons, instability_reasons, insufficient_reasons


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "evaluation_id",
            "strategy_identity",
            "baseline_variant_identity",
            "baseline_signal_sequence",
            "market_data_identity",
            "market_data_sha256",
            "market_bars",
            "macro_snapshot_sha256",
            "alignment_output_sha256",
            "feature_set_output_sha256",
            "macro_regime_candidate_output_sha256",
            "macro_regime_candidate_result",
            "filter_policy",
            "ablation_policy",
            "evaluation_windows",
            "chronological_folds",
            "transaction_cost_assumptions",
            "slippage_assumptions",
            "execution_policy",
            "classification_policy",
            "minimum_evidence_policy",
            "provenance",
        },
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    strategy_identity = _validate_strategy_identity(payload.get("strategy_identity"))
    market_bars = _validate_market_bars(payload.get("market_bars"))
    market_data_identity = _required_text(payload, "market_data_identity")
    market_data_sha256 = _required_text(payload, "market_data_sha256")
    if market_data_sha256 != _canonical_sha256(market_bars):
        raise ValueError("market_data_sha256 must match market bars.")
    baseline_signal_sequence = _validate_signal_sequence(
        payload.get("baseline_signal_sequence"),
        strategy_identity=strategy_identity,
        baseline_variant_identity=_required_text(payload, "baseline_variant_identity"),
        market_data_identity=market_data_identity,
        market_bars=market_bars,
    )
    baseline_signal_sequence_original = json.loads(json.dumps(baseline_signal_sequence))
    regime_candidate = _validate_regime_candidate(
        payload.get("macro_regime_candidate_result"),
        macro_regime_candidate_output_sha256=_required_text(payload, "macro_regime_candidate_output_sha256"),
        macro_snapshot_sha256=_required_text(payload, "macro_snapshot_sha256"),
        alignment_output_sha256=_required_text(payload, "alignment_output_sha256"),
        feature_set_output_sha256=_required_text(payload, "feature_set_output_sha256"),
    )
    filter_policy = _validate_filter_policy(payload.get("filter_policy"), regime_labels=regime_candidate["regime_labels"])
    ablation_policy = _validate_ablation_policy(payload.get("ablation_policy"), regime_labels=regime_candidate["regime_labels"])
    evaluation_windows = _validate_windows(payload.get("evaluation_windows"), name="evaluation_windows")
    chronological_folds = _validate_folds(payload.get("chronological_folds"), evaluation_windows=evaluation_windows)
    execution_policy = _validate_execution_policy(payload.get("execution_policy"))
    classification_policy = _validate_classification_policy(payload.get("classification_policy"))
    minimum_evidence_policy = _validate_minimum_evidence_policy(payload.get("minimum_evidence_policy"))
    transaction_cost = _required_non_negative_number(
        _required_mapping(payload.get("transaction_cost_assumptions"), name="transaction_cost_assumptions"),
        "per_unit_turnover_cost",
    )
    slippage_cost = _required_non_negative_number(
        _required_mapping(payload.get("slippage_assumptions"), name="slippage_assumptions"),
        "per_unit_turnover_slippage",
    )
    provenance = _validate_provenance(payload.get("provenance"))
    hashable_request = {
        "version": REQUEST_VERSION,
        "evaluation_id": _required_text(payload, "evaluation_id"),
        "strategy_identity": strategy_identity,
        "baseline_variant_identity": _required_text(payload, "baseline_variant_identity"),
        "baseline_signal_sequence": baseline_signal_sequence,
        "market_data_identity": market_data_identity,
        "market_data_sha256": market_data_sha256,
        "macro_lineage": regime_candidate["macro_lineage"],
        "macro_regime_candidate_output_sha256": regime_candidate["output_payload_sha256"],
        "filter_policy": filter_policy,
        "ablation_policy": ablation_policy,
        "evaluation_windows": evaluation_windows,
        "chronological_folds": chronological_folds,
        "transaction_cost": transaction_cost,
        "slippage_cost": slippage_cost,
        "execution_policy": execution_policy,
        "classification_policy": classification_policy,
        "minimum_evidence_policy": minimum_evidence_policy,
        "provenance": provenance,
    }
    return {
        "evaluation_id": _required_text(payload, "evaluation_id"),
        "strategy_identity": strategy_identity,
        "baseline_variant_identity": _required_text(payload, "baseline_variant_identity"),
        "baseline_signal_sequence": baseline_signal_sequence,
        "baseline_signal_sequence_original": baseline_signal_sequence_original,
        "market_data_identity": market_data_identity,
        "market_bars": market_bars,
        "macro_lineage": regime_candidate["macro_lineage"],
        "regime_observations": regime_candidate["regime_observations"],
        "regime_labels": regime_candidate["regime_labels"],
        "filter_policy": filter_policy,
        "inverse_regime_action_map": ablation_policy["inverse_regime_action_map"],
        "chronological_folds": chronological_folds,
        "transaction_cost": transaction_cost,
        "slippage_cost": slippage_cost,
        "execution_policy": execution_policy,
        "classification_policy": classification_policy,
        "minimum_evidence_policy": minimum_evidence_policy,
        "provenance": provenance,
        "hashable_request": hashable_request,
    }


def _validate_strategy_identity(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="strategy_identity")
    _reject_unknown_fields(
        payload,
        allowed={"strategy_id", "strategy_version", "strategy_builder", "symbol", "allows_short"},
        name="strategy_identity",
    )
    symbol = _required_text(payload, "symbol")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_version": _required_text(payload, "strategy_version"),
        "strategy_builder": _required_text(payload, "strategy_builder"),
        "symbol": symbol,
        "allows_short": _required_bool(payload, "allows_short"),
    }


def _validate_market_bars(value: Any) -> list[dict[str, Any]]:
    bars = _required_list(value, name="market_bars")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for raw in bars:
        bar = _required_mapping(raw, name="market_bar")
        timestamp = _required_text(bar, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("market_bars timestamps must be strictly increasing.")
        normalized.append(
            {
                "timestamp": timestamp,
                "open": _required_positive_number(bar, "open"),
                "high": _required_positive_number(bar, "high"),
                "low": _required_positive_number(bar, "low"),
                "close": _required_positive_number(bar, "close"),
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_signal_sequence(
    value: Any,
    *,
    strategy_identity: dict[str, Any],
    baseline_variant_identity: str,
    market_data_identity: str,
    market_bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signals = _required_list(value, name="baseline_signal_sequence")
    timestamps = {item["timestamp"] for item in market_bars}
    previous_timestamp: str | None = None
    seen_signal_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in signals:
        signal = _required_mapping(raw, name="baseline_signal")
        timestamp = _required_text(signal, "timestamp")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("baseline_signal_sequence timestamps must be strictly ordered.")
        if previous_timestamp is not None and timestamp == previous_timestamp:
            raise ValueError("baseline_signal_sequence contains duplicate timestamp.")
        if timestamp not in timestamps:
            raise ValueError("baseline_signal_sequence references future market-bar timestamp.")
        signal_id = _required_text(signal, "signal_id")
        if signal_id in seen_signal_ids:
            raise ValueError("baseline_signal_sequence signal_id must be unique.")
        seen_signal_ids.add(signal_id)
        signal_type = _required_text(signal, "signal_type")
        if signal_type not in {"entry", "exit", "rebalance"}:
            raise ValueError("unsupported signal_type.")
        target_direction = _required_text(signal, "target_direction")
        if not strategy_identity["allows_short"] and target_direction not in {"long", "flat"}:
            raise ValueError("shorting is not allowed by strategy identity.")
        if _required_text(signal, "strategy_identity") != strategy_identity["strategy_id"]:
            raise ValueError("baseline signal strategy_identity must match strategy_identity.strategy_id.")
        if _required_text(signal, "baseline_variant_id") != baseline_variant_identity:
            raise ValueError("baseline signal baseline_variant_id must match baseline_variant_identity.")
        if _required_text(signal, "symbol") != strategy_identity["symbol"]:
            raise ValueError("baseline signal symbol must match strategy identity symbol.")
        if _required_text(signal, "market_data_identity") != market_data_identity:
            raise ValueError("baseline signal market_data_identity must match request market_data_identity.")
        target_exposure = _required_non_negative_number(signal, "target_exposure")
        if target_exposure > 1.0:
            raise ValueError("target_exposure must be less than or equal to 1.")
        protective_exit = signal.get("protective_exit")
        if signal_type in {"entry", "rebalance"} and not isinstance(protective_exit, dict):
            raise ValueError("protective exits must be preserved exactly for entry and rebalance signals.")
        if signal_type == "exit" and protective_exit is not None:
            raise ValueError("exit signals must not include protective exits.")
        normalized.append(
            {
                "timestamp": timestamp,
                "signal_id": signal_id,
                "signal_type": signal_type,
                "target_direction": target_direction,
                "target_exposure": target_exposure,
                "strategy_identity": strategy_identity["strategy_id"],
                "baseline_variant_id": baseline_variant_identity,
                "symbol": strategy_identity["symbol"],
                "market_data_identity": market_data_identity,
                "protective_exit": _optional_mapping(protective_exit, name="protective_exit"),
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_regime_candidate(
    value: Any,
    *,
    macro_regime_candidate_output_sha256: str,
    macro_snapshot_sha256: str,
    alignment_output_sha256: str,
    feature_set_output_sha256: str,
) -> dict[str, Any]:
    payload = _required_mapping(value, name="macro_regime_candidate_result")
    if _required_text(payload, "version") != "macro_regime_filter_candidate_result_v1":
        raise ValueError("macro_regime_candidate_result.version must be macro_regime_filter_candidate_result_v1.")
    if _required_bool(payload, "production_runtime_supported"):
        raise ValueError("macro_regime_candidate_result.production_runtime_supported must be false.")
    if _required_bool(payload, "automatic_strategy_application_performed"):
        raise ValueError("macro_regime_candidate_result.automatic_strategy_application_performed must be false.")
    if _required_bool(payload, "candidate_only") is not True:
        raise ValueError("macro_regime_candidate_result.candidate_only must be true.")
    if _required_non_negative_number(payload, "provider_calls_used") != 0:
        raise ValueError("macro_regime_candidate_result.provider_calls_used must be 0.")
    if _required_bool(payload, "registry_write_performed"):
        raise ValueError("macro_regime_candidate_result.registry_write_performed must be false.")
    if _required_non_negative_number(payload, "broker_actions_used") != 0:
        raise ValueError("macro_regime_candidate_result.broker_actions_used must be 0.")
    if _required_bool(payload, "deployment_performed"):
        raise ValueError("macro_regime_candidate_result.deployment_performed must be false.")
    if _canonical_sha256(_without_field(payload, "output_payload_sha256")) != _required_text(payload, "output_payload_sha256"):
        raise ValueError("macro regime candidate output hash mismatch.")
    if _required_text(payload, "output_payload_sha256") != macro_regime_candidate_output_sha256:
        raise ValueError("macro regime-candidate hash mismatch.")
    macro_lineage = _required_mapping(payload.get("macro_lineage"), name="macro_lineage")
    if _required_text(macro_lineage, "macro_snapshot_sha256") != macro_snapshot_sha256:
        raise ValueError("macro snapshot hash mismatch.")
    if _required_text(macro_lineage, "alignment_output_sha256") != alignment_output_sha256:
        raise ValueError("alignment hash mismatch.")
    if _required_text(macro_lineage, "feature_set_output_sha256") != feature_set_output_sha256:
        raise ValueError("feature-set hash mismatch.")
    regime_observations = _required_list(payload.get("regime_observations"), name="regime_observations")
    previous_timestamp: str | None = None
    labels: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in regime_observations:
        item = _required_mapping(raw, name="regime_observation")
        timestamp = _required_text(item, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("macro regime observations must be strictly ordered.")
        availability = _required_mapping(item.get("feature_availability_timestamps_utc"), name="feature_availability_timestamps_utc")
        normalized.append(
            {
                "timestamp": timestamp,
                "regime_label": _required_text(item, "regime_label"),
                "feature_availability_timestamps_utc": dict(availability),
            }
        )
        labels.add(_required_text(item, "regime_label"))
        previous_timestamp = timestamp
    return {
        "output_payload_sha256": _required_text(payload, "output_payload_sha256"),
        "macro_lineage": {
            "macro_snapshot_sha256": macro_snapshot_sha256,
            "alignment_output_sha256": alignment_output_sha256,
            "feature_set_output_sha256": feature_set_output_sha256,
        },
        "regime_observations": normalized,
        "regime_labels": labels,
    }


def _validate_filter_policy(value: Any, *, regime_labels: set[str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="filter_policy")
    _reject_unknown_fields(payload, allowed={"regime_action_map"}, name="filter_policy")
    mapping = _required_mapping(payload.get("regime_action_map"), name="regime_action_map")
    normalized: dict[str, Any] = {}
    for label in regime_labels:
        if label not in mapping:
            raise ValueError(f"regime_action_map missing label: {label}")
        normalized[label] = _validate_action(mapping[label])
    return {"regime_action_map": normalized}


def _validate_ablation_policy(value: Any, *, regime_labels: set[str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="ablation_policy")
    _reject_unknown_fields(payload, allowed={"enable_inverse_filter", "inverse_regime_action_map"}, name="ablation_policy")
    enabled = _required_bool(payload, "enable_inverse_filter")
    inverse = payload.get("inverse_regime_action_map")
    if not enabled:
        if inverse is not None:
            raise ValueError("inverse filter disabled.")
        return {"inverse_regime_action_map": None}
    inverse_mapping = _required_mapping(inverse, name="inverse_regime_action_map")
    normalized: dict[str, Any] = {}
    for label in regime_labels:
        if label not in inverse_mapping:
            raise ValueError(f"inverse_regime_action_map missing label: {label}")
        normalized[label] = _validate_action(inverse_mapping[label])
    return {"inverse_regime_action_map": normalized}


def _validate_action(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="action")
    action = _required_text(payload, "action")
    if action not in {ACTION_ALLOW, ACTION_BLOCK, ACTION_REDUCE, ACTION_LEAVE}:
        raise ValueError(f"unknown filter action: {action}")
    normalized = {"action": action}
    if action == ACTION_REDUCE:
        factor = _required_non_negative_number(payload, "factor")
        if factor > 1.0:
            raise ValueError("factor must be less than or equal to 1.")
        normalized["factor"] = factor
    return normalized


def _validate_windows(value: Any, *, name: str) -> list[dict[str, Any]]:
    windows = _required_list(value, name=name)
    normalized: list[dict[str, Any]] = []
    for raw in windows:
        item = _required_mapping(raw, name="window")
        start_timestamp = _required_text(item, "start_timestamp")
        end_timestamp = _required_text(item, "end_timestamp")
        if end_timestamp < start_timestamp:
            raise ValueError("window end_timestamp must be greater than or equal to start_timestamp.")
        normalized.append(
            {
                "window_id": _required_text(item, "window_id"),
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            }
        )
    return normalized


def _validate_folds(value: Any, *, evaluation_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folds = _required_list(value, name="chronological_folds")
    normalized: list[dict[str, Any]] = []
    for raw in folds:
        item = _required_mapping(raw, name="chronological_fold")
        start_timestamp = _required_text(item, "start_timestamp")
        end_timestamp = _required_text(item, "end_timestamp")
        if not any(window["start_timestamp"] <= start_timestamp and end_timestamp <= window["end_timestamp"] for window in evaluation_windows):
            raise ValueError("chronological fold must fit within an evaluation window.")
        normalized.append(
            {
                "fold_id": _required_text(item, "fold_id"),
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "min_total_return": _required_finite_number(item, "min_total_return"),
                "max_drawdown_limit": _required_non_negative_number(item, "max_drawdown_limit"),
                "min_trade_count": _required_non_negative_int(item, "min_trade_count"),
            }
        )
    return normalized


def _validate_execution_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="execution_policy")
    if _required_text(payload, "fill_convention") != "next_open":
        raise ValueError("execution_policy.fill_convention must be next_open.")
    delay = _required_non_negative_int(payload, "decision_to_fill_delay_bars")
    allow_same_bar_fill = _required_bool(payload, "allow_same_bar_fill")
    if not allow_same_bar_fill and delay == 0:
        raise ValueError("no same-bar fill is allowed unless explicitly enabled.")
    return {
        "initial_capital": _required_positive_number(payload, "initial_capital"),
        "fill_convention": "next_open",
        "decision_to_fill_delay_bars": delay,
        "allow_same_bar_fill": allow_same_bar_fill,
    }


def _validate_classification_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="classification_policy")
    return {
        "risk": _required_mapping(payload.get("risk"), name="classification_policy.risk"),
        "return": _required_mapping(payload.get("return"), name="classification_policy.return"),
        "mixed": _required_mapping(payload.get("mixed"), name="classification_policy.mixed"),
        "no_value": _required_mapping(payload.get("no_value"), name="classification_policy.no_value"),
        "unstable": _required_mapping(payload.get("unstable"), name="classification_policy.unstable"),
    }


def _validate_minimum_evidence_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="minimum_evidence_policy")
    return {
        "min_candidate_trade_count": _required_non_negative_int(payload, "min_candidate_trade_count"),
        "min_fold_pass_rate": _required_non_negative_number(payload, "min_fold_pass_rate"),
        "min_regime_observations": _required_non_negative_int(payload, "min_regime_observations"),
    }


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value + "T00:00:00+00:00" if len(value) == 10 else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc


def _decision_boundary(signal_timestamp: str) -> datetime:
    return _parse_timestamp(signal_timestamp)


def _index_for_timestamp(bars: list[dict[str, Any]], timestamp: str) -> int:
    for index, bar in enumerate(bars):
        if bar["timestamp"] == timestamp:
            return index
    raise ValueError("signal timestamp missing from market bars.")


def _without_field(payload: dict[str, Any], field: str) -> dict[str, Any]:
    copy_payload = dict(payload)
    copy_payload.pop(field, None)
    return copy_payload


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _optional_mapping(value: Any, *, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _required_mapping(value, name=name)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _required_positive_number(payload: dict[str, Any], field: str) -> float:
    number = _required_finite_number(payload, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive.")
    return number


def _required_non_negative_number(payload: dict[str, Any], field: str) -> float:
    number = _required_finite_number(payload, field)
    if number < 0:
        raise ValueError(f"{field} must be non-negative.")
    return number


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _json_scalar(value: Any, *, name: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
