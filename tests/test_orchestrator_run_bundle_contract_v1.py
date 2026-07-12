from __future__ import annotations

import copy
import hashlib
import json

import pytest

from research_lab.execution.orchestrator_run_bundle_contract_v1 import (
    build_orchestrator_run_bundle_contract,
)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _input_bars() -> list[dict[str, object]]:
    closes = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
        113.0,
        114.0,
        111.0,
        110.0,
        109.0,
        111.0,
        112.0,
        111.0,
        112.0,
        106.0,
    ]
    dates = [
        "2026-01-01",
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
        "2026-01-15",
        "2026-01-16",
        "2026-01-19",
        "2026-01-20",
        "2026-01-21",
        "2026-01-22",
        "2026-01-23",
        "2026-01-26",
        "2026-01-27",
        "2026-01-28",
        "2026-01-29",
        "2026-01-30",
        "2026-02-02",
    ]
    bars: list[dict[str, object]] = []
    ranges = [2.0] * len(closes)
    ranges[20] = 6.0
    ranges[21] = 8.0
    ranges[22] = 10.0
    for index, (date, close, span) in enumerate(zip(dates, closes, ranges, strict=True), start=1):
        bars.append(
            {
                "timestamp": date,
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
                "volume": 1_000_000 + index,
            }
        )
    return bars


def _orchestrator_request() -> dict[str, object]:
    strategy_identity = {
        "strategy_id": "STFP_BASE",
        "strategy_builder": "swing_trend_filtered_pullback",
        "strategy_version": "v1",
    }
    dataset_identity = {
        "dataset_id": "SYNTH_ABC_2026",
        "data_source": "synthetic_local_bars",
        "symbol": "SYNTH_ABC",
        "bar_count": len(_input_bars()),
    }
    evaluation_period_identity = {
        "window_id": "WF_2026",
        "train_start": "2026-01-01",
        "train_end": "2026-01-20",
        "test_start": "2026-01-21",
        "test_end": "2026-01-31",
    }
    parameter_schema = {
        "parameters": [
            {"name": "fast_sma", "type": "int", "minimum": 2, "maximum": 10},
            {"name": "slow_sma", "type": "int", "minimum": 3, "maximum": 20},
            {"name": "rsi_entry", "type": "float", "minimum": 50.0, "maximum": 90.0},
            {"name": "rsi_exit", "type": "float", "minimum": 55.0, "maximum": 95.0},
            {"name": "atr_stop", "type": "float", "minimum": 0.5, "maximum": 5.0},
            {"name": "max_exposure", "type": "float", "minimum": 0.1, "maximum": 1.0},
        ]
    }
    baseline_parameter_set = {
        "fast_sma": 3,
        "slow_sma": 5,
        "rsi_entry": 80.0,
        "rsi_exit": 85.0,
        "atr_stop": 2.0,
        "max_exposure": 0.5,
    }
    permitted_variants = [
        {"variant_id": "BASELINE", "parameter_overrides": {}},
        {"variant_id": "SIMPLER_SAFE", "parameter_overrides": {"fast_sma": 4}},
    ]
    robustness_policy = {"min_walk_forward_windows": 3}
    complexity_budget = {"max_parameter_count": 6, "max_complexity_score": 8.0}
    immutable_input_hashes = {
        "strategy_identity": _canonical_sha256(strategy_identity),
        "dataset_identity": _canonical_sha256(dataset_identity),
        "evaluation_period_identity": _canonical_sha256(evaluation_period_identity),
        "parameter_schema": _canonical_sha256(parameter_schema),
        "baseline_parameter_set": _canonical_sha256(baseline_parameter_set),
        "robustness_policy": _canonical_sha256(robustness_policy),
        "complexity_budget": _canonical_sha256(complexity_budget),
        "permitted_variants": _canonical_sha256(permitted_variants),
    }
    return {
        "version": "e2e_research_orchestrator_acceptance_request_v1",
        "experiment_manifest_request": {
            "version": "experiment_manifest_contract_request_v1",
            "experiment_id": "EXP-20260712-ORCH-001",
            "strategy_identity": strategy_identity,
            "immutable_input_hashes": immutable_input_hashes,
            "dataset_identity": dataset_identity,
            "evaluation_period_identity": evaluation_period_identity,
            "parameter_schema": parameter_schema,
            "baseline_parameter_set": baseline_parameter_set,
            "permitted_variants": permitted_variants,
            "required_evaluators": ["e2e_review_pipeline_acceptance_v1"],
            "robustness_policy": robustness_policy,
            "complexity_budget": complexity_budget,
            "iteration_budget": 6,
            "revision_budget": 1,
            "retry_budget": 1,
            "knowledge_note_ids": ["KNIH-001"],
            "required_human_gates": ["FINAL_REVIEW_ONLY_APPROVAL"],
        },
        "initial_orchestration_state_request": {
            "version": "orchestration_state_contract_request_v1",
            "target_state": "CREATED",
            "reason": "created",
        },
        "robustness_pipeline_request": {
            "version": "e2e_review_pipeline_acceptance_request_v1",
            "strategy_identity": {
                "strategy_id": "STFP_BASE",
                "strategy_builder": "swing_trend_filtered_pullback",
                "baseline_variant_id": "SIMPLER_SAFE",
            },
            "symbol": "abc",
            "input_bars": _input_bars(),
            "strategy_parameters": baseline_parameter_set,
            "executor_config": {
                "runtime_contract_version": "risk_execution_contract_v1",
                "initial_equity": 100_000.0,
                "fixed_fractional_config": {"selected_risk_per_trade_pct": 1.0},
                "strategy_position_cap": 100_000.0,
                "portfolio_exposure_cap": 100_000.0,
                "circuit_breaker_thresholds": [{"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75}],
                "reentry_rule": {"type": "equity_recovery", "recovery_from_peak_pct": 1.0, "cooldown_days": 1},
                "fractional_units_allowed": False,
                "output_mode": "full_result",
            },
            "strategy_rule_definitions": [
                {"rule_id": "protective_stop", "rule_role": "risk_safety", "description": "Protective stop must stay active."},
                {"rule_id": "legacy_filter", "rule_role": "alpha", "description": "Legacy alpha filter."},
                {"rule_id": "note_tag", "rule_role": "alpha", "description": "Decorative rule tag."},
            ],
            "robustness_review_inputs": {
                "parameter_schema": {
                    "parameters": [
                        {"name": "fast_sma", "type": "int", "baseline": 3, "tested_values": [2, 3, 4]},
                        {"name": "slow_sma", "type": "int", "baseline": 5, "tested_values": [5, 6, 7]},
                        {"name": "rsi_entry", "type": "float", "baseline": 80.0, "tested_values": [75.0, 80.0, 82.0]},
                    ]
                },
                "evaluation_window_metadata": {
                    "walk_forward_method": "true_rolling_oos",
                    "window_count": 4,
                    "pass_rate": 0.75,
                    "effective_sample_size": 160,
                },
                "experiment_trial_metadata": {
                    "trial_count": 12,
                    "selection_mode": "bounded_grid",
                    "selection_bias_controls": {
                        "deflated_sharpe_applied": True,
                        "pbo_checked": True,
                    },
                },
                "validated_knihomol_evidence": {
                    "notes": [
                        {
                            "note_id": "KNIH-001",
                            "status": "validated",
                            "topic": "selection_bias",
                            "summary": "Prefer simpler robust variants once all safety controls hold.",
                            "supports": ["selection_bias", "risk_controls"],
                        }
                    ]
                },
                "robustness_policy": {
                    "min_walk_forward_windows": 3,
                    "min_walk_forward_pass_rate": 0.67,
                    "max_drawdown": -0.20,
                    "max_trial_count": 16,
                    "max_parameter_count": 4,
                },
            },
            "ablation_inputs": {
                "baseline_variant": {
                    "strategy_id": "STFP_BASE",
                    "evaluation_artifact": {
                        "total_return": 0.04,
                        "max_drawdown": -0.08,
                        "final_review_status": "REVIEW_REQUIRED",
                    },
                },
                "ablated_variants": [
                    {
                        "variant_id": "BASELINE",
                        "strategy_id": "STFP_BASE",
                        "removed_rule": {"rule_id": "note_tag", "rule_role": "alpha"},
                        "evaluation_artifact": {
                            "total_return": 0.0401,
                            "max_drawdown": -0.0801,
                            "final_review_status": "REVIEW_REQUIRED",
                        },
                    },
                    {
                        "variant_id": "SIMPLER_SAFE",
                        "strategy_id": "STFP_BASE",
                        "removed_rule": {"rule_id": "legacy_filter", "rule_role": "alpha"},
                        "evaluation_artifact": {
                            "total_return": 0.039,
                            "max_drawdown": -0.081,
                            "final_review_status": "REVIEW_REQUIRED",
                        },
                    },
                    {
                        "variant_id": "RISKY",
                        "strategy_id": "STFP_BASE",
                        "removed_rule": {"rule_id": "protective_stop", "rule_role": "risk_safety"},
                        "evaluation_artifact": {
                            "total_return": 0.05,
                            "max_drawdown": -0.11,
                            "final_review_status": "REVIEW_REQUIRED",
                        },
                    },
                ],
                "ablation_policy": {
                    "return_tolerance": 0.002,
                    "drawdown_tolerance": 0.002,
                },
            },
            "parameter_stability_inputs": [
                {
                    "version": "parameter_stability_evaluator_request_v1",
                    "parameter_name": "fast_sma",
                    "baseline_value": 3,
                    "one_dimensional_results": [
                        {"value": 1, "score": 0.20},
                        {"value": 2, "score": 0.39},
                        {"value": 3, "score": 0.40},
                        {"value": 4, "score": 0.39},
                        {"value": 5, "score": 0.21},
                    ],
                    "pair_interactions": [],
                    "stability_policy": {
                        "plateau_tolerance": 0.02,
                        "edge_buffer": 1,
                        "spike_penalty_threshold": 0.08,
                    },
                },
                {
                    "version": "parameter_stability_evaluator_request_v1",
                    "parameter_name": "slow_sma",
                    "baseline_value": 5,
                    "one_dimensional_results": [
                        {"value": 3, "score": 0.10},
                        {"value": 4, "score": 0.20},
                        {"value": 5, "score": 0.40},
                        {"value": 6, "score": 0.39},
                        {"value": 7, "score": 0.15},
                    ],
                    "pair_interactions": [],
                    "stability_policy": {
                        "plateau_tolerance": 0.02,
                        "edge_buffer": 1,
                        "spike_penalty_threshold": 0.08,
                    },
                },
            ],
            "robustness_decision_inputs": {
                "walk_forward_fold_evidence": {
                    "fold_results": [
                        {"fold_id": "WF-1", "passed": True, "failure_reasons": []},
                        {"fold_id": "WF-2", "passed": True, "failure_reasons": []},
                        {"fold_id": "WF-3", "passed": True, "failure_reasons": []},
                    ],
                },
                "effective_sample_metadata": {
                    "effective_sample_size": 160,
                    "minimum_required": 100,
                    "passed": True,
                },
                "trial_count_metadata": {
                    "total_trials": 12,
                    "complete_accounting": True,
                    "bounded_search": True,
                    "selection_mode": "bounded_grid",
                },
                "deflated_sharpe_result": {
                    "available": True,
                    "passed": True,
                    "observed_value": 0.32,
                    "minimum_required": 0.10,
                },
                "pbo_cscv_result": {
                    "available": True,
                    "passed": True,
                    "observed_value": 0.08,
                    "maximum_allowed": 0.20,
                },
                "drawdown_stress_result": {
                    "available": True,
                    "passed": True,
                    "stressed_max_drawdown": -0.11,
                    "maximum_allowed_drawdown": -0.20,
                },
                "complexity_variants": {
                    "variants": [
                        {
                            "variant_id": "BASELINE",
                            "parameter_count": 4,
                            "complexity_score": 4.0,
                            "required_risk_controls_preserved": True,
                        },
                        {
                            "variant_id": "SIMPLER_SAFE",
                            "parameter_count": 3,
                            "complexity_score": 3.0,
                            "required_risk_controls_preserved": True,
                        },
                        {
                            "variant_id": "RISKY",
                            "parameter_count": 2,
                            "complexity_score": 2.0,
                            "required_risk_controls_preserved": False,
                        },
                    ],
                },
                "decision_policy": {
                    "missing_effective_sample_action": "REVISE",
                    "failed_effective_sample_action": "REVISE",
                    "missing_dsr_action": "REVISE",
                    "failed_dsr_action": "REJECT_OVERFIT",
                    "missing_pbo_action": "REVISE",
                    "failed_pbo_action": "REJECT_OVERFIT",
                    "missing_drawdown_stress_action": "REVISE",
                    "failed_drawdown_stress_action": "REJECT_RISK",
                },
            },
        },
        "revise_retest_request": {
            "version": "bounded_revise_retest_loop_request_v1",
            "steps": [],
        },
        "failure_memory_request": {
            "version": "research_failure_memory_contract_request_v1",
            "prior_memory": None,
            "failure_observation": {
                "observation_id": "OBS-001",
                "variant_id": "SIMPLER_SAFE",
                "failure_category": "rejected_variant",
                "parameter_region": {"fast_sma": 4},
                "lineage_hashes": {
                    "experiment_manifest_output_sha256": "a" * 64,
                    "robustness_decision_output_sha256": "b" * 64,
                },
                "evidence_hashes": {
                    "primary_evidence_sha256": "c" * 64,
                },
                "notes": ["review_only_failure"],
            },
        },
        "human_approval_request": {
            "version": "human_approval_gate_request_v1",
            "selected_variant_id": "SIMPLER_SAFE",
            "approval_action": "APPROVE",
            "reviewer_identity": {
                "reviewer_id": "reviewer-001",
                "reviewer_role": "research_reviewer",
            },
            "approval_timestamp": "2026-07-12T08:10:00Z",
            "expiry_policy": {
                "expiry_timestamp": "2026-07-15T08:10:00Z",
                "validation_timestamp": "2026-07-12T08:10:00Z",
            },
            "prior_approval": None,
        },
        "provenance": {"source": "unit_test"},
    }


def _request() -> dict[str, object]:
    orchestrator_request = _orchestrator_request()
    experiment_manifest_request = orchestrator_request["experiment_manifest_request"]
    request_source_metadata = {
        "source_type": "local_request_file",
        "source_path": "requests/orchestrator/request-001.json",
        "source_sha256": "d" * 64,
    }
    supplied_input_artifact_hashes = {
        "ohlcv_dataset_sha256": "a" * 64,
        "knihomol_snapshot_sha256": "b" * 64,
        "request_file_sha256": "c" * 64,
    }
    return {
        "version": "orchestrator_run_bundle_contract_request_v1",
        "run_id": "RUN-20260712-001",
        "orchestrator_request": orchestrator_request,
        "request_source_metadata": request_source_metadata,
        "supplied_input_artifact_hashes": supplied_input_artifact_hashes,
        "expected_experiment_id": experiment_manifest_request["experiment_id"],
        "expected_strategy_identity": copy.deepcopy(experiment_manifest_request["strategy_identity"]),
        "expected_dataset_identity": copy.deepcopy(experiment_manifest_request["dataset_identity"]),
        "expected_knihomol_evidence_ids": ["KNIH-001"],
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_orchestrator_run_bundle_contract(copy.deepcopy(request))


def test_builds_deterministic_run_bundle_contract():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["bundle_contract_version"] == "orchestrator_run_bundle_contract_v1"
    assert first["run_id"] == "RUN-20260712-001"
    assert first["canonical_request_sha256"] == _canonical_sha256(first["normalized_request"])
    assert first["bundle_manifest_sha256"] == _canonical_sha256(first["bundle_manifest"])
    assert first["execution_authority_granted"] is False
    assert first["persistence_authority_granted"] is False
    assert first["filesystem_access_performed"] is False
    assert first["provider_calls_used"] == 0
    assert first["external_data_used"] is False
    assert first["production_runtime_supported"] is False


def test_rejects_blank_run_id():
    request = _request()
    request["run_id"] = "   "

    with pytest.raises(ValueError, match="run_id"):
        _run(request)


def test_rejects_malformed_supplied_input_hash():
    request = _request()
    request["supplied_input_artifact_hashes"]["ohlcv_dataset_sha256"] = "not-a-sha"  # type: ignore[index]

    with pytest.raises(ValueError, match="sha256"):
        _run(request)


def test_rejects_expected_identity_mismatch():
    request = _request()
    request["expected_experiment_id"] = "EXP-OTHER"

    with pytest.raises(ValueError, match="expected_experiment_id"):
        _run(request)


def test_rejects_duplicate_expected_knihomol_evidence_ids():
    request = _request()
    request["expected_knihomol_evidence_ids"] = ["KNIH-001", "KNIH-001"]

    with pytest.raises(ValueError, match="unique"):
        _run(request)


def test_rejects_knihomol_id_mismatch_between_request_sections():
    request = _request()
    request["orchestrator_request"]["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"]["notes"][0]["note_id"] = "KNIH-999"  # type: ignore[index]

    with pytest.raises(ValueError, match="expected_knihomol_evidence_ids"):
        _run(request)
