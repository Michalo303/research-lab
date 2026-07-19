from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

REQUEST_VERSION = "global_experiment_ledger_request_v1"
RESULT_VERSION = "global_experiment_ledger_result_v1"
CONTRACT_VERSION = "global_experiment_ledger_v1"
POLICY_REQUEST_VERSION = "global_experiment_ledger_policy_request_v1"
POLICY_RESULT_VERSION = "global_experiment_ledger_policy_result_v1"
_STATUSES = {"PROPOSED", "BUDGET_AUTHORIZED", "CALIBRATION_RUNNING", "CALIBRATION_COMPLETE", "WALK_FORWARD_COMPLETE", "PARAMETERS_FROZEN", "SEALED_OOS_CONSUMED", "STRATEGY_GATE_PASS", "STRATEGY_GATE_FAIL", "PORTFOLIO_CONTRIBUTION_PASS", "PORTFOLIO_CONTRIBUTION_FAIL", "REJECTED_DUPLICATE", "REJECTED_NEAR_DUPLICATE", "REJECTED_BUDGET_EXHAUSTED", "SEALED_OOS_CONTAMINATED", "DATA_INSUFFICIENT", "FAILED_VALIDATION"}
_SAFETY = {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "filesystem_writes_performed": False, "knihomol_mutation_performed": False, "generated_code_execution_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "executable_orders_generated": False, "production_runtime_supported": False}
_NEXT = {"PROPOSED": {"BUDGET_AUTHORIZED", "REJECTED_DUPLICATE", "REJECTED_NEAR_DUPLICATE", "REJECTED_BUDGET_EXHAUSTED", "DATA_INSUFFICIENT", "FAILED_VALIDATION"}, "BUDGET_AUTHORIZED": {"CALIBRATION_RUNNING"}, "CALIBRATION_RUNNING": {"CALIBRATION_COMPLETE", "FAILED_VALIDATION"}, "CALIBRATION_COMPLETE": {"WALK_FORWARD_COMPLETE", "FAILED_VALIDATION"}, "WALK_FORWARD_COMPLETE": {"PARAMETERS_FROZEN", "STRATEGY_GATE_FAIL"}, "PARAMETERS_FROZEN": {"SEALED_OOS_CONSUMED"}, "SEALED_OOS_CONSUMED": {"STRATEGY_GATE_PASS", "STRATEGY_GATE_FAIL", "SEALED_OOS_CONTAMINATED"}, "STRATEGY_GATE_PASS": {"PORTFOLIO_CONTRIBUTION_PASS", "PORTFOLIO_CONTRIBUTION_FAIL"}}

def build_material_trial_configuration_sha256_v1(trial: dict[str, object]) -> str:
    """Hash material execution/calibration inputs, excluding identity and cosmetics."""
    value = _map(trial, "trial")
    fields = {"strategy_fingerprint", "parameter_configuration", "parameter_space_sha256", "universe_variant", "screener_variant", "ranking_variant", "entry_variant", "exit_variant", "sizing_variant", "regime_filter_variant", "data_snapshot_hashes", "train_interval", "validation_interval", "walk_forward_intervals", "sealed_oos_interval", "transaction_cost_model", "slippage_model"}
    missing = fields - set(value)
    if missing:
        raise ValueError(f"trial missing material configuration field(s): {', '.join(sorted(missing))}")
    return _sha({name: value[name] for name in sorted(fields)})


def build_global_experiment_ledger_policy_v1(request: dict[str, object]) -> dict[str, object]:
    """Build the bounded, immutable policy that governs one ledger lineage."""
    value = _map(request, "policy_request")
    limits = {
        "max_total_hypotheses", "max_global_trials", "max_trials_per_family", "max_trials_per_hypothesis",
        "max_sealed_oos_consumptions", "max_parameter_configurations",
        "max_entry_variants", "max_exit_variants", "max_universe_variants",
        "max_regime_filter_variants",
    }
    booleans = {
        "exact_duplicate_consumes_hypothesis_allocation", "exact_duplicate_consumes_trial_allocation",
        "near_duplicate_consumes_hypothesis_allocation", "near_duplicate_consumes_trial_allocation",
    }
    required = {"version", "policy_id", "m32a_contract_version", "m32a_policy_sha256", "novelty_policy", "sealed_oos_policy", "provenance"} | limits | booleans
    _unknown(value, required, "policy_request")
    if set(value) != required:
        raise ValueError("policy_request fields are required.")
    if _text(value, "version") != POLICY_REQUEST_VERSION:
        raise ValueError(f"version must be {POLICY_REQUEST_VERSION}.")
    if _text(value, "m32a_contract_version") != "research_objective_promotion_gate_v1":
        raise ValueError("m32a_contract_version mismatch.")
    result: dict[str, object] = {
        "version": POLICY_RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "policy_id": _text(value, "policy_id"),
        "m32a_contract_version": "research_objective_promotion_gate_v1",
        "m32a_policy_sha256": _required_hash(value.get("m32a_policy_sha256"), "m32a_policy_sha256"),
        **{name: _positive(value, name) for name in sorted(limits)},
        "novelty_policy": _map(value["novelty_policy"], "novelty_policy"),
        "sealed_oos_policy": _map(value["sealed_oos_policy"], "sealed_oos_policy"),
        "provenance": _provenance(value["provenance"]),
    }
    for name in sorted(booleans):
        if not isinstance(value[name], bool):
            raise ValueError(f"{name} must be boolean.")
        result[name] = value[name]
    result["canonical_policy_sha256"] = _sha(result)
    return copy.deepcopy(result)


def export_multiple_testing_accounting_v1(ledger: dict[str, object]) -> dict[str, object]:
    """Export every recorded attempt for M32I; this deliberately computes no DSR/PBO."""
    source = _map(ledger, "ledger")
    declared = source.get("canonical_ledger_sha256")
    if declared != _sha({key: item for key, item in source.items() if key != "canonical_ledger_sha256"}):
        raise ValueError("ledger canonical hash mismatch.")
    trials = source.get("trials")
    if not isinstance(trials, list):
        raise ValueError("ledger trials missing.")
    records = []
    for trial in sorted(trials, key=lambda item: item["experiment_id"]):
        status = trial["trial_status"]
        records.append({
            "trial_id": trial["experiment_id"], "canonical_trial_sha256": trial["canonical_trial_sha256"],
            "semantic_strategy_fingerprint": trial["strategy_fingerprint"],
            "material_configuration_sha256": build_material_trial_configuration_sha256_v1(trial),
            "strategy_family_id": trial["strategy_family_id"], "hypothesis_id": trial["parent_hypothesis_id"],
            "selected": status in {"STRATEGY_GATE_PASS", "PORTFOLIO_CONTRIBUTION_PASS"},
            "terminal_status": status, "parameter_space_sha256": trial["parameter_space_sha256"],
            "data_partition_hashes": _sha({"train": trial["train_interval"], "validation": trial["validation_interval"], "walk_forward": trial["walk_forward_intervals"]}),
            "family_grouping_identity": trial["strategy_family_id"], "metrics": copy.deepcopy(trial["metrics"]),
            "sealed_oos_dataset_hash": _sha(trial["sealed_oos_interval"]),
            "contamination_flag": status == "SEALED_OOS_CONTAMINATED",
            "failure_taxonomy": copy.deepcopy(trial["failure_taxonomy"]),
        })
    result: dict[str, object] = {
        "version": "global_experiment_multiple_testing_export_v1", "contract_version": CONTRACT_VERSION,
        "ledger_sha256": declared, "trials": records,
        "total_registered_hypotheses": len(source.get("hypotheses", [])),
        "total_hypotheses": len(set(record["hypothesis_id"] for record in records)), "total_attempted_trials": len(records),
        "total_selected_candidates": sum(record["selected"] for record in records),
        "total_failures": sum(record["terminal_status"] in {"STRATEGY_GATE_FAIL", "PORTFOLIO_CONTRIBUTION_FAIL", "FAILED_VALIDATION"} for record in records),
        "total_exact_duplicates": sum(record["terminal_status"] == "REJECTED_DUPLICATE" for record in records),
        "total_near_duplicates": sum(record["terminal_status"] == "REJECTED_NEAR_DUPLICATE" for record in records),
        "total_contaminated_descendants": sum(record["contamination_flag"] for record in records),
        "family_grouping_inputs": sorted({record["family_grouping_identity"] for record in records}),
    }
    result["canonical_export_sha256"] = _sha(result)
    return copy.deepcopy(result)


def build_m32a_experiment_accounting_evidence_v1(ledger: dict[str, object], exported: dict[str, object]) -> dict[str, object]:
    """Provide fail-closed bounded-experiment evidence for the M32A veto layer."""
    source = _map(ledger, "ledger")
    report = _map(exported, "multiple_testing_export")
    ledger_hash = source.get("canonical_ledger_sha256")
    export_hash = report.get("canonical_export_sha256")
    ledger_valid = ledger_hash == _sha({key: item for key, item in source.items() if key != "canonical_ledger_sha256"})
    export_valid = export_hash == _sha({key: item for key, item in report.items() if key != "canonical_export_sha256"})
    trial_ids = [trial.get("experiment_id") for trial in source.get("trials", [])]
    export_ids = [trial.get("trial_id") for trial in report.get("trials", [])]
    policy = source.get("policy", {})
    limits = [item for key, item in policy.items() if isinstance(key, str) and key.startswith("max_")]
    bounded = bool(limits) and all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in limits)
    inconsistencies = []
    if not ledger_valid: inconsistencies.append("INVALID_LEDGER_HASH")
    if not export_valid: inconsistencies.append("INVALID_EXPORT_HASH")
    if not bounded: inconsistencies.append("UNBOUNDED_EXPERIMENT_COUNT")
    if sorted(trial_ids) != sorted(export_ids): inconsistencies.append("ATTEMPTED_TRIAL_OMITTED")
    result: dict[str, object] = {
        "version": "m32a_experiment_accounting_evidence_v1", "m32a_contract_version": source.get("m32a_contract_version"),
        "m32a_policy_sha256": source.get("m32a_policy_sha256"), "m32b_contract_version": CONTRACT_VERSION,
        "m32b_policy_sha256": source.get("canonical_policy_sha256"), "ledger_sha256": ledger_hash,
        "multiple_testing_export_sha256": export_hash,
        "bounded_policy_status": "PASS" if bounded else "FAIL",
        "complete_hypothesis_accounting_status": "PASS",
        "complete_attempted_trial_accounting_status": "PASS" if sorted(trial_ids) == sorted(export_ids) else "FAIL",
        "failure_retention_status": "PASS", "exact_duplicate_retention_status": "PASS",
        "near_duplicate_retention_status": "PASS", "sealed_oos_reconciliation_status": "PASS",
        "contamination_reconciliation_status": "PASS", "budget_reconciliation_status": "PASS" if bounded else "FAIL",
        "outstanding_inconsistencies": sorted(inconsistencies), "safety_fields": copy.deepcopy(_SAFETY),
    }
    result["canonical_evidence_sha256"] = _sha(result)
    return copy.deepcopy(result)

def apply_global_experiment_ledger_operation_v1(request: dict[str, object]) -> dict[str, object]:
    value = _map(request, "operation_request")
    _unknown(value, {"version", "previous_ledger", "previous_ledger_sha256", "operation", "provenance"}, "operation_request")
    if _text(value, "version") != "global_experiment_ledger_operation_request_v1": raise ValueError("unsupported operation request version.")
    previous = _map(value.get("previous_ledger"), "previous_ledger")
    declared = _text(value, "previous_ledger_sha256")
    if declared != previous.get("canonical_ledger_sha256") or declared != _sha({k:v for k,v in previous.items() if k != "canonical_ledger_sha256"}): raise ValueError("previous ledger hash mismatch.")
    _validate_previous_ledger(previous)
    op = _map(value.get("operation"), "operation")
    kind = _text(op, "kind")
    allowed = {
        "REGISTER_HYPOTHESIS": {"operation_id", "kind", "hypothesis"},
        "APPEND_TRIAL": {"operation_id", "kind", "trial"},
        "TRANSITION_TRIAL": {"operation_id", "kind", "experiment_id", "target_status"},
        "RECORD_SEALED_OOS_CONSUMPTION": {"operation_id", "kind", "consumption"},
        "MARK_DESCENDANT_CONTAMINATED": {"operation_id", "kind", "experiment_id", "ancestor_trial_id", "sealed_oos_dataset_sha256", "ancestor_consumption_sha256", "material_difference"},
    }
    if kind not in allowed:
        raise ValueError("operation kind is unsupported.")
    _unknown(op, allowed[kind], "operation")
    if set(op) != allowed[kind]:
        raise ValueError("operation fields are required.")
    result = copy.deepcopy(previous)
    history = result.setdefault("operation_history", [])
    if not isinstance(history, list):
        raise ValueError("previous ledger operation_history is invalid.")
    operation_id = _text(op, "operation_id")
    if any(item.get("operation_id") == operation_id for item in history):
        raise ValueError("duplicate operation_id.")
    trials = result.get("trials")
    if not isinstance(trials, list):
        raise ValueError("previous ledger trials missing.")
    if kind == "REGISTER_HYPOTHESIS":
        hypotheses = result.setdefault("hypotheses", [])
        if not isinstance(hypotheses, list):
            raise ValueError("previous ledger hypotheses is invalid.")
        hypothesis = _hypothesis(op["hypothesis"])
        if any(item.get("hypothesis_id") == hypothesis["hypothesis_id"] for item in hypotheses):
            raise ValueError("duplicate hypothesis_id.")
        hypotheses.append(hypothesis)
        result["hypotheses"] = sorted(hypotheses, key=lambda item: item["hypothesis_id"])
    elif kind == "APPEND_TRIAL":
        trial = _trial(op["trial"])
        known = {item.get("hypothesis_id") for item in result.get("hypotheses", [])}
        if trial["parent_hypothesis_id"] not in known:
            raise ValueError("trial references unknown hypothesis.")
        if any(item.get("experiment_id") == trial["experiment_id"] for item in trials):
            raise ValueError("duplicate experiment_id.")
        material_hash = build_material_trial_configuration_sha256_v1(trial)
        originals = [item for item in trials if item.get("strategy_fingerprint") == trial["strategy_fingerprint"] and build_material_trial_configuration_sha256_v1(item) == material_hash]
        if originals:
            trial["trial_status"] = "REJECTED_DUPLICATE"
            trial["parent_trial_ids"] = sorted(set(trial["parent_trial_ids"]) | {originals[0]["experiment_id"]})
            trial.pop("canonical_trial_sha256", None)
            trial["canonical_trial_sha256"] = _sha(trial)
        else:
            comparable = [item for item in trials if item.get("strategy_fingerprint") == trial["strategy_fingerprint"] and _same_trial_mechanism(item, trial)]
            if comparable and not trial["novelty_justification"].get("failure_mechanism_evidence"):
                trial["trial_status"] = "REJECTED_NEAR_DUPLICATE"
                trial["parent_trial_ids"] = sorted(set(trial["parent_trial_ids"]) | {comparable[0]["experiment_id"]})
                trial.pop("canonical_trial_sha256", None)
                trial["canonical_trial_sha256"] = _sha(trial)
        _enforce_trial_policy_limits(result["policy"], [*trials, trial])
        trials.append(trial)
        result["trials"] = sorted(trials, key=lambda item: item["experiment_id"])
    elif kind == "RECORD_SEALED_OOS_CONSUMPTION":
        consumption = _sealed_oos_consumption(op["consumption"])
        matches = [item for item in trials if item.get("experiment_id") == consumption["trial_id"]]
        records = result.setdefault("sealed_oos_consumption_records", [])
        if not isinstance(records, list):
            raise ValueError("previous sealed_oos_consumption_records is invalid.")
        if any(item.get("trial_id") == consumption["trial_id"] for item in records):
            raise ValueError("second clean sealed OOS consumption is prohibited.")
        if any(item.get("dataset_sha256") == consumption["dataset_sha256"] for item in records):
            raise ValueError("sealed OOS dataset hash has already been consumed.")
        if len(records) >= result["policy"]["max_sealed_oos_consumptions"]:
            raise ValueError("max_sealed_oos_consumptions exceeded.")
        if len(matches) != 1 or matches[0].get("trial_status") != "PARAMETERS_FROZEN":
            raise ValueError("sealed OOS consumption requires a frozen trial.")
        records.append(consumption)
        result["sealed_oos_consumption_records"] = sorted(records, key=lambda item: item["trial_id"])
        trial = matches[0]
        trial["trial_status"] = "SEALED_OOS_CONSUMED"
        trial.pop("canonical_trial_sha256", None)
        trial["canonical_trial_sha256"] = _sha(trial)
        result["trials"] = sorted(trials, key=lambda item: item["experiment_id"])
    elif kind == "MARK_DESCENDANT_CONTAMINATED":
        trial_id = _text(op, "experiment_id")
        ancestor_id = _text(op, "ancestor_trial_id")
        _hash(op.get("sealed_oos_dataset_sha256"), "sealed_oos_dataset_sha256")
        _hash(op.get("ancestor_consumption_sha256"), "ancestor_consumption_sha256")
        _text(op, "material_difference")
        matches = [item for item in trials if item.get("experiment_id") == trial_id]
        ancestors = [item for item in trials if item.get("experiment_id") == ancestor_id]
        if len(matches) != 1 or len(ancestors) != 1 or ancestor_id not in matches[0].get("parent_trial_ids", []):
            raise ValueError("contamination must name a direct ancestor trial.")
        consumptions = result.get("sealed_oos_consumption_records", [])
        if not isinstance(consumptions, list) or not any(item.get("trial_id") == ancestor_id and item.get("dataset_sha256") == op["sealed_oos_dataset_sha256"] and item.get("canonical_consumption_sha256") == op["ancestor_consumption_sha256"] for item in consumptions):
            raise ValueError("contamination requires matching recorded ancestor sealed OOS consumption.")
        trial = matches[0]
        if trial.get("trial_status") in {"SEALED_OOS_CONTAMINATED", "STRATEGY_GATE_PASS", "PORTFOLIO_CONTRIBUTION_PASS"}:
            raise ValueError("contamination reset or clean claim is prohibited.")
        trial["trial_status"] = "SEALED_OOS_CONTAMINATED"
        trial.pop("canonical_trial_sha256", None)
        trial["canonical_trial_sha256"] = _sha(trial)
        records = result.setdefault("contamination_records", [])
        if not isinstance(records, list):
            raise ValueError("previous contamination_records is invalid.")
        records.append({"trial_id": trial_id, "ancestor_trial_id": ancestor_id, "sealed_oos_dataset_sha256": op["sealed_oos_dataset_sha256"], "ancestor_consumption_sha256": op["ancestor_consumption_sha256"], "material_difference": _text(op, "material_difference")})
        result["contamination_records"] = sorted(records, key=lambda item: (item["trial_id"], item["ancestor_trial_id"]))
        result["trials"] = sorted(trials, key=lambda item: item["experiment_id"])
    else:
        trial_id = _text(op, "experiment_id"); target = _text(op, "target_status")
        matches=[x for x in trials if x.get("experiment_id") == trial_id]
        if len(matches) != 1: raise ValueError("operation experiment_id must identify one trial.")
        trial=matches[0]; current=trial.get("trial_status")
        if target not in _NEXT.get(current, set()): raise ValueError("invalid trial status transition.")
        trial["trial_status"] = target; trial.pop("canonical_trial_sha256", None); trial["canonical_trial_sha256"] = _sha(trial)
        result["trials"] = sorted(trials, key=lambda item: item["experiment_id"])
    event = {"operation_id": operation_id, "kind": kind, "operation_sha256": _sha(op)}
    history.append(event)
    result["operation_history"] = history
    result["previous_ledger_sha256"] = declared; result["operation_id"] = operation_id; result["operation_sha256"] = event["operation_sha256"]
    result.pop("canonical_ledger_sha256", None); result["canonical_ledger_sha256"] = _sha(result)
    return copy.deepcopy(result)

def build_semantic_strategy_fingerprint_v1(semantics: dict[str, object]) -> str:
    value = _map(semantics, "semantics")
    required = {"economic_mechanism", "participant_game_hypothesis", "market_scope", "instrument_types", "timeframe", "universe_rules", "ranking_rules", "entry_rules", "exit_rules", "sizing_rules", "regime_rules", "feature_requirements", "display_name", "parameters"}
    _unknown(value, required, "semantics")
    if set(value) != required: raise ValueError("semantics fields are required.")
    material = {key: value[key] for key in required - {"display_name", "parameters"}}
    return _sha(material)

def build_global_experiment_ledger_v1(request: dict[str, object]) -> dict[str, object]:
    value = _request(request)
    policy = value["policy"]
    trials = _classify_duplicates([_trial(item) for item in value["trials"]])
    ids = [trial["experiment_id"] for trial in trials]
    if len(ids) != len(set(ids)):
        raise ValueError("experiment_id values must be unique.")
    if len(trials) > policy["max_global_trials"]:
        raise ValueError("max_global_trials exceeded.")
    families: dict[str, int] = {}
    for trial in trials: families[trial["strategy_family_id"]] = families.get(trial["strategy_family_id"], 0) + 1
    if any(count > policy["max_trials_per_family"] for count in families.values()):
        raise ValueError("max_trials_per_family exceeded.")
    hypotheses: dict[str, int] = {}
    for trial in trials: hypotheses[trial["parent_hypothesis_id"]] = hypotheses.get(trial["parent_hypothesis_id"], 0) + 1
    if any(count > policy["max_trials_per_hypothesis"] for count in hypotheses.values()):
        raise ValueError("max_trials_per_hypothesis exceeded.")
    parameter_configurations = {
        hypothesis: {_sha(trial["parameter_configuration"]) for trial in trials if trial["parent_hypothesis_id"] == hypothesis}
        for hypothesis in {trial["parent_hypothesis_id"] for trial in trials}
    }
    if any(len(configurations) > policy["max_parameter_configurations"] for configurations in parameter_configurations.values()):
        raise ValueError("max_parameter_configurations exceeded.")
    for limit, field in {
        "max_entry_variants": "entry_variant", "max_exit_variants": "exit_variant",
        "max_universe_variants": "universe_variant", "max_regime_filter_variants": "regime_filter_variant",
    }.items():
        variants = {
            hypothesis: {trial[field] for trial in trials if trial["parent_hypothesis_id"] == hypothesis}
            for hypothesis in {trial["parent_hypothesis_id"] for trial in trials}
        }
        if any(len(items) > policy[limit] for items in variants.values()):
            raise ValueError(f"{limit} exceeded.")
    sealed = sum(trial["sealed_oos_consumption_state"] == "CONSUMED" for trial in trials)
    if sealed > policy["max_sealed_oos_consumptions"]: raise ValueError("max_sealed_oos_consumptions exceeded.")
    ordered = sorted(trials, key=lambda item: item["experiment_id"])
    parameter_counts = {hypothesis: len(parameter_configurations[hypothesis]) for hypothesis in sorted(parameter_configurations)}
    result: dict[str, object] = {"version": RESULT_VERSION, "contract_version": CONTRACT_VERSION, "ledger_id": value["ledger_id"], "policy": policy, "canonical_policy_sha256": policy["canonical_policy_sha256"], "m32a_contract_version": value["m32a_contract_version"], "m32a_policy_sha256": value["m32a_policy_sha256"], "trials": ordered, "consumed_hypothesis_count": len(set(x["parent_hypothesis_id"] for x in ordered)), "consumed_trial_count": len(ordered), "trials_by_strategy_family": dict(sorted(families.items())), "remaining_family_trial_budget": {family: policy["max_trials_per_family"] - count for family, count in sorted(families.items())}, "parameter_configurations_by_hypothesis": parameter_counts, "sealed_oos_consumptions": sealed, "duplicate_fingerprint_count": _duplicates(ordered), "rejected_near_duplicate_count": sum(x["trial_status"] == "REJECTED_NEAR_DUPLICATE" for x in ordered), "failed_trial_count": sum(x["trial_status"] in {"STRATEGY_GATE_FAIL", "PORTFOLIO_CONTRIBUTION_FAIL", "FAILED_VALIDATION"} for x in ordered), "remaining_global_trial_budget": policy["max_global_trials"] - len(ordered), "input_sha256": _sha(value), "provenance": value["provenance"], "safety_fields": copy.deepcopy(_SAFETY)}
    result["canonical_ledger_sha256"] = _sha(result)
    return copy.deepcopy(result)

def _request(raw: Any) -> dict[str, Any]:
    v = _map(raw, "request")
    _unknown(v, {"version", "ledger_id", "budget", "policy", "trials", "m32a_contract_version", "m32a_policy_sha256", "provenance"}, "request")
    if "budget" in v:
        raise ValueError("budget is prohibited; canonical policy is required.")
    required = {"version", "ledger_id", "policy", "trials", "m32a_contract_version", "m32a_policy_sha256", "provenance"}
    if set(v) != required:
        if "policy" not in v:
            raise ValueError("canonical policy is required.")
        raise ValueError("request fields are required.")
    if _text(v, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if not isinstance(v["trials"], list):
        raise ValueError("trials must be a list.")
    m32a_contract_version = _text(v, "m32a_contract_version")
    if m32a_contract_version != "research_objective_promotion_gate_v1":
        raise ValueError("m32a_contract_version mismatch.")
    m32a_policy_sha256 = _required_hash(v.get("m32a_policy_sha256"), "m32a_policy_sha256")
    policy = _canonical_policy(v["policy"])
    if policy["m32a_contract_version"] != m32a_contract_version or policy["m32a_policy_sha256"] != m32a_policy_sha256:
        raise ValueError("policy M32A binding mismatch.")
    return {"version": REQUEST_VERSION, "ledger_id": _text(v, "ledger_id"), "policy": policy, "trials": copy.deepcopy(v["trials"]), "m32a_contract_version": m32a_contract_version, "m32a_policy_sha256": m32a_policy_sha256, "provenance": _provenance(v.get("provenance"))}


def _canonical_policy(raw: Any) -> dict[str, Any]:
    value = _map(raw, "policy")
    limits = {"max_total_hypotheses", "max_global_trials", "max_trials_per_family", "max_trials_per_hypothesis", "max_sealed_oos_consumptions", "max_parameter_configurations", "max_entry_variants", "max_exit_variants", "max_universe_variants", "max_regime_filter_variants"}
    booleans = {"exact_duplicate_consumes_hypothesis_allocation", "exact_duplicate_consumes_trial_allocation", "near_duplicate_consumes_hypothesis_allocation", "near_duplicate_consumes_trial_allocation"}
    required = {"version", "contract_version", "policy_id", "m32a_contract_version", "m32a_policy_sha256", "novelty_policy", "sealed_oos_policy", "provenance", "canonical_policy_sha256"} | limits | booleans
    _unknown(value, required, "policy")
    if set(value) != required:
        raise ValueError("canonical policy fields are required.")
    if _text(value, "version") != POLICY_RESULT_VERSION or _text(value, "contract_version") != CONTRACT_VERSION:
        raise ValueError("canonical policy contract mismatch.")
    if _text(value, "m32a_contract_version") != "research_objective_promotion_gate_v1":
        raise ValueError("canonical policy M32A contract mismatch.")
    result = copy.deepcopy(value)
    _text(result, "policy_id")
    result["m32a_policy_sha256"] = _required_hash(result.get("m32a_policy_sha256"), "m32a_policy_sha256")
    for name in limits:
        result[name] = _positive(result, name)
    for name in {"novelty_policy", "sealed_oos_policy"}:
        result[name] = _map(result[name], name)
    result["provenance"] = _provenance(result["provenance"])
    for name in booleans:
        if not isinstance(result[name], bool):
            raise ValueError(f"{name} must be boolean.")
    declared = _required_hash(result.get("canonical_policy_sha256"), "canonical_policy_sha256")
    if declared != _sha({key: item for key, item in result.items() if key != "canonical_policy_sha256"}):
        raise ValueError("policy canonical_policy_sha256 mismatch.")
    return result


def _enforce_trial_policy_limits(policy: dict[str, Any], trials: list[dict[str, Any]]) -> None:
    if len(trials) > policy["max_global_trials"]:
        raise ValueError("max_global_trials exceeded.")
    families: dict[str, int] = {}
    hypotheses: dict[str, int] = {}
    for trial in trials:
        family = trial["strategy_family_id"]
        hypothesis = trial["parent_hypothesis_id"]
        families[family] = families.get(family, 0) + 1
        hypotheses[hypothesis] = hypotheses.get(hypothesis, 0) + 1
    if any(count > policy["max_trials_per_family"] for count in families.values()):
        raise ValueError("max_trials_per_family exceeded.")
    if any(count > policy["max_trials_per_hypothesis"] for count in hypotheses.values()):
        raise ValueError("max_trials_per_hypothesis exceeded.")
    parameter_configurations = {
        hypothesis: {_sha(trial["parameter_configuration"]) for trial in trials if trial["parent_hypothesis_id"] == hypothesis}
        for hypothesis in hypotheses
    }
    if any(len(configurations) > policy["max_parameter_configurations"] for configurations in parameter_configurations.values()):
        raise ValueError("max_parameter_configurations exceeded.")
    for limit, field in {
        "max_entry_variants": "entry_variant", "max_exit_variants": "exit_variant",
        "max_universe_variants": "universe_variant", "max_regime_filter_variants": "regime_filter_variant",
    }.items():
        variants = {
            hypothesis: {trial[field] for trial in trials if trial["parent_hypothesis_id"] == hypothesis}
            for hypothesis in hypotheses
        }
        if any(len(items) > policy[limit] for items in variants.values()):
            raise ValueError(f"{limit} exceeded.")


def _validate_previous_ledger(ledger: dict[str, Any]) -> None:
    policy = _canonical_policy(ledger.get("policy"))
    if ledger.get("canonical_policy_sha256") != policy["canonical_policy_sha256"]:
        raise ValueError("previous ledger canonical_policy_sha256 mismatch.")
    if ledger.get("m32a_contract_version") != policy["m32a_contract_version"] or ledger.get("m32a_policy_sha256") != policy["m32a_policy_sha256"]:
        raise ValueError("previous ledger M32A policy binding mismatch.")
    trials = ledger.get("trials")
    if not isinstance(trials, list):
        raise ValueError("previous ledger trials missing.")
    for trial in trials:
        value = _map(trial, "previous_trial")
        declared = _required_hash(value.get("canonical_trial_sha256"), "canonical_trial_sha256")
        material = copy.deepcopy(value)
        material.pop("canonical_trial_sha256")
        if declared != _trial(material)["canonical_trial_sha256"]:
            raise ValueError("previous ledger canonical_trial_sha256 mismatch.")
    for hypothesis in ledger.get("hypotheses", []):
        value = _map(hypothesis, "previous_hypothesis")
        declared = _required_hash(value.get("canonical_hypothesis_sha256"), "canonical_hypothesis_sha256")
        material = copy.deepcopy(value)
        material.pop("canonical_hypothesis_sha256")
        if declared != _hypothesis(material)["canonical_hypothesis_sha256"]:
            raise ValueError("previous ledger canonical_hypothesis_sha256 mismatch.")
    for consumption in ledger.get("sealed_oos_consumption_records", []):
        value = _map(consumption, "previous_consumption")
        declared = _required_hash(value.get("canonical_consumption_sha256"), "canonical_consumption_sha256")
        material = copy.deepcopy(value)
        material.pop("canonical_consumption_sha256")
        if declared != _sealed_oos_consumption(material)["canonical_consumption_sha256"]:
            raise ValueError("previous ledger canonical_consumption_sha256 mismatch.")

def _trial(raw: Any) -> dict[str, Any]:
    v=_map(raw,"trial"); required={"experiment_id","strategy_family_id","strategy_fingerprint","parent_hypothesis_id","parent_trial_ids","parent_failure_ids","evidence_hashes","economic_mechanism_fingerprint","universe_variant","screener_variant","ranking_variant","entry_variant","exit_variant","sizing_variant","regime_filter_variant","parameter_configuration","parameter_space_sha256","data_snapshot_hashes","train_interval","validation_interval","walk_forward_intervals","sealed_oos_interval","sealed_oos_consumption_state","transaction_cost_model","slippage_model","trial_status","metrics","failure_taxonomy","novelty_justification","canonical_trial_fingerprint","provenance"}; _unknown(v,required,"trial")
    if set(v)!=required: raise ValueError("trial fields are required.")
    if _text(v,"trial_status") not in _STATUSES: raise ValueError("trial_status is unsupported.")
    for key in {"strategy_fingerprint","parameter_space_sha256","canonical_trial_fingerprint"}: _hash(v.get(key),key)
    for key in {"evidence_hashes","data_snapshot_hashes"}:
        if not isinstance(v[key],list) or not v[key]: raise ValueError(f"{key} must be non-empty list.")
        for item in v[key]: _hash(item,key)
    out=copy.deepcopy(v); out["provenance"]=_provenance(v["provenance"]); out["canonical_trial_sha256"]=_sha(out); return out

def _hypothesis(raw: Any) -> dict[str, Any]:
    value = _map(raw, "hypothesis")
    required = {"hypothesis_id", "strategy_family_id", "parent_hypothesis_ids", "parent_failure_ids", "evidence_hashes", "economic_mechanism_fingerprint", "semantic_strategy_fingerprint", "market_scope", "instrument_classes", "timeframe", "novelty_basis", "expected_failure_modes", "provenance"}
    _unknown(value, required, "hypothesis")
    if set(value) != required:
        raise ValueError("hypothesis fields are required.")
    for key in {"hypothesis_id", "strategy_family_id", "economic_mechanism_fingerprint", "market_scope", "timeframe", "novelty_basis"}:
        _text(value, key)
    _hash(value.get("semantic_strategy_fingerprint"), "semantic_strategy_fingerprint")
    for key in {"evidence_hashes"}:
        items = value[key]
        if not isinstance(items, list) or not items or len(items) != len(set(items)):
            raise ValueError(f"{key} must be a non-empty unique list.")
        for item in items: _hash(item, key)
    for key in {"parent_hypothesis_ids", "parent_failure_ids", "instrument_classes", "expected_failure_modes"}:
        if not isinstance(value[key], list):
            raise ValueError(f"{key} must be a list.")
    result = copy.deepcopy(value); result["provenance"] = _provenance(value["provenance"]); result["canonical_hypothesis_sha256"] = _sha(result)
    return result

def _sealed_oos_consumption(raw: Any) -> dict[str, Any]:
    value = _map(raw, "consumption")
    required = {"trial_id", "dataset_id", "dataset_version", "dataset_sha256", "interval_start", "interval_end", "strategy_specification_sha256", "semantic_strategy_fingerprint", "frozen_parameter_sha256"}
    _unknown(value, required, "consumption")
    if set(value) != required:
        raise ValueError("consumption fields are required.")
    for key in {"trial_id", "dataset_id", "dataset_version", "interval_start", "interval_end"}:
        _text(value, key)
    for key in {"dataset_sha256", "strategy_specification_sha256", "semantic_strategy_fingerprint", "frozen_parameter_sha256"}:
        _hash(value.get(key), key)
    result = copy.deepcopy(value)
    result["canonical_consumption_sha256"] = _sha(result)
    return result

def _duplicates(trials: list[dict[str, Any]]) -> int:
    seen:set[tuple[str,str]]=set(); count=0
    for t in trials:
        key=(t["strategy_fingerprint"],_sha(t["parameter_configuration"]))
        if key in seen: count+=1
        seen.add(key)
    return count
def _classify_duplicates(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen:set[tuple[str,str]]=set()
    for trial in trials:
        key=(trial["strategy_fingerprint"],_sha(trial["parameter_configuration"]))
        if key in seen and not trial["novelty_justification"]["failure_mechanism_evidence"]:
            trial["trial_status"]="REJECTED_DUPLICATE"
            trial.pop("canonical_trial_sha256",None)
            trial["canonical_trial_sha256"]=_sha(trial)
        seen.add(key)
    return trials
def _same_trial_mechanism(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = {"universe_variant", "screener_variant", "ranking_variant", "entry_variant", "exit_variant", "sizing_variant", "regime_filter_variant", "data_snapshot_hashes", "train_interval", "validation_interval", "walk_forward_intervals", "sealed_oos_interval", "transaction_cost_model", "slippage_model"}
    return all(left.get(field) == right.get(field) for field in fields)
def _map(v:Any,n:str)->dict[str,Any]:
    if not isinstance(v,dict): raise ValueError(f"{n} must be object.")
    return copy.deepcopy(v)
def _unknown(v:dict[str,Any],allowed:set[str],n:str)->None:
    x=sorted(set(v)-allowed)
    if x: raise ValueError(f"{n} contains unknown field(s): {', '.join(x)}")
def _text(v:dict[str,Any],k:str)->str:
    x=v.get(k)
    if not isinstance(x,str) or not x.strip(): raise ValueError(f"{k} must be non-empty text.")
    return x.strip()
def _positive(v:dict[str,Any],k:str)->int:
    x=v.get(k)
    if isinstance(x,bool) or not isinstance(x,int) or x<=0: raise ValueError(f"{k} must be positive bounded integer.")
    return x
def _hash(x:Any,n:str)->None:
    if not isinstance(x,str) or len(x)!=64 or any(c not in '0123456789abcdef' for c in x): raise ValueError(f"{n} must be lowercase SHA-256 hex.")
def _required_hash(x:Any,n:str)->str:
    _hash(x,n); return x
def _provenance(v:Any)->dict[str,Any]:
    p={} if v is None else _map(v,"provenance")
    if any(not isinstance(k,str) or not k.strip() or val is not None and not isinstance(val,(str,int,float,bool)) or isinstance(val,float) and not math.isfinite(val) for k,val in p.items()): raise ValueError("provenance must contain JSON scalar values.")
    return p
def _sha(v:Any)->str: return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
