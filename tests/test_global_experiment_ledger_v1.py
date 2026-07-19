from __future__ import annotations

import copy
import hashlib
import json

import pytest

from research_lab.research.global_experiment_ledger_v1 import (
    apply_global_experiment_ledger_operation_v1,
    build_global_experiment_ledger_policy_v1,
    build_global_experiment_ledger_v1,
    build_m32a_experiment_accounting_evidence_v1,
    build_material_trial_configuration_sha256_v1,
    build_semantic_strategy_fingerprint_v1,
    export_multiple_testing_accounting_v1,
)


def _trial(experiment_id: str = "EXP-001") -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "strategy_family_id": "TREND",
        "strategy_fingerprint": "a" * 64,
        "parent_hypothesis_id": "HYP-001",
        "parent_trial_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": ["b" * 64],
        "economic_mechanism_fingerprint": "trend following",
        "universe_variant": "ETF_CORE",
        "screener_variant": "TREND_SCREEN",
        "ranking_variant": "RELATIVE_STRENGTH",
        "entry_variant": "BREAKOUT",
        "exit_variant": "TRAILING_STOP",
        "sizing_variant": "VOL_TARGET",
        "regime_filter_variant": "RISK_ON",
        "parameter_configuration": {"lookback": 50},
        "parameter_space_sha256": "c" * 64,
        "data_snapshot_hashes": ["d" * 64],
        "train_interval": {"start": "2020-01-01", "end": "2021-01-01"},
        "validation_interval": {"start": "2021-01-02", "end": "2021-06-01"},
        "walk_forward_intervals": [{"start": "2020-01-01", "end": "2020-06-01"}],
        "sealed_oos_interval": {"dataset_version": "OOS-V1", "start": "2021-06-02", "end": "2021-12-31"},
        "sealed_oos_consumption_state": "UNCONSUMED",
        "transaction_cost_model": "BASE_COST",
        "slippage_model": "BASE_SLIPPAGE",
        "trial_status": "STRATEGY_GATE_FAIL",
        "metrics": {"sharpe": 0.1},
        "failure_taxonomy": ["WEAK_EDGE"],
        "novelty_justification": {"kind": "NEW_MECHANISM", "failure_mechanism_evidence": []},
        "canonical_trial_fingerprint": "e" * 64,
        "provenance": {"source": "unit_test"},
    }


def _policy_request() -> dict[str, object]:
    return {
        "version": "global_experiment_ledger_policy_request_v1",
        "policy_id": "M32B-UNIT",
        "m32a_contract_version": "research_objective_promotion_gate_v1",
        "m32a_policy_sha256": "f" * 64,
        "max_total_hypotheses": 10,
        "max_global_trials": 20,
        "max_trials_per_family": 5,
        "max_trials_per_hypothesis": 10,
        "max_sealed_oos_consumptions": 3,
        "max_parameter_configurations": 50,
        "max_entry_variants": 5,
        "max_exit_variants": 5,
        "max_universe_variants": 3,
        "max_regime_filter_variants": 3,
        "novelty_policy": {"rejected_duplicates_consume_trial_allocation": True},
        "sealed_oos_policy": {"one_clean_consumption_per_frozen_lineage": True},
        "exact_duplicate_consumes_hypothesis_allocation": True,
        "exact_duplicate_consumes_trial_allocation": True,
        "near_duplicate_consumes_hypothesis_allocation": True,
        "near_duplicate_consumes_trial_allocation": True,
        "provenance": {"source": "unit_test"},
    }


def _request() -> dict[str, object]:
    policy = build_global_experiment_ledger_policy_v1(_policy_request())
    return {
        "version": "global_experiment_ledger_request_v1",
        "ledger_id": "LEDGER-001",
        "policy": policy,
        "trials": [_trial()],
        "m32a_contract_version": "research_objective_promotion_gate_v1",
        "m32a_policy_sha256": "f" * 64,
        "provenance": {"source": "unit_test"},
    }


def _replace_policy(request: dict[str, object], **changes: object) -> None:
    policy_request = _policy_request()
    policy_request.update(changes)
    request["policy"] = build_global_experiment_ledger_policy_v1(policy_request)


def _legacy_budget() -> dict[str, object]:
    policy = _policy_request()
    return {
        key: policy[key]
        for key in {
            "max_total_hypotheses", "max_global_trials", "max_trials_per_family", "max_trials_per_hypothesis",
            "max_sealed_oos_consumptions", "max_parameter_configurations",
            "max_entry_variants", "max_exit_variants", "max_universe_variants",
            "max_regime_filter_variants", "novelty_policy", "sealed_oos_policy",
        }
    }


def test_builds_deterministic_closed_world_ledger_that_retains_failed_trials():
    first = build_global_experiment_ledger_v1(copy.deepcopy(_request()))
    second = build_global_experiment_ledger_v1(copy.deepcopy(_request()))

    assert first == second
    assert first["consumed_trial_count"] == 1
    assert first["trials"][0]["trial_status"] == "STRATEGY_GATE_FAIL"
    assert len(first["trials"][0]["canonical_trial_sha256"]) == 64
    assert first["safety_fields"]["production_runtime_supported"] is False


def test_rejects_standalone_budget_and_duplicate_experiment_ids():
    request = _request()
    request["budget"] = _legacy_budget()
    del request["policy"]
    with pytest.raises(ValueError, match="budget"):
        build_global_experiment_ledger_v1(request)

    request = _request()
    request["trials"].append(_trial("EXP-001"))
    with pytest.raises(ValueError, match="experiment_id"):
        build_global_experiment_ledger_v1(request)


def test_exact_duplicate_is_retained_and_rejected_in_multiple_testing_accounting():
    request = _request()
    duplicate = _trial("EXP-002")
    duplicate["trial_status"] = "PROPOSED"
    request["trials"].append(duplicate)

    ledger = build_global_experiment_ledger_v1(request)

    assert ledger["consumed_trial_count"] == 2
    assert ledger["duplicate_fingerprint_count"] == 1
    assert [trial["trial_status"] for trial in ledger["trials"]] == [
        "STRATEGY_GATE_FAIL",
        "REJECTED_DUPLICATE",
    ]


@pytest.mark.parametrize("field, value", [
    ("max_global_trials", True),
    ("max_global_trials", 0),
    ("max_global_trials", -1),
    ("max_global_trials", 1.5),
    ("max_global_trials", "unbounded"),
])
def test_policy_rejects_non_bounded_integer_limits(field: str, value: object):
    with pytest.raises(ValueError, match=field):
        build_global_experiment_ledger_policy_v1({**_policy_request(), field: value})


def test_policy_is_hash_bound_and_input_output_are_deeply_immutable():
    request = _request()
    first = build_global_experiment_ledger_v1(request)
    _replace_policy(request, max_global_trials=1)
    first["policy"]["max_global_trials"] = 1

    second = build_global_experiment_ledger_v1(_request())

    assert second["policy"]["max_global_trials"] == 20
    assert second["canonical_policy_sha256"] == build_global_experiment_ledger_v1(_request())["canonical_policy_sha256"]


def test_policy_requires_exact_m32a_contract_binding():
    request = _request()
    request["m32a_contract_version"] = "research_objective_promotion_gate_v1"
    request["m32a_policy_sha256"] = "f" * 64

    ledger = build_global_experiment_ledger_v1(request)

    assert ledger["m32a_contract_version"] == "research_objective_promotion_gate_v1"
    assert ledger["m32a_policy_sha256"] == "f" * 64


def test_semantic_fingerprint_excludes_cosmetics_and_parameters_but_changes_for_entry_mechanism():
    semantics = {
        "economic_mechanism": "trend following", "participant_game_hypothesis": "slow institutional repricing",
        "market_scope": "US_ETF", "instrument_types": ["ETF"], "timeframe": "1D",
        "universe_rules": "liquid sector ETFs", "ranking_rules": "relative strength",
        "entry_rules": "breakout", "exit_rules": "trailing stop", "sizing_rules": "volatility target",
        "regime_rules": "risk on", "feature_requirements": ["close", "volume"],
        "display_name": "Trend Alpha", "parameters": {"ema": 20},
    }
    renamed = copy.deepcopy(semantics); renamed["display_name"] = "Renamed"; renamed["parameters"] = {"ema": 21}
    changed = copy.deepcopy(semantics); changed["entry_rules"] = "pullback reclaim"

    assert build_semantic_strategy_fingerprint_v1(semantics) == build_semantic_strategy_fingerprint_v1(renamed)
    assert build_semantic_strategy_fingerprint_v1(semantics) != build_semantic_strategy_fingerprint_v1(changed)


def test_append_only_transition_requires_adjacent_states_and_preserves_previous_ledger():
    previous = build_global_experiment_ledger_v1(_request())
    previous_copy = copy.deepcopy(previous)
    request = {
        "version": "global_experiment_ledger_operation_request_v1",
        "previous_ledger": previous,
        "previous_ledger_sha256": previous["canonical_ledger_sha256"],
        "operation": {"operation_id": "OP-001", "kind": "TRANSITION_TRIAL", "experiment_id": "EXP-001", "target_status": "PORTFOLIO_CONTRIBUTION_PASS"},
        "provenance": {"source": "unit_test"},
    }
    with pytest.raises(ValueError, match="transition"):
        apply_global_experiment_ledger_operation_v1(request)
    assert previous == previous_copy


def test_canonical_policy_binds_the_exact_m32a_contract_and_duplicate_allocation_rules():
    policy = build_global_experiment_ledger_policy_v1(_policy_request())

    assert policy["policy_id"] == "M32B-UNIT"
    assert policy["m32a_contract_version"] == "research_objective_promotion_gate_v1"
    assert len(policy["canonical_policy_sha256"]) == 64


def test_multiple_testing_export_retains_all_attempts_without_statistical_placeholders():
    request = _request()
    duplicate = _trial("EXP-002")
    duplicate["trial_status"] = "PROPOSED"
    request["trials"].append(duplicate)
    ledger = build_global_experiment_ledger_v1(request)

    exported = export_multiple_testing_accounting_v1(ledger)

    assert [item["trial_id"] for item in exported["trials"]] == ["EXP-001", "EXP-002"]
    assert exported["total_exact_duplicates"] == 1
    assert "deflated_sharpe_ratio" not in exported
    assert "probability_backtest_overfitting" not in exported


def _hypothesis(hypothesis_id: str = "HYP-002") -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "strategy_family_id": "MOMENTUM",
        "parent_hypothesis_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": ["1" * 64],
        "economic_mechanism_fingerprint": "cross sectional momentum",
        "semantic_strategy_fingerprint": "2" * 64,
        "market_scope": "US_ETF",
        "instrument_classes": ["ETF"],
        "timeframe": "1D",
        "novelty_basis": "distinct cross sectional ranking mechanism",
        "expected_failure_modes": ["CROWDING"],
        "provenance": {"source": "unit_test"},
    }


def _operation(previous: dict[str, object], operation: dict[str, object]) -> dict[str, object]:
    return {
        "version": "global_experiment_ledger_operation_request_v1",
        "previous_ledger": previous,
        "previous_ledger_sha256": previous["canonical_ledger_sha256"],
        "operation": operation,
        "provenance": {"source": "unit_test"},
    }


def _rehash_ledger_for_tamper_test(ledger: dict[str, object]) -> None:
    material = {key: value for key, value in ledger.items() if key != "canonical_ledger_sha256"}
    ledger["canonical_ledger_sha256"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def test_register_hypothesis_is_append_only_deterministic_and_immutable():
    previous = build_global_experiment_ledger_v1(_request())
    snapshot = copy.deepcopy(previous)
    request = _operation(previous, {
        "operation_id": "OP-REGISTER-1", "kind": "REGISTER_HYPOTHESIS",
        "hypothesis": _hypothesis(),
    })

    first = apply_global_experiment_ledger_operation_v1(request)
    second = apply_global_experiment_ledger_operation_v1(request)

    assert previous == snapshot
    assert first == second
    assert first["hypotheses"][0]["hypothesis_id"] == "HYP-002"
    assert len(first["hypotheses"][0]["canonical_hypothesis_sha256"]) == 64
    assert first["operation_history"][-1]["operation_id"] == "OP-REGISTER-1"


def test_append_trial_rejects_unknown_hypothesis_and_preserves_previous_ledger():
    previous = build_global_experiment_ledger_v1(_request())
    snapshot = copy.deepcopy(previous)
    candidate = _trial("EXP-002")
    candidate["parent_hypothesis_id"] = "HYP-UNKNOWN"

    with pytest.raises(ValueError, match="hypothesis"):
        apply_global_experiment_ledger_operation_v1(_operation(previous, {
            "operation_id": "OP-APPEND-1", "kind": "APPEND_TRIAL", "trial": candidate,
        }))
    assert previous == snapshot


def test_sealed_oos_consumption_requires_frozen_trial_and_is_append_only():
    request = _request()
    request["trials"][0]["trial_status"] = "PARAMETERS_FROZEN"
    previous = build_global_experiment_ledger_v1(request)
    consumption = {
        "trial_id": "EXP-001", "dataset_id": "SEALED-1", "dataset_version": "v1",
        "dataset_sha256": "3" * 64, "interval_start": "2022-01-01", "interval_end": "2022-12-31",
        "strategy_specification_sha256": "4" * 64, "semantic_strategy_fingerprint": "a" * 64,
        "frozen_parameter_sha256": "5" * 64,
    }

    result = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-OOS-1", "kind": "RECORD_SEALED_OOS_CONSUMPTION", "consumption": consumption,
    }))

    assert result["sealed_oos_consumption_records"][0]["trial_id"] == "EXP-001"
    assert result["trials"][0]["trial_status"] == "SEALED_OOS_CONSUMED"
    with pytest.raises(ValueError, match="second clean"):
        apply_global_experiment_ledger_operation_v1(_operation(result, {
            "operation_id": "OP-OOS-2", "kind": "RECORD_SEALED_OOS_CONSUMPTION", "consumption": consumption,
        }))


def test_mark_descendant_contaminated_is_terminal_and_preserves_ancestor_evidence():
    request = _request()
    request["trials"][0]["trial_status"] = "PARAMETERS_FROZEN"
    child = _trial("EXP-002")
    child["parent_trial_ids"] = ["EXP-001"]
    request["trials"].append(child)
    previous = build_global_experiment_ledger_v1(request)
    previous = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-CONTAMINATE-OOS", "kind": "RECORD_SEALED_OOS_CONSUMPTION",
        "consumption": {"trial_id": "EXP-001", "dataset_id": "SEALED-C", "dataset_version": "v1", "dataset_sha256": "6" * 64, "interval_start": "2022-01-01", "interval_end": "2022-12-31", "strategy_specification_sha256": "4" * 64, "semantic_strategy_fingerprint": "a" * 64, "frozen_parameter_sha256": "5" * 64},
    }))

    result = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-CONTAMINATE-1", "kind": "MARK_DESCENDANT_CONTAMINATED",
        "experiment_id": "EXP-002", "ancestor_trial_id": "EXP-001", "sealed_oos_dataset_sha256": "6" * 64,
        "ancestor_consumption_sha256": previous["sealed_oos_consumption_records"][0]["canonical_consumption_sha256"],
        "material_difference": "parameter lookback changed after sealed OOS",
    }))

    assert result["trials"][1]["trial_status"] == "SEALED_OOS_CONTAMINATED"
    assert result["contamination_records"][0]["ancestor_trial_id"] == "EXP-001"
    assert result["contamination_records"][0]["ancestor_consumption_sha256"] == previous["sealed_oos_consumption_records"][0]["canonical_consumption_sha256"]
    with pytest.raises(ValueError, match="transition"):
        apply_global_experiment_ledger_operation_v1(_operation(result, {
            "operation_id": "OP-CONTAMINATE-2", "kind": "TRANSITION_TRIAL", "experiment_id": "EXP-002", "target_status": "BUDGET_AUTHORIZED",
        }))


def test_material_trial_configuration_hash_is_stable_for_cosmetics_and_changes_for_material_inputs():
    trial = _trial()
    first = build_material_trial_configuration_sha256_v1(trial)
    reordered = dict(reversed(list(trial.items())))
    changed = copy.deepcopy(trial)
    changed["parameter_configuration"] = {"lookback": 51}

    assert first == build_material_trial_configuration_sha256_v1(reordered)
    assert first != build_material_trial_configuration_sha256_v1(changed)
    changed = copy.deepcopy(trial)
    changed["data_snapshot_hashes"] = ["7" * 64]
    assert first != build_material_trial_configuration_sha256_v1(changed)


def test_append_trial_retains_exact_duplicate_and_references_original_trial():
    request = _request()
    request["trials"][0]["trial_status"] = "PROPOSED"
    previous = build_global_experiment_ledger_v1(request)
    registered = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-REGISTER-HYP-1", "kind": "REGISTER_HYPOTHESIS",
        "hypothesis": {**_hypothesis("HYP-001"), "semantic_strategy_fingerprint": "a" * 64},
    }))
    duplicate = _trial("EXP-002")
    result = apply_global_experiment_ledger_operation_v1(_operation(registered, {
        "operation_id": "OP-APPEND-DUP-1", "kind": "APPEND_TRIAL", "trial": duplicate,
    }))

    appended = result["trials"][1]
    assert appended["trial_status"] == "REJECTED_DUPLICATE"
    assert appended["parent_trial_ids"] == ["EXP-001"]


def test_append_trial_rejects_unjustified_local_parameter_neighbor_as_near_duplicate():
    request = _request()
    request["trials"][0]["trial_status"] = "PROPOSED"
    previous = build_global_experiment_ledger_v1(request)
    registered = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-REGISTER-HYP-NEAR", "kind": "REGISTER_HYPOTHESIS",
        "hypothesis": {**_hypothesis("HYP-001"), "semantic_strategy_fingerprint": "a" * 64},
    }))
    neighbor = _trial("EXP-NEAR-002")
    neighbor["parameter_configuration"] = {"lookback": 51}
    result = apply_global_experiment_ledger_operation_v1(_operation(registered, {
        "operation_id": "OP-APPEND-NEAR-1", "kind": "APPEND_TRIAL", "trial": neighbor,
    }))

    assert result["trials"][1]["trial_status"] == "REJECTED_NEAR_DUPLICATE"
    assert result["trials"][1]["parent_trial_ids"] == ["EXP-001"]


def test_ledger_derives_family_and_variant_budgets_from_retained_trials():
    request = _request()
    request["trials"].append(_trial("EXP-002"))
    request["trials"][1]["strategy_fingerprint"] = "9" * 64
    request["trials"][1]["parameter_configuration"] = {"lookback": 51}
    ledger = build_global_experiment_ledger_v1(request)

    assert ledger["trials_by_strategy_family"] == {"TREND": 2}
    assert ledger["remaining_family_trial_budget"]["TREND"] == 3
    assert ledger["parameter_configurations_by_hypothesis"]["HYP-001"] == 2


def test_ledger_rejects_parameter_configuration_budget_exhaustion():
    request = _request()
    _replace_policy(request, max_parameter_configurations=1)
    request["trials"].append(_trial("EXP-002"))
    request["trials"][1]["parameter_configuration"] = {"lookback": 51}

    with pytest.raises(ValueError, match="max_parameter_configurations"):
        build_global_experiment_ledger_v1(request)


def test_ledger_rejects_per_hypothesis_trial_exhaustion():
    request = _request()
    _replace_policy(request, max_trials_per_hypothesis=1)
    request["trials"].append(_trial("EXP-002"))
    request["trials"][1]["strategy_fingerprint"] = "9" * 64

    with pytest.raises(ValueError, match="max_trials_per_hypothesis"):
        build_global_experiment_ledger_v1(request)


def test_append_trial_cannot_bypass_canonical_policy_limits():
    request = _request()
    _replace_policy(request, max_trials_per_hypothesis=1)
    previous = build_global_experiment_ledger_v1(request)
    registered = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-REGISTER-POLICY", "kind": "REGISTER_HYPOTHESIS",
        "hypothesis": {**_hypothesis("HYP-001"), "semantic_strategy_fingerprint": "a" * 64},
    }))
    candidate = _trial("EXP-002")
    candidate["strategy_fingerprint"] = "9" * 64

    with pytest.raises(ValueError, match="max_trials_per_hypothesis"):
        apply_global_experiment_ledger_operation_v1(_operation(registered, {
            "operation_id": "OP-APPEND-POLICY", "kind": "APPEND_TRIAL", "trial": candidate,
        }))


def test_operations_reject_a_self_hashed_ledger_with_tampered_policy():
    previous = build_global_experiment_ledger_v1(_request())
    previous["policy"]["max_trials_per_hypothesis"] = 999
    _rehash_ledger_for_tamper_test(previous)

    with pytest.raises(ValueError, match="canonical_policy_sha256"):
        apply_global_experiment_ledger_operation_v1(_operation(previous, {
            "operation_id": "OP-TAMPER-POLICY", "kind": "REGISTER_HYPOTHESIS",
            "hypothesis": _hypothesis(),
        }))


def test_operations_reject_a_self_hashed_ledger_with_tampered_frozen_trial():
    previous = build_global_experiment_ledger_v1(_request())
    previous["trials"][0]["trial_status"] = "PARAMETERS_FROZEN"
    _rehash_ledger_for_tamper_test(previous)

    with pytest.raises(ValueError, match="canonical_trial_sha256"):
        apply_global_experiment_ledger_operation_v1(_operation(previous, {
            "operation_id": "OP-TAMPER-FROZEN", "kind": "RECORD_SEALED_OOS_CONSUMPTION",
            "consumption": {"trial_id": "EXP-001", "dataset_id": "SEALED-T", "dataset_version": "v1", "dataset_sha256": "3" * 64, "interval_start": "2022-01-01", "interval_end": "2022-12-31", "strategy_specification_sha256": "4" * 64, "semantic_strategy_fingerprint": "a" * 64, "frozen_parameter_sha256": "5" * 64},
        }))


@pytest.mark.parametrize(("limit", "field"), [
    ("max_entry_variants", "entry_variant"),
    ("max_exit_variants", "exit_variant"),
    ("max_universe_variants", "universe_variant"),
    ("max_regime_filter_variants", "regime_filter_variant"),
])
def test_ledger_rejects_variant_budget_exhaustion(limit: str, field: str):
    request = _request()
    _replace_policy(request, **{limit: 1})
    request["trials"].append(_trial("EXP-002"))
    request["trials"][1][field] = "DIFFERENT_VARIANT"

    with pytest.raises(ValueError, match=limit):
        build_global_experiment_ledger_v1(request)


def test_m32a_accounting_evidence_reconciles_canonical_ledger_and_export_fail_closed():
    ledger = build_global_experiment_ledger_v1(_request())
    exported = export_multiple_testing_accounting_v1(ledger)

    evidence = build_m32a_experiment_accounting_evidence_v1(ledger, exported)

    assert evidence["bounded_policy_status"] == "PASS"
    assert evidence["complete_attempted_trial_accounting_status"] == "PASS"
    assert evidence["outstanding_inconsistencies"] == []


def test_public_results_expose_complete_zero_side_effect_safety_evidence():
    ledger = build_global_experiment_ledger_v1(_request())

    assert ledger["safety_fields"] == {
        "provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0,
        "filesystem_writes_performed": False, "knihomol_mutation_performed": False,
        "generated_code_execution_performed": False, "paper_trading_performed": False,
        "live_trading_performed": False, "executable_orders_generated": False,
        "production_runtime_supported": False,
    }


def test_multiple_testing_export_counts_registered_hypotheses_when_present():
    previous = build_global_experiment_ledger_v1(_request())
    registered = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-REGISTER-EXPORT", "kind": "REGISTER_HYPOTHESIS",
        "hypothesis": _hypothesis("HYP-002"),
    }))

    exported = export_multiple_testing_accounting_v1(registered)

    assert exported["total_registered_hypotheses"] == 1


def test_sealed_oos_rejects_same_dataset_hash_under_different_dataset_name():
    request = _request()
    request["trials"].append(_trial("EXP-002"))
    request["trials"][1]["strategy_fingerprint"] = "9" * 64
    request["trials"][0]["trial_status"] = "PARAMETERS_FROZEN"
    request["trials"][1]["trial_status"] = "PARAMETERS_FROZEN"
    previous = build_global_experiment_ledger_v1(request)
    first = apply_global_experiment_ledger_operation_v1(_operation(previous, {
        "operation_id": "OP-OOS-UNIQUE-1", "kind": "RECORD_SEALED_OOS_CONSUMPTION",
        "consumption": {"trial_id": "EXP-001", "dataset_id": "SEALED-A", "dataset_version": "v1", "dataset_sha256": "8" * 64, "interval_start": "2022-01-01", "interval_end": "2022-12-31", "strategy_specification_sha256": "4" * 64, "semantic_strategy_fingerprint": "a" * 64, "frozen_parameter_sha256": "5" * 64},
    }))
    with pytest.raises(ValueError, match="dataset hash"):
        apply_global_experiment_ledger_operation_v1(_operation(first, {
            "operation_id": "OP-OOS-UNIQUE-2", "kind": "RECORD_SEALED_OOS_CONSUMPTION",
            "consumption": {"trial_id": "EXP-002", "dataset_id": "RENAMED", "dataset_version": "v1", "dataset_sha256": "8" * 64, "interval_start": "2022-01-01", "interval_end": "2022-12-31", "strategy_specification_sha256": "4" * 64, "semantic_strategy_fingerprint": "a" * 64, "frozen_parameter_sha256": "5" * 64},
        }))


def test_contamination_requires_matching_recorded_ancestor_consumption():
    request = _request()
    child = _trial("EXP-002")
    child["strategy_fingerprint"] = "9" * 64
    child["parent_trial_ids"] = ["EXP-001"]
    request["trials"].append(child)
    previous = build_global_experiment_ledger_v1(request)

    with pytest.raises(ValueError, match="consumption"):
        apply_global_experiment_ledger_operation_v1(_operation(previous, {
            "operation_id": "OP-CONTAMINATION-NO-OOS", "kind": "MARK_DESCENDANT_CONTAMINATED",
            "experiment_id": "EXP-002", "ancestor_trial_id": "EXP-001", "sealed_oos_dataset_sha256": "6" * 64,
            "ancestor_consumption_sha256": "7" * 64, "material_difference": "parameter change",
        }))


def test_ledger_rejects_m32a_contract_mismatch_instead_of_only_echoing_it():
    request = _request()
    request["m32a_contract_version"] = "wrong_contract"

    with pytest.raises(ValueError, match="m32a_contract_version"):
        build_global_experiment_ledger_v1(request)


def test_ledger_rejects_budget_when_canonical_policy_is_present():
    request = _request()
    request["budget"] = _legacy_budget()

    with pytest.raises(ValueError, match="budget"):
        build_global_experiment_ledger_v1(request)


def test_ledger_rejects_missing_or_hash_mismatched_canonical_policy():
    request = _request()
    del request["policy"]
    with pytest.raises(ValueError, match="policy"):
        build_global_experiment_ledger_v1(request)

    request = _request()
    request["policy"]["max_global_trials"] = 1
    with pytest.raises(ValueError, match="canonical_policy_sha256"):
        build_global_experiment_ledger_v1(request)
