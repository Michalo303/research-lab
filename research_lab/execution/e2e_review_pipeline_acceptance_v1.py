from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from research_lab.execution.deterministic_ablation_evaluator_v1 import (
    evaluate_deterministic_ablations,
)
from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.markov_hmm_regime_pilot_v1 import (
    run_markov_hmm_regime_pilot,
)
from research_lab.execution.parameter_stability_evaluator_v1 import (
    evaluate_parameter_stability,
)
from research_lab.execution.qlib_isolated_evaluator_v1 import (
    run_qlib_isolated_evaluator,
)
from research_lab.execution.rd_agent_proposal_contract_v1 import (
    build_rd_agent_proposal_contract,
)
from research_lab.execution.result_review_gate_v1 import (
    build_result_review_gate,
)
from research_lab.execution.robustness_decision_gate_v1 import (
    build_robustness_decision_gate,
)
from research_lab.execution.strategy_execution_bridge_synthetic_executor_v1 import (
    run_strategy_execution_bridge_synthetic_executor,
)
from research_lab.execution.strategy_robustness_review_contract_v1 import (
    build_strategy_robustness_review_contract,
)
from research_lab.execution.swing_trend_filtered_pullback_strategy_contract_v1 import (
    build_swing_trend_filtered_pullback_strategy_contract,
)


REQUEST_VERSION = "e2e_review_pipeline_acceptance_request_v1"
RESULT_VERSION = "e2e_review_pipeline_acceptance_result_v1"
PIPELINE_VERSION = "e2e_review_pipeline_acceptance_v1"


def run_e2e_review_pipeline_acceptance(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    adapter_result = build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": validated["symbol"],
            "input_bars": validated["input_bars"],
            "provenance": validated["provenance"],
        }
    )
    strategy_contract_result = build_swing_trend_filtered_pullback_strategy_contract(
        {
            "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
            "symbol": adapter_result["symbol"],
            "synthetic_bars": adapter_result["synthetic_bars"],
            "strategy_parameters": validated["strategy_parameters"],
            "provenance": validated["provenance"],
        }
    )
    bridge_request = {
        "version": "strategy_execution_capability_bridge_request_v1",
        "strategy_builder": validated["strategy_identity"]["strategy_builder"],
        "symbol": strategy_contract_result["symbol"],
        "synthetic_bars": strategy_contract_result["synthetic_bars"],
        "strategy_signal_plan": strategy_contract_result["strategy_signal_plan"],
        "provenance": validated["provenance"],
    }
    bridge_executor_result = run_strategy_execution_bridge_synthetic_executor(
        {
            "version": "strategy_execution_bridge_synthetic_executor_request_v1",
            "bridge_request": bridge_request,
            "executor_config": validated["executor_config"],
            "provenance": validated["provenance"],
        }
    )
    review_artifact = build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": adapter_result,
            "strategy_contract_result": strategy_contract_result,
            "bridge_result": bridge_executor_result["bridge_result"],
            "isolated_execution_result": bridge_executor_result["executor_result"],
            "provenance": validated["provenance"],
        }
    )
    robustness_review_result = build_strategy_robustness_review_contract(
        {
            "version": "strategy_robustness_review_contract_request_v1",
            "strategy_contract": strategy_contract_result,
            "baseline_review_artifact": review_artifact,
            "parameter_schema": validated["robustness_review_inputs"]["parameter_schema"],
            "evaluation_window_metadata": validated["robustness_review_inputs"]["evaluation_window_metadata"],
            "experiment_trial_metadata": validated["robustness_review_inputs"]["experiment_trial_metadata"],
            "validated_knihomol_evidence": validated["robustness_review_inputs"]["validated_knihomol_evidence"],
            "robustness_policy": validated["robustness_review_inputs"]["robustness_policy"],
            "provenance": validated["provenance"],
        }
    )
    ablation_result = evaluate_deterministic_ablations(
        {
            "version": "deterministic_ablation_evaluator_request_v1",
            "strategy_contract": strategy_contract_result,
            "baseline_variant": validated["ablation_inputs"]["baseline_variant"],
            "ablated_variants": validated["ablation_inputs"]["ablated_variants"],
            "ablation_policy": validated["ablation_inputs"]["ablation_policy"],
            "provenance": validated["provenance"],
        }
    )
    parameter_stability_results = [
        evaluate_parameter_stability(
            {
                **item,
                "provenance": validated["provenance"],
            }
        )
        for item in validated["parameter_stability_inputs"]
    ]
    robustness_decision_result = build_robustness_decision_gate(
        {
            "version": "robustness_decision_gate_request_v1",
            "strategy_identity": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                "strategy_builder": validated["strategy_identity"]["strategy_builder"],
                "symbol": strategy_contract_result["symbol"],
                "baseline_variant_id": validated["strategy_identity"]["baseline_variant_id"],
            },
            "robustness_review_result": _robustness_review_gate_view(robustness_review_result),
            "ablation_result": _ablation_gate_view(ablation_result),
            "parameter_stability_results": _parameter_stability_gate_views(parameter_stability_results),
            "baseline_review_artifact": _baseline_review_gate_view(review_artifact),
            "walk_forward_fold_evidence": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                "fold_results": validated["robustness_decision_inputs"]["walk_forward_fold_evidence"]["fold_results"],
            },
            "effective_sample_metadata": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                **validated["robustness_decision_inputs"]["effective_sample_metadata"],
            },
            "trial_count_metadata": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                **validated["robustness_decision_inputs"]["trial_count_metadata"],
            },
            "deflated_sharpe_result": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                **validated["robustness_decision_inputs"]["deflated_sharpe_result"],
            },
            "pbo_cscv_result": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                **validated["robustness_decision_inputs"]["pbo_cscv_result"],
            },
            "drawdown_stress_result": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                **validated["robustness_decision_inputs"]["drawdown_stress_result"],
            },
            "complexity_variants": {
                "strategy_id": validated["strategy_identity"]["strategy_id"],
                "variants": validated["robustness_decision_inputs"]["complexity_variants"]["variants"],
            },
            "validated_knihomol_evidence": validated["robustness_review_inputs"]["validated_knihomol_evidence"],
            "decision_policy": validated["robustness_decision_inputs"]["decision_policy"],
            "provenance": validated["provenance"],
        }
    )
    reviewed_robustness_context = _reviewed_robustness_context(
        validated=validated,
        robustness_decision_result=robustness_decision_result,
        parameter_stability_results=parameter_stability_results,
        ablation_result=ablation_result,
    )
    qlib_evaluation = run_qlib_isolated_evaluator(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "review_artifact",
            "review_artifact": review_artifact,
            "evaluation_mode": "deterministic_local",
            "provenance": validated["provenance"],
        }
    )
    regime_pilot_result = run_markov_hmm_regime_pilot(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "review_artifact": review_artifact,
            "provenance": validated["provenance"],
        }
    )
    rd_agent_proposal = build_rd_agent_proposal_contract(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": review_artifact,
            "robustness_context": reviewed_robustness_context,
            "qlib_evaluation": qlib_evaluation,
            "regime_pilot_result": regime_pilot_result,
            "parameters": {"mode": "deterministic_local"},
            "provenance": validated["provenance"],
        }
    )

    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "adapter_result": adapter_result,
        "strategy_contract_result": strategy_contract_result,
        "bridge_result": bridge_executor_result["bridge_result"],
        "bridge_executor_result": bridge_executor_result,
        "isolated_execution_result": bridge_executor_result["executor_result"],
        "review_artifact": review_artifact,
        "robustness_review_result": robustness_review_result,
        "ablation_result": ablation_result,
        "parameter_stability_results": parameter_stability_results,
        "robustness_decision_result": robustness_decision_result,
        "qlib_evaluation": qlib_evaluation,
        "regime_pilot_result": regime_pilot_result,
        "rd_agent_proposal": rd_agent_proposal,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "generated_code_executed": False,
        "external_data_used": False,
        "production_runtime_supported": False,
        "input_sha256": _canonical_sha256(validated),
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
            "symbol",
            "input_bars",
            "strategy_parameters",
            "executor_config",
            "strategy_rule_definitions",
            "robustness_review_inputs",
            "ablation_inputs",
            "parameter_stability_inputs",
            "robustness_decision_inputs",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    strategy_identity = _validate_strategy_identity(payload.get("strategy_identity"))
    return {
        "version": version,
        "strategy_identity": strategy_identity,
        "symbol": _required_text(payload, "symbol").upper(),
        "input_bars": _required_non_empty_list(payload.get("input_bars"), name="input_bars"),
        "strategy_parameters": _required_mapping(payload.get("strategy_parameters"), name="strategy_parameters"),
        "executor_config": _required_mapping(payload.get("executor_config"), name="executor_config"),
        "strategy_rule_definitions": _validate_strategy_rule_definitions(payload.get("strategy_rule_definitions")),
        "robustness_review_inputs": _validate_robustness_review_inputs(payload.get("robustness_review_inputs")),
        "ablation_inputs": _validate_ablation_inputs(payload.get("ablation_inputs"), expected_strategy_id=strategy_identity["strategy_id"]),
        "parameter_stability_inputs": _validate_parameter_stability_inputs(payload.get("parameter_stability_inputs")),
        "robustness_decision_inputs": _validate_robustness_decision_inputs(payload.get("robustness_decision_inputs")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_strategy_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="strategy_identity")
    _reject_unknown_fields(payload, allowed={"strategy_id", "strategy_builder", "baseline_variant_id"}, name="strategy_identity")
    strategy_builder = _required_text(payload, "strategy_builder")
    if strategy_builder != "swing_trend_filtered_pullback":
        raise ValueError("strategy_identity.strategy_builder must be swing_trend_filtered_pullback.")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": strategy_builder,
        "baseline_variant_id": _required_text(payload, "baseline_variant_id"),
    }


def _validate_strategy_rule_definitions(value: Any) -> list[dict[str, str]]:
    items = _required_non_empty_list(value, name="strategy_rule_definitions")
    normalized: list[dict[str, str]] = []
    seen_rule_ids: set[str] = set()
    for item in items:
        payload = _required_mapping(item, name="strategy_rule_definition")
        _reject_unknown_fields(payload, allowed={"rule_id", "rule_role", "description"}, name="strategy_rule_definition")
        rule_id = _required_text(payload, "rule_id")
        if rule_id in seen_rule_ids:
            raise ValueError("strategy_rule_definitions.rule_id values must be unique.")
        seen_rule_ids.add(rule_id)
        normalized.append(
            {
                "rule_id": rule_id,
                "rule_role": _required_text(payload, "rule_role"),
                "description": _required_text(payload, "description"),
            }
        )
    return sorted(normalized, key=lambda item: item["rule_id"])


def _validate_robustness_review_inputs(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_review_inputs")
    _reject_unknown_fields(
        payload,
        allowed={"parameter_schema", "evaluation_window_metadata", "experiment_trial_metadata", "validated_knihomol_evidence", "robustness_policy"},
        name="robustness_review_inputs",
    )
    return {
        "parameter_schema": _required_mapping(payload.get("parameter_schema"), name="parameter_schema"),
        "evaluation_window_metadata": _required_mapping(payload.get("evaluation_window_metadata"), name="evaluation_window_metadata"),
        "experiment_trial_metadata": _required_mapping(payload.get("experiment_trial_metadata"), name="experiment_trial_metadata"),
        "validated_knihomol_evidence": _required_mapping(payload.get("validated_knihomol_evidence"), name="validated_knihomol_evidence"),
        "robustness_policy": _required_mapping(payload.get("robustness_policy"), name="robustness_policy"),
    }


def _validate_ablation_inputs(value: Any, *, expected_strategy_id: str) -> dict[str, Any]:
    payload = _required_mapping(value, name="ablation_inputs")
    _reject_unknown_fields(payload, allowed={"baseline_variant", "ablated_variants", "ablation_policy"}, name="ablation_inputs")
    baseline_variant = _required_mapping(payload.get("baseline_variant"), name="baseline_variant")
    if _required_text(baseline_variant, "strategy_id") != expected_strategy_id:
        raise ValueError("ablation_inputs.baseline_variant.strategy_id must match strategy_identity.strategy_id.")
    ablated_variants = _required_non_empty_list(payload.get("ablated_variants"), name="ablated_variants")
    for item in ablated_variants:
        entry = _required_mapping(item, name="ablated_variant")
        if _required_text(entry, "strategy_id") != expected_strategy_id:
            raise ValueError("ablation_inputs.ablated_variants.strategy_id must match strategy_identity.strategy_id.")
    return {
        "baseline_variant": baseline_variant,
        "ablated_variants": ablated_variants,
        "ablation_policy": _required_mapping(payload.get("ablation_policy"), name="ablation_policy"),
    }


def _validate_parameter_stability_inputs(value: Any) -> list[dict[str, Any]]:
    items = _required_non_empty_list(value, name="parameter_stability_inputs")
    normalized: list[dict[str, Any]] = []
    seen_parameter_names: set[str] = set()
    for item in items:
        payload = _required_mapping(item, name="parameter_stability_input")
        _reject_unknown_fields(
            payload,
            allowed={"version", "parameter_name", "baseline_value", "one_dimensional_results", "pair_interactions", "stability_policy"},
            name="parameter_stability_input",
        )
        parameter_name = _required_text(payload, "parameter_name")
        if parameter_name in seen_parameter_names:
            raise ValueError("parameter_stability_inputs.parameter_name values must be unique.")
        seen_parameter_names.add(parameter_name)
        normalized.append(payload)
    return normalized


def _validate_robustness_decision_inputs(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="robustness_decision_inputs")
    _reject_unknown_fields(
        payload,
        allowed={
            "walk_forward_fold_evidence",
            "effective_sample_metadata",
            "trial_count_metadata",
            "deflated_sharpe_result",
            "pbo_cscv_result",
            "drawdown_stress_result",
            "complexity_variants",
            "decision_policy",
        },
        name="robustness_decision_inputs",
    )
    return {
        "walk_forward_fold_evidence": _required_mapping(payload.get("walk_forward_fold_evidence"), name="walk_forward_fold_evidence"),
        "effective_sample_metadata": _required_mapping(payload.get("effective_sample_metadata"), name="effective_sample_metadata"),
        "trial_count_metadata": _required_mapping(payload.get("trial_count_metadata"), name="trial_count_metadata"),
        "deflated_sharpe_result": _required_mapping(payload.get("deflated_sharpe_result"), name="deflated_sharpe_result"),
        "pbo_cscv_result": _required_mapping(payload.get("pbo_cscv_result"), name="pbo_cscv_result"),
        "drawdown_stress_result": _required_mapping(payload.get("drawdown_stress_result"), name="drawdown_stress_result"),
        "complexity_variants": _required_mapping(payload.get("complexity_variants"), name="complexity_variants"),
        "decision_policy": _required_mapping(payload.get("decision_policy"), name="decision_policy"),
    }


def _reviewed_robustness_context(
    *,
    validated: dict[str, Any],
    robustness_decision_result: dict[str, Any],
    parameter_stability_results: list[dict[str, Any]],
    ablation_result: dict[str, Any],
) -> dict[str, Any]:
    blocking_reasons = list(robustness_decision_result["blocking_reasons"])
    decision_inputs = validated["robustness_decision_inputs"]
    strategy_rule_definitions = {item["rule_id"]: item for item in validated["strategy_rule_definitions"]}
    required_risk_safety_rule_ids = {
        item["removed_rule"]["rule_id"]
        for item in ablation_result["ablation_results"]
        if item["classification"] == "REQUIRED_FOR_RISK_SAFETY"
    }
    return {
        "decision_status": robustness_decision_result["decision_status"],
        "selected_variant_id": robustness_decision_result["selected_variant_id"] or robustness_decision_result["recommended_variant_id"],
        "recommended_variant_id": robustness_decision_result["recommended_variant_id"] or robustness_decision_result["selected_variant_id"],
        "rejected_variants": robustness_decision_result["rejected_variants"],
        "ablation_classifications": robustness_decision_result["ablation_classifications"],
        "required_risk_safety_rules": [
            strategy_rule_definitions[rule_id]
            for rule_id in sorted(required_risk_safety_rule_ids)
            if rule_id in strategy_rule_definitions
        ],
        "parameter_stability_classifications": [
            {
                "parameter_name": item["parameter_name"],
                "stability_classification": item["stability_classification"],
            }
            for item in parameter_stability_results
        ],
        "weak_parameters": robustness_decision_result["weak_parameters"],
        "isolated_spike_findings": sorted(item for item in blocking_reasons if item.startswith("isolated_spike_detected:")),
        "walk_forward_failures": robustness_decision_result["fold_failures"],
        "effective_sample_findings": {
            "available": bool(decision_inputs["effective_sample_metadata"].get("available", True)),
            "passed": bool(decision_inputs["effective_sample_metadata"]["passed"]),
            "effective_sample_size": int(decision_inputs["effective_sample_metadata"]["effective_sample_size"]),
            "minimum_required": int(decision_inputs["effective_sample_metadata"]["minimum_required"]),
            "blocking_reasons": sorted(item for item in blocking_reasons if item.startswith("effective_sample") or item.startswith("missing_effective_sample")),
        },
        "trial_accounting_findings": {
            "total_trials": int(decision_inputs["trial_count_metadata"]["total_trials"]),
            "complete_accounting": bool(decision_inputs["trial_count_metadata"]["complete_accounting"]),
            "bounded_search": bool(decision_inputs["trial_count_metadata"]["bounded_search"]),
            "selection_mode": str(decision_inputs["trial_count_metadata"]["selection_mode"]),
            "blocking_reasons": sorted(
                item for item in blocking_reasons if item in {"incomplete_trial_accounting", "unbounded_search_not_allowed"}
            ),
        },
        "deflated_sharpe_findings": {
            "available": bool(decision_inputs["deflated_sharpe_result"]["available"]),
            "passed": bool(decision_inputs["deflated_sharpe_result"]["passed"]),
            "observed_value": float(decision_inputs["deflated_sharpe_result"]["observed_value"]),
            "minimum_required": float(decision_inputs["deflated_sharpe_result"]["minimum_required"]),
            "blocking_reasons": sorted(item for item in blocking_reasons if item.startswith("deflated_sharpe") or item.startswith("missing_deflated_sharpe")),
        },
        "pbo_cscv_findings": {
            "available": bool(decision_inputs["pbo_cscv_result"]["available"]),
            "passed": bool(decision_inputs["pbo_cscv_result"]["passed"]),
            "observed_value": float(decision_inputs["pbo_cscv_result"]["observed_value"]),
            "maximum_allowed": float(decision_inputs["pbo_cscv_result"]["maximum_allowed"]),
            "blocking_reasons": sorted(item for item in blocking_reasons if item.startswith("pbo") or item.startswith("missing_pbo")),
        },
        "selection_bias_findings": robustness_decision_result["selection_bias_findings"],
        "drawdown_findings": robustness_decision_result["drawdown_findings"],
        "complexity_findings": robustness_decision_result["complexity_findings"],
        "knowledge_note_ids_used": robustness_decision_result["knowledge_note_ids_used"],
    }


def _robustness_review_gate_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": result["version"],
        "robustness_status": result["robustness_status"],
        "required_parameter_checks": result["required_parameter_checks"],
        "required_walk_forward_checks": result["required_walk_forward_checks"],
        "required_selection_bias_checks": result["required_selection_bias_checks"],
        "required_drawdown_checks": result["required_drawdown_checks"],
        "complexity_budget": result["complexity_budget"],
        "blocking_reasons": result["blocking_reasons"],
        "knowledge_note_ids_used": result["knowledge_note_ids_used"],
        "production_runtime_supported": result["production_runtime_supported"],
    }


def _ablation_gate_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": result["version"],
        "ablation_results": result["ablation_results"],
        "production_runtime_supported": result["production_runtime_supported"],
    }


def _parameter_stability_gate_views(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "version": item["version"],
            "parameter_name": item["parameter_name"],
            "baseline_value": item["baseline_value"],
            "stability_classification": item["stability_classification"],
            "production_runtime_supported": item["production_runtime_supported"],
        }
        for item in results
    ]


def _baseline_review_gate_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": result["version"],
        "candidate_id": result["candidate_id"],
        "candidate_sha256": result["candidate_sha256"],
        "symbol": result["symbol"],
        "final_review_status": result["final_review_status"],
    }


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


def _required_non_empty_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


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
