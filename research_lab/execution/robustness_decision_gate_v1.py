from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "robustness_decision_gate_request_v1"
RESULT_VERSION = "robustness_decision_gate_result_v1"
GATE_VERSION = "robustness_decision_gate_v1"

STATUS_PASS = "PASS"
STATUS_PASS_WITH_SIMPLIFICATION = "PASS_WITH_SIMPLIFICATION"
STATUS_REVISE = "REVISE"
STATUS_REJECT_OVERFIT = "REJECT_OVERFIT"
STATUS_REJECT_RISK = "REJECT_RISK"

_POLICY_ACTIONS = {STATUS_REVISE, STATUS_REJECT_OVERFIT, STATUS_REJECT_RISK}
_STABILITY_BLOCKING = {"ISOLATED_SPIKE", "UNSTABLE", "MONOTONIC_NO_OPTIMUM", "EDGE_OF_RANGE"}
_STABILITY_WEAK = {"ISOLATED_SPIKE", "UNSTABLE", "NARROW_PLATEAU", "MONOTONIC_NO_OPTIMUM", "EDGE_OF_RANGE"}


def build_robustness_decision_gate(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    blocking_reasons: list[str] = []
    missing_evidence: list[str] = []
    weak_parameters: list[dict[str, str]] = []

    parameter_status = _parameter_findings(validated["parameter_stability_results"])
    blocking_reasons.extend(parameter_status["blocking_reasons"])
    weak_parameters.extend(parameter_status["weak_parameters"])
    blocking_reasons.extend(validated["robustness_review_result"]["blocking_reasons"])

    fold_failures = _fold_failures(validated["walk_forward_fold_evidence"])
    if fold_failures:
        blocking_reasons.append("walk_forward_fold_failures_present")

    evidence_statuses = [
        _evidence_status(
            "effective_sample_metadata",
            validated["effective_sample_metadata"]["available"],
            validated["effective_sample_metadata"]["passed"],
            "missing_effective_sample_evidence",
            "effective_sample_below_policy",
            validated["decision_policy"]["missing_effective_sample_action"],
            validated["decision_policy"]["failed_effective_sample_action"],
        ),
        _evidence_status(
            "deflated_sharpe_result",
            validated["deflated_sharpe_result"]["available"],
            validated["deflated_sharpe_result"]["passed"],
            "missing_deflated_sharpe_evidence",
            "deflated_sharpe_below_policy",
            validated["decision_policy"]["missing_dsr_action"],
            validated["decision_policy"]["failed_dsr_action"],
        ),
        _evidence_status(
            "pbo_cscv_result",
            validated["pbo_cscv_result"]["available"],
            validated["pbo_cscv_result"]["passed"],
            "missing_pbo_cscv_evidence",
            "pbo_cscv_above_policy",
            validated["decision_policy"]["missing_pbo_action"],
            validated["decision_policy"]["failed_pbo_action"],
        ),
        _evidence_status(
            "drawdown_stress_result",
            validated["drawdown_stress_result"]["available"],
            validated["drawdown_stress_result"]["passed"],
            "missing_drawdown_stress_evidence",
            "drawdown_stress_failed",
            validated["decision_policy"]["missing_drawdown_stress_action"],
            validated["decision_policy"]["failed_drawdown_stress_action"],
        ),
    ]
    for status in evidence_statuses:
        if status is None:
            continue
        blocking_reasons.append(status["reason"])
        if status["missing"]:
            missing_evidence.append(status["name"])

    if not validated["trial_count_metadata"]["complete_accounting"]:
        blocking_reasons.append("incomplete_trial_accounting")
    if not validated["trial_count_metadata"]["bounded_search"]:
        blocking_reasons.append("unbounded_search_not_allowed")

    accepted_variants, rejected_variants = _variant_decisions(validated)
    selected_variant_id = _select_variant_id(
        accepted_variants=accepted_variants,
        baseline_variant_id=validated["strategy_identity"]["baseline_variant_id"],
    )

    decision_status = _decision_status(
        robustness_status=validated["robustness_review_result"]["robustness_status"],
        blocking_reasons=blocking_reasons,
        evidence_statuses=evidence_statuses,
        complete_accounting=validated["trial_count_metadata"]["complete_accounting"],
        accepted_variants=accepted_variants,
        baseline_variant_id=validated["strategy_identity"]["baseline_variant_id"],
        selected_variant_id=selected_variant_id,
    )
    if decision_status == STATUS_REJECT_RISK and "drawdown_stress_failed" not in blocking_reasons and not accepted_variants:
        blocking_reasons.append("no_variant_preserves_required_risk_controls")

    knowledge_note_ids_used = sorted(
        set(validated["robustness_review_result"]["knowledge_note_ids_used"])
        | {item["note_id"] for item in validated["validated_knihomol_evidence"]["notes"]}
    )

    result = {
        "version": RESULT_VERSION,
        "gate_version": GATE_VERSION,
        "strategy_identity": validated["strategy_identity"],
        "decision_status": decision_status,
        "selected_variant_id": selected_variant_id,
        "recommended_variant_id": selected_variant_id,
        "accepted_variants": accepted_variants,
        "rejected_variants": rejected_variants,
        "ablation_classifications": _ablation_classifications(validated["ablation_result"]["ablation_results"]),
        "weak_parameters": weak_parameters,
        "fold_failures": fold_failures,
        "selection_bias_findings": {
            "required_checks": validated["robustness_review_result"]["required_selection_bias_checks"],
            "blocking_reasons": _matching_blocking_reasons(
                blocking_reasons,
                prefixes=("overfit", "selection_bias", "incomplete_trial_accounting", "unbounded_search"),
            ),
        },
        "drawdown_findings": {
            "required_checks": validated["robustness_review_result"]["required_drawdown_checks"],
            "blocking_reasons": _matching_blocking_reasons(blocking_reasons, prefixes=("drawdown",)),
        },
        "complexity_findings": {
            "required_parameter_checks": validated["robustness_review_result"]["required_parameter_checks"],
            "complexity_budget": validated["robustness_review_result"]["complexity_budget"],
        },
        "knowledge_note_ids_used": knowledge_note_ids_used,
        "missing_evidence": missing_evidence,
        "blocking_reasons": blocking_reasons,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "strategy_identity",
            "robustness_review_result",
            "ablation_result",
            "parameter_stability_results",
            "baseline_review_artifact",
            "walk_forward_fold_evidence",
            "effective_sample_metadata",
            "trial_count_metadata",
            "deflated_sharpe_result",
            "pbo_cscv_result",
            "drawdown_stress_result",
            "complexity_variants",
            "validated_knihomol_evidence",
            "decision_policy",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    strategy_identity = _validate_strategy_identity(payload.get("strategy_identity"))
    robustness_review_result = _validate_robustness_review_result(payload.get("robustness_review_result"))
    ablation_result = _validate_ablation_result(payload.get("ablation_result"), expected_strategy_id=strategy_identity["strategy_id"])
    parameter_stability_results = _validate_parameter_stability_results(payload.get("parameter_stability_results"))
    baseline_review_artifact = _validate_baseline_review_artifact(payload.get("baseline_review_artifact"), expected_symbol=strategy_identity["symbol"])
    walk_forward_fold_evidence = _validate_walk_forward_fold_evidence(payload.get("walk_forward_fold_evidence"), expected_strategy_id=strategy_identity["strategy_id"])
    effective_sample_metadata = _validate_effective_sample_metadata(payload.get("effective_sample_metadata"), expected_strategy_id=strategy_identity["strategy_id"])
    trial_count_metadata = _validate_trial_count_metadata(payload.get("trial_count_metadata"), expected_strategy_id=strategy_identity["strategy_id"])
    deflated_sharpe_result = _validate_binary_evidence(
        payload.get("deflated_sharpe_result"),
        name="deflated_sharpe_result",
        expected_strategy_id=strategy_identity["strategy_id"],
        observed_field="observed_value",
        threshold_field="minimum_required",
    )
    pbo_cscv_result = _validate_binary_evidence(
        payload.get("pbo_cscv_result"),
        name="pbo_cscv_result",
        expected_strategy_id=strategy_identity["strategy_id"],
        observed_field="observed_value",
        threshold_field="maximum_allowed",
    )
    drawdown_stress_result = _validate_binary_evidence(
        payload.get("drawdown_stress_result"),
        name="drawdown_stress_result",
        expected_strategy_id=strategy_identity["strategy_id"],
        observed_field="stressed_max_drawdown",
        threshold_field="maximum_allowed_drawdown",
    )
    complexity_variants = _validate_complexity_variants(
        payload.get("complexity_variants"),
        expected_strategy_id=strategy_identity["strategy_id"],
        expected_variant_ids={item["variant_id"] for item in ablation_result["ablation_results"]},
    )
    validated_knihomol_evidence = _validate_knihomol_evidence(payload.get("validated_knihomol_evidence"))
    decision_policy = _validate_decision_policy(payload.get("decision_policy"))
    if robustness_review_result["production_runtime_supported"] is not False:
        raise ValueError("robustness_review_result.production_runtime_supported must be false.")
    if baseline_review_artifact["final_review_status"] != "REVIEW_REQUIRED":
        raise ValueError("baseline_review_artifact.final_review_status must be REVIEW_REQUIRED.")
    return {
        "version": version,
        "strategy_identity": strategy_identity,
        "robustness_review_result": robustness_review_result,
        "ablation_result": ablation_result,
        "parameter_stability_results": parameter_stability_results,
        "baseline_review_artifact": baseline_review_artifact,
        "walk_forward_fold_evidence": walk_forward_fold_evidence,
        "effective_sample_metadata": effective_sample_metadata,
        "trial_count_metadata": trial_count_metadata,
        "deflated_sharpe_result": deflated_sharpe_result,
        "pbo_cscv_result": pbo_cscv_result,
        "drawdown_stress_result": drawdown_stress_result,
        "complexity_variants": complexity_variants,
        "validated_knihomol_evidence": validated_knihomol_evidence,
        "decision_policy": decision_policy,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_strategy_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="strategy_identity")
    _reject_unknown_fields(payload, allowed={"strategy_id", "strategy_builder", "symbol", "baseline_variant_id"}, name="strategy_identity")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": _required_text(payload, "strategy_builder"),
        "symbol": _required_text(payload, "symbol"),
        "baseline_variant_id": _required_text(payload, "baseline_variant_id"),
    }


def _validate_robustness_review_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_review_result")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "robustness_status",
            "required_parameter_checks",
            "required_walk_forward_checks",
            "required_selection_bias_checks",
            "required_drawdown_checks",
            "complexity_budget",
            "blocking_reasons",
            "knowledge_note_ids_used",
            "production_runtime_supported",
        },
        name="robustness_review_result",
    )
    if _required_text(payload, "version") != "strategy_robustness_review_contract_result_v1":
        raise ValueError("robustness_review_result.version must be strategy_robustness_review_contract_result_v1.")
    return {
        "version": "strategy_robustness_review_contract_result_v1",
        "robustness_status": _required_text(payload, "robustness_status"),
        "required_parameter_checks": _required_text_list(payload.get("required_parameter_checks"), name="required_parameter_checks"),
        "required_walk_forward_checks": _required_text_list(payload.get("required_walk_forward_checks"), name="required_walk_forward_checks"),
        "required_selection_bias_checks": _required_text_list(payload.get("required_selection_bias_checks"), name="required_selection_bias_checks"),
        "required_drawdown_checks": _required_text_list(payload.get("required_drawdown_checks"), name="required_drawdown_checks"),
        "complexity_budget": _required_mapping(payload.get("complexity_budget"), name="complexity_budget"),
        "blocking_reasons": _required_text_list(payload.get("blocking_reasons"), name="blocking_reasons"),
        "knowledge_note_ids_used": _required_text_list(payload.get("knowledge_note_ids_used"), name="knowledge_note_ids_used"),
        "production_runtime_supported": _required_bool(payload, "production_runtime_supported"),
    }


def _validate_ablation_result(value: Any, *, expected_strategy_id: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="ablation_result")
    _reject_unknown_fields(payload, allowed={"version", "ablation_results", "production_runtime_supported"}, name="ablation_result")
    if _required_text(payload, "version") != "deterministic_ablation_evaluator_result_v1":
        raise ValueError("ablation_result.version must be deterministic_ablation_evaluator_result_v1.")
    if _required_bool(payload, "production_runtime_supported"):
        raise ValueError("ablation_result.production_runtime_supported must be false.")
    results = _required_list(payload.get("ablation_results"), name="ablation_results")
    normalized: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    for item in results:
        entry = _required_mapping(item, name="ablation_result item")
        _reject_unknown_fields(
            entry,
            allowed={"variant_id", "strategy_id", "removed_rule", "classification", "total_return_delta", "max_drawdown_delta"},
            name="ablation_result item",
        )
        variant_id = _required_text(entry, "variant_id")
        if variant_id in seen_variant_ids:
            raise ValueError("ablation_result.variant_id values must be unique.")
        seen_variant_ids.add(variant_id)
        strategy_id = _required_text(entry, "strategy_id")
        if strategy_id != expected_strategy_id:
            raise ValueError("ablation_result strategy_id must match strategy_identity.strategy_id.")
        removed_rule = _required_mapping(entry.get("removed_rule"), name="removed_rule")
        _reject_unknown_fields(removed_rule, allowed={"rule_id", "rule_role"}, name="removed_rule")
        normalized.append(
            {
                "variant_id": variant_id,
                "strategy_id": strategy_id,
                "removed_rule": {
                    "rule_id": _required_text(removed_rule, "rule_id"),
                    "rule_role": _required_text(removed_rule, "rule_role"),
                },
                "classification": _required_text(entry, "classification"),
                "total_return_delta": _required_finite_number(entry, "total_return_delta"),
                "max_drawdown_delta": _required_finite_number(entry, "max_drawdown_delta"),
            }
        )
    return {
        "version": "deterministic_ablation_evaluator_result_v1",
        "ablation_results": sorted(normalized, key=lambda item: item["variant_id"]),
        "production_runtime_supported": False,
    }


def _validate_parameter_stability_results(value: Any) -> list[dict[str, Any]]:
    items = _required_list(value, name="parameter_stability_results")
    normalized: list[dict[str, Any]] = []
    seen_parameter_names: set[str] = set()
    for item in items:
        payload = _required_mapping(item, name="parameter_stability_result")
        _reject_unknown_fields(
            payload,
            allowed={"version", "parameter_name", "baseline_value", "stability_classification", "production_runtime_supported"},
            name="parameter_stability_result",
        )
        if _required_text(payload, "version") != "parameter_stability_evaluator_result_v1":
            raise ValueError("parameter_stability_result.version must be parameter_stability_evaluator_result_v1.")
        parameter_name = _required_text(payload, "parameter_name")
        if parameter_name in seen_parameter_names:
            raise ValueError("parameter_stability_results parameter_name values must be unique.")
        seen_parameter_names.add(parameter_name)
        if _required_bool(payload, "production_runtime_supported"):
            raise ValueError("parameter_stability_result.production_runtime_supported must be false.")
        normalized.append(
            {
                "version": "parameter_stability_evaluator_result_v1",
                "parameter_name": parameter_name,
                "baseline_value": _json_scalar(payload.get("baseline_value"), name="baseline_value"),
                "stability_classification": _required_text(payload, "stability_classification"),
                "production_runtime_supported": False,
            }
        )
    return sorted(normalized, key=lambda item: item["parameter_name"])


def _validate_baseline_review_artifact(value: Any, *, expected_symbol: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="baseline_review_artifact")
    _reject_unknown_fields(payload, allowed={"version", "candidate_id", "candidate_sha256", "symbol", "final_review_status"}, name="baseline_review_artifact")
    if _required_text(payload, "version") != "result_review_gate_result_v1":
        raise ValueError("baseline_review_artifact.version must be result_review_gate_result_v1.")
    symbol = _required_text(payload, "symbol")
    if symbol != expected_symbol:
        raise ValueError("baseline_review_artifact.symbol must match strategy_identity.symbol.")
    return {
        "version": "result_review_gate_result_v1",
        "candidate_id": _required_text(payload, "candidate_id"),
        "candidate_sha256": _required_text(payload, "candidate_sha256"),
        "symbol": symbol,
        "final_review_status": _required_text(payload, "final_review_status"),
    }


def _validate_walk_forward_fold_evidence(value: Any, *, expected_strategy_id: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="walk_forward_fold_evidence")
    _reject_unknown_fields(payload, allowed={"strategy_id", "fold_results"}, name="walk_forward_fold_evidence")
    strategy_id = _required_text(payload, "strategy_id")
    if strategy_id != expected_strategy_id:
        raise ValueError("walk_forward_fold_evidence.strategy_id must match strategy_identity.strategy_id.")
    fold_results = _required_list(payload.get("fold_results"), name="fold_results")
    normalized: list[dict[str, Any]] = []
    seen_fold_ids: set[str] = set()
    for item in fold_results:
        entry = _required_mapping(item, name="fold_result")
        _reject_unknown_fields(entry, allowed={"fold_id", "passed", "failure_reasons"}, name="fold_result")
        fold_id = _required_text(entry, "fold_id")
        if fold_id in seen_fold_ids:
            raise ValueError("fold_id values must be unique.")
        seen_fold_ids.add(fold_id)
        normalized.append(
            {
                "fold_id": fold_id,
                "passed": _required_bool(entry, "passed"),
                "failure_reasons": _required_text_list(entry.get("failure_reasons"), name="failure_reasons"),
            }
        )
    return {"strategy_id": strategy_id, "fold_results": sorted(normalized, key=lambda item: item["fold_id"])}


def _validate_effective_sample_metadata(value: Any, *, expected_strategy_id: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="effective_sample_metadata")
    _reject_unknown_fields(payload, allowed={"strategy_id", "effective_sample_size", "minimum_required", "available", "passed"}, name="effective_sample_metadata")
    strategy_id = _required_text(payload, "strategy_id")
    if strategy_id != expected_strategy_id:
        raise ValueError("effective_sample_metadata.strategy_id must match strategy_identity.strategy_id.")
    available = _optional_bool(payload.get("available"), default=True, field="available")
    passed = _required_bool(payload, "passed")
    return {
        "strategy_id": strategy_id,
        "effective_sample_size": _required_non_negative_int(payload, "effective_sample_size"),
        "minimum_required": _required_positive_int(payload, "minimum_required"),
        "available": available,
        "passed": passed,
    }


def _validate_trial_count_metadata(value: Any, *, expected_strategy_id: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="trial_count_metadata")
    _reject_unknown_fields(payload, allowed={"strategy_id", "total_trials", "complete_accounting", "bounded_search", "selection_mode"}, name="trial_count_metadata")
    strategy_id = _required_text(payload, "strategy_id")
    if strategy_id != expected_strategy_id:
        raise ValueError("trial_count_metadata.strategy_id must match strategy_identity.strategy_id.")
    return {
        "strategy_id": strategy_id,
        "total_trials": _required_positive_int(payload, "total_trials"),
        "complete_accounting": _required_bool(payload, "complete_accounting"),
        "bounded_search": _required_bool(payload, "bounded_search"),
        "selection_mode": _required_text(payload, "selection_mode"),
    }


def _validate_binary_evidence(
    value: Any,
    *,
    name: str,
    expected_strategy_id: str,
    observed_field: str,
    threshold_field: str,
) -> dict[str, Any]:
    payload = _required_mapping(value, name=name)
    _reject_unknown_fields(payload, allowed={"strategy_id", "available", "passed", observed_field, threshold_field}, name=name)
    strategy_id = _required_text(payload, "strategy_id")
    if strategy_id != expected_strategy_id:
        raise ValueError(f"{name}.strategy_id must match strategy_identity.strategy_id.")
    return {
        "strategy_id": strategy_id,
        "available": _required_bool(payload, "available"),
        "passed": _required_bool(payload, "passed"),
        observed_field: _required_finite_number(payload, observed_field),
        threshold_field: _required_finite_number(payload, threshold_field),
    }


def _validate_complexity_variants(value: Any, *, expected_strategy_id: str, expected_variant_ids: set[str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="complexity_variants")
    _reject_unknown_fields(payload, allowed={"strategy_id", "variants"}, name="complexity_variants")
    strategy_id = _required_text(payload, "strategy_id")
    if strategy_id != expected_strategy_id:
        raise ValueError("complexity_variants.strategy_id must match strategy_identity.strategy_id.")
    variants = _required_list(payload.get("variants"), name="complexity_variants.variants")
    normalized: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    for item in variants:
        entry = _required_mapping(item, name="complexity_variant")
        _reject_unknown_fields(entry, allowed={"variant_id", "parameter_count", "complexity_score", "required_risk_controls_preserved"}, name="complexity_variant")
        variant_id = _required_text(entry, "variant_id")
        if variant_id in seen_variant_ids:
            raise ValueError("complexity_variants.variant_id values must be unique.")
        seen_variant_ids.add(variant_id)
        if variant_id not in expected_variant_ids:
            raise ValueError("complexity_variants.variant_id must exist in ablation_result.")
        normalized.append(
            {
                "variant_id": variant_id,
                "parameter_count": _required_positive_int(entry, "parameter_count"),
                "complexity_score": _required_finite_number(entry, "complexity_score"),
                "required_risk_controls_preserved": _required_bool(entry, "required_risk_controls_preserved"),
            }
        )
    return {"strategy_id": strategy_id, "variants": sorted(normalized, key=lambda item: item["variant_id"])}


def _validate_knihomol_evidence(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="validated_knihomol_evidence")
    _reject_unknown_fields(payload, allowed={"notes"}, name="validated_knihomol_evidence")
    notes = _required_list(payload.get("notes"), name="validated_knihomol_evidence.notes")
    normalized: list[dict[str, Any]] = []
    for item in notes:
        note = _required_mapping(item, name="validated_knihomol_evidence note")
        _reject_unknown_fields(note, allowed={"note_id", "status", "topic", "summary", "supports"}, name="validated_knihomol_evidence note")
        status = _required_text(note, "status")
        if status != "validated":
            raise ValueError("validated_knihomol_evidence notes must have status=validated.")
        normalized.append(
            {
                "note_id": _required_text(note, "note_id"),
                "status": status,
                "topic": _required_text(note, "topic"),
                "summary": _required_text(note, "summary"),
                "supports": _required_text_list(note.get("supports"), name="supports"),
            }
        )
    return {"notes": sorted(normalized, key=lambda item: item["note_id"])}


def _validate_decision_policy(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="decision_policy")
    _reject_unknown_fields(
        payload,
        allowed={
            "missing_effective_sample_action",
            "failed_effective_sample_action",
            "missing_dsr_action",
            "failed_dsr_action",
            "missing_pbo_action",
            "failed_pbo_action",
            "missing_drawdown_stress_action",
            "failed_drawdown_stress_action",
        },
        name="decision_policy",
    )
    normalized = {key: _required_text(payload, key) for key in payload}
    for key, value_text in normalized.items():
        if value_text not in _POLICY_ACTIONS:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(_POLICY_ACTIONS))}.")
    return normalized


def _parameter_findings(results: list[dict[str, Any]]) -> dict[str, list[Any]]:
    blocking_reasons: list[str] = []
    weak_parameters: list[dict[str, str]] = []
    for item in results:
        classification = item["stability_classification"]
        if classification in _STABILITY_WEAK:
            weak_parameters.append(
                {
                    "parameter_name": item["parameter_name"],
                    "stability_classification": classification,
                }
            )
        if classification in _STABILITY_BLOCKING:
            blocking_reasons.append(f"isolated_spike_detected:{item['parameter_name']}" if classification == "ISOLATED_SPIKE" else f"parameter_instability:{item['parameter_name']}:{classification}")
    return {
        "blocking_reasons": blocking_reasons,
        "weak_parameters": sorted(weak_parameters, key=lambda item: item["parameter_name"]),
    }


def _fold_failures(walk_forward_fold_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    failures = [
        {
            "fold_id": item["fold_id"],
            "failure_reasons": item["failure_reasons"],
        }
        for item in walk_forward_fold_evidence["fold_results"]
        if (not item["passed"]) or item["failure_reasons"]
    ]
    return sorted(failures, key=lambda item: item["fold_id"])


def _ablation_classifications(ablation_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "variant_id": item["variant_id"],
            "classification": item["classification"],
        }
        for item in ablation_results
    ]


def _matching_blocking_reasons(blocking_reasons: list[str], *, prefixes: tuple[str, ...]) -> list[str]:
    return [item for item in blocking_reasons if item.startswith(prefixes)]


def _evidence_status(
    name: str,
    available: bool,
    passed: bool,
    missing_reason: str,
    failed_reason: str,
    missing_action: str,
    failed_action: str,
) -> dict[str, Any] | None:
    if not available:
        return {"name": name, "reason": missing_reason, "action": missing_action, "missing": True}
    if not passed:
        return {"name": name, "reason": failed_reason, "action": failed_action, "missing": False}
    return None


def _variant_decisions(validated: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ablation_by_variant = {item["variant_id"]: item for item in validated["ablation_result"]["ablation_results"]}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for variant in validated["complexity_variants"]["variants"]:
        ablation = ablation_by_variant[variant["variant_id"]]
        if (not variant["required_risk_controls_preserved"]) or ablation["classification"] == "REQUIRED_FOR_RISK_SAFETY":
            rejected.append({"variant_id": variant["variant_id"], "reason": "required_risk_safety_rule_removed"})
            continue
        accepted.append(
            {
                "variant_id": variant["variant_id"],
                "parameter_count": variant["parameter_count"],
                "complexity_score": variant["complexity_score"],
            }
        )
    return sorted(accepted, key=lambda item: (item["parameter_count"], item["complexity_score"], item["variant_id"])), sorted(
        rejected,
        key=lambda item: item["variant_id"],
    )


def _select_variant_id(*, accepted_variants: list[dict[str, Any]], baseline_variant_id: str) -> str | None:
    if not accepted_variants:
        return None
    preferred = next((item for item in accepted_variants if item["variant_id"] == baseline_variant_id), None)
    simplest = min(accepted_variants, key=lambda item: (item["parameter_count"], item["complexity_score"], item["variant_id"]))
    if preferred is not None and preferred == simplest:
        return baseline_variant_id
    return simplest["variant_id"]


def _decision_status(
    *,
    robustness_status: str,
    blocking_reasons: list[str],
    evidence_statuses: list[dict[str, Any] | None],
    complete_accounting: bool,
    accepted_variants: list[dict[str, Any]],
    baseline_variant_id: str,
    selected_variant_id: str | None,
) -> str:
    if not accepted_variants:
        return STATUS_REJECT_RISK
    if robustness_status == STATUS_REJECT_OVERFIT:
        return STATUS_REJECT_OVERFIT
    if robustness_status == STATUS_REVISE:
        return STATUS_REVISE
    if not complete_accounting:
        return STATUS_REJECT_OVERFIT
    actions = [item["action"] for item in evidence_statuses if item is not None]
    if any(reason.startswith("isolated_spike_detected:") or reason.startswith("parameter_instability:") for reason in blocking_reasons):
        return STATUS_REJECT_OVERFIT
    if any(reason == "drawdown_stress_failed" for reason in blocking_reasons):
        return STATUS_REJECT_RISK
    if any(reason == "walk_forward_fold_failures_present" for reason in blocking_reasons):
        return STATUS_REVISE
    if STATUS_REJECT_RISK in actions:
        return STATUS_REJECT_RISK
    if STATUS_REJECT_OVERFIT in actions or "unbounded_search_not_allowed" in blocking_reasons:
        return STATUS_REJECT_OVERFIT
    if STATUS_REVISE in actions or blocking_reasons:
        return STATUS_REVISE
    if selected_variant_id is not None and selected_variant_id != baseline_variant_id:
        return STATUS_PASS_WITH_SIMPLIFICATION
    return STATUS_PASS


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


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_text_list(value: Any, *, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty text.")
        normalized.append(item.strip())
    return normalized


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _optional_bool(value: Any, *, default: bool, field: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
