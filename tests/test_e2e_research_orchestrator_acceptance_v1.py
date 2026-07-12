from __future__ import annotations

import copy
import hashlib
import json

import pandas as pd
import pytest

import research_lab.execution as execution
from research_lab.execution.e2e_review_pipeline_acceptance_v1 import (
    run_e2e_review_pipeline_acceptance,
)
from research_lab.execution.e2e_research_orchestrator_acceptance_v1 import (
    run_e2e_research_orchestrator_acceptance,
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
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    bars: list[dict[str, object]] = []
    ranges = [2.0] * len(closes)
    ranges[20] = 6.0
    ranges[21] = 8.0
    ranges[22] = 10.0
    for index, (ts, close, span) in enumerate(zip(dates, closes, ranges, strict=True), start=1):
        bars.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
                "volume": 1_000_000 + index,
            }
        )
    return bars


def _experiment_manifest_request(
    *,
    experiment_id: str = "EXP-20260712-ORCH-001",
    revision_budget: int = 1,
    retry_budget: int = 1,
    iteration_budget: int = 6,
) -> dict[str, object]:
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
        "version": "experiment_manifest_contract_request_v1",
        "experiment_id": experiment_id,
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
        "iteration_budget": iteration_budget,
        "revision_budget": revision_budget,
        "retry_budget": retry_budget,
        "knowledge_note_ids": ["KNIH-001"],
        "required_human_gates": ["FINAL_REVIEW_ONLY_APPROVAL"],
    }


def _robustness_pipeline_request() -> dict[str, object]:
    return {
        "version": "e2e_review_pipeline_acceptance_request_v1",
        "strategy_identity": {
            "strategy_id": "STFP_BASE",
            "strategy_builder": "swing_trend_filtered_pullback",
            "baseline_variant_id": "SIMPLER_SAFE",
        },
        "symbol": "abc",
        "input_bars": _input_bars(),
        "strategy_parameters": {
            "fast_sma": 3,
            "slow_sma": 5,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
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
    }


def _failure_memory_request(
    *,
    manifest_hash: str = "a" * 64,
    robustness_hash: str = "b" * 64,
    prior_memory: dict[str, object] | None = None,
    observation_id: str = "OBS-001",
    variant_id: str = "SIMPLER_SAFE",
) -> dict[str, object]:
    return {
        "version": "research_failure_memory_contract_request_v1",
        "prior_memory": prior_memory,
        "failure_observation": {
            "observation_id": observation_id,
            "variant_id": variant_id,
            "failure_category": "rejected_variant",
            "parameter_region": {"fast_sma": 4},
            "lineage_hashes": {
                "experiment_manifest_output_sha256": manifest_hash,
                "robustness_decision_output_sha256": robustness_hash,
            },
            "evidence_hashes": {
                "primary_evidence_sha256": "c" * 64,
            },
            "notes": ["review_only_failure"],
        },
    }


def _human_approval_request(
    *,
    selected_variant_id: str = "SIMPLER_SAFE",
    approval_action: str = "APPROVE",
    prior_approval: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "version": "human_approval_gate_request_v1",
        "selected_variant_id": selected_variant_id,
        "approval_action": approval_action,
        "reviewer_identity": {
            "reviewer_id": "reviewer-001",
            "reviewer_role": "research_reviewer",
        },
        "approval_timestamp": "2026-07-12T08:10:00Z",
        "expiry_policy": {
            "expiry_timestamp": "2026-07-15T08:10:00Z",
            "validation_timestamp": "2026-07-12T08:10:00Z",
        },
        "prior_approval": prior_approval,
    }


def _request() -> dict[str, object]:
    return {
        "version": "e2e_research_orchestrator_acceptance_request_v1",
        "experiment_manifest_request": _experiment_manifest_request(),
        "initial_orchestration_state_request": {
            "version": "orchestration_state_contract_request_v1",
            "target_state": "CREATED",
            "reason": "created",
        },
        "robustness_pipeline_request": _robustness_pipeline_request(),
        "revise_retest_request": {
            "version": "bounded_revise_retest_loop_request_v1",
            "steps": [],
        },
        "failure_memory_request": _failure_memory_request(),
        "human_approval_request": _human_approval_request(),
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_e2e_research_orchestrator_acceptance(copy.deepcopy(request))


def _bind_failure_memory_request(request: dict[str, object], *, observation_id: str = "OBS-001") -> None:
    manifest = execution.build_experiment_manifest_contract(
        {**request["experiment_manifest_request"], "provenance": request["provenance"]}
    )
    pipeline = run_e2e_review_pipeline_acceptance(
        {**request["robustness_pipeline_request"], "provenance": request["provenance"]}
    )
    request["failure_memory_request"] = _failure_memory_request(  # type: ignore[index]
        manifest_hash=manifest["output_payload_sha256"],
        robustness_hash=pipeline["robustness_decision_result"]["output_payload_sha256"],
        prior_memory=request["failure_memory_request"].get("prior_memory"),  # type: ignore[index]
        observation_id=observation_id,
    )


def _stage_artifacts_for_success() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    request = _request()
    manifest = execution.build_experiment_manifest_contract(
        {
            **request["experiment_manifest_request"],
            "provenance": request["provenance"],
        }
    )
    pipeline = run_e2e_review_pipeline_acceptance(
        {
            **request["robustness_pipeline_request"],
            "provenance": request["provenance"],
        }
    )
    created = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": None,
            "target_state": "CREATED",
            "reason": "created",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": request["provenance"],
        }
    )
    baseline = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": created,
            "target_state": "BASELINE_REVIEW_REQUIRED",
            "reason": "baseline_review_required",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": request["provenance"],
        }
    )
    robustness = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": baseline,
            "target_state": "ROBUSTNESS_REVIEW_REQUIRED",
            "reason": "robustness_review_required",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": request["provenance"],
        }
    )
    human_required = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": robustness,
            "target_state": "HUMAN_APPROVAL_REQUIRED",
            "reason": "robustness_review_passed",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
                "robustness_decision_output_sha256": pipeline["robustness_decision_result"]["output_payload_sha256"],
            },
            "provenance": request["provenance"],
        }
    )
    return manifest, pipeline, human_required


def _approved_prior_approval() -> dict[str, object]:
    manifest, pipeline, orchestration_state = _stage_artifacts_for_success()
    return execution.build_human_approval_gate(
        {
            "version": "human_approval_gate_request_v1",
            "experiment_manifest": manifest,
            "robustness_decision_result": pipeline["robustness_decision_result"],
            "orchestration_state": orchestration_state,
            "selected_variant_id": pipeline["robustness_decision_result"]["selected_variant_id"],
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
            "provenance": {"source": "unit_test"},
        }
    )


def _rejected_prior_approval() -> dict[str, object]:
    manifest, pipeline, orchestration_state = _stage_artifacts_for_success()
    return execution.build_human_approval_gate(
        {
            "version": "human_approval_gate_request_v1",
            "experiment_manifest": manifest,
            "robustness_decision_result": pipeline["robustness_decision_result"],
            "orchestration_state": orchestration_state,
            "selected_variant_id": pipeline["robustness_decision_result"]["selected_variant_id"],
            "approval_action": "REJECT",
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
            "provenance": {"source": "unit_test"},
        }
    )


def _prior_failure_memory(request: dict[str, object] | None = None) -> dict[str, object]:
    request = _request() if request is None else copy.deepcopy(request)
    manifest = execution.build_experiment_manifest_contract(
        {
            **request["experiment_manifest_request"],
            "provenance": request["provenance"],
        }
    )
    pipeline = run_e2e_review_pipeline_acceptance(
        {
            **request["robustness_pipeline_request"],
            "provenance": request["provenance"],
        }
    )
    failure_request = _failure_memory_request(
        manifest_hash=manifest["output_payload_sha256"],
        robustness_hash=pipeline["robustness_decision_result"]["output_payload_sha256"],
        observation_id="OBS-001",
        variant_id="SIMPLER_SAFE",
    )
    return execution.build_research_failure_memory_contract(
        {
            **failure_request,
            "experiment_manifest": manifest,
            "provenance": request["provenance"],
        }
    )


def test_deterministic_successful_review_only_flow():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["final_status"] == "ACCEPTED_REVIEW_ONLY"
    assert first["human_approval_result"]["approval_status"] == "APPROVED_FOR_NEXT_REVIEW_STAGE"
    assert first["lineage"]["selected_variant_id"] == "SIMPLER_SAFE"
    assert first["human_approval_result"]["bound_artifact_hashes"]["experiment_manifest_output_sha256"] == first["experiment_manifest"]["output_payload_sha256"]
    assert first["human_approval_result"]["bound_artifact_hashes"]["robustness_decision_output_sha256"] == first["robustness_pipeline_result"]["robustness_decision_result"]["output_payload_sha256"]
    assert first["human_approval_result"]["bound_artifact_hashes"]["orchestration_state_output_sha256"] == first["loop_result"]["final_state_artifact"]["output_payload_sha256"]
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_gate_run"] is False
    assert first["promotion_performed"] is False
    assert first["hermes_state_touched"] is False
    assert first["hetzner_state_touched"] is False
    assert first["generated_code_executed"] is False
    assert first["external_data_used"] is False
    assert first["automatic_strategy_application_performed"] is False
    assert first["production_runtime_supported"] is False


def test_one_bounded_revision_followed_by_success():
    request = _request()
    request["revise_retest_request"]["steps"] = [  # type: ignore[index]
        {
            "step_id": "retry-0",
            "target_state": "REVISION_REQUIRED",
            "reason": "candidate_revision_required",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-v1",
            "artifact_hashes": {},
        },
        {
            "step_id": "retry-1",
            "target_state": "RETEST_REQUIRED",
            "reason": "candidate_revised",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-v2",
            "artifact_hashes": {},
        },
        {
            "step_id": "retry-2",
            "target_state": "ROBUSTNESS_REVIEW_REQUIRED",
            "reason": "retest_succeeded",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": None,
            "artifact_hashes": {},
        },
        {
            "step_id": "retry-3",
            "target_state": "HUMAN_APPROVAL_REQUIRED",
            "reason": "review_only_acceptance_ready",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": None,
            "artifact_hashes": {},
        },
    ]
    result = _run(request)

    assert result["final_status"] == "ACCEPTED_REVIEW_ONLY"
    assert result["loop_result"]["revision_count"] == 1
    assert result["loop_result"]["retry_count"] == 1
    assert [item["to_state"] for item in result["final_orchestration_state"]["transition_history"]] == [
        "CREATED",
        "BASELINE_REVIEW_REQUIRED",
        "ROBUSTNESS_REVIEW_REQUIRED",
        "REVISION_REQUIRED",
        "RETEST_REQUIRED",
        "ROBUSTNESS_REVIEW_REQUIRED",
        "HUMAN_APPROVAL_REQUIRED",
        "ACCEPTED_REVIEW_ONLY",
    ]


def test_repeated_failed_proposal_rejected_by_failure_memory():
    request = _request()
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["passed"] = False  # type: ignore[index]
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["minimum_required"] = 200  # type: ignore[index]
    request["revise_retest_request"]["steps"] = [  # type: ignore[index]
        {
            "step_id": "retry-1",
            "target_state": "RETEST_REQUIRED",
            "reason": "candidate_revised",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-repeat",
            "artifact_hashes": {},
        },
        {
            "step_id": "retry-2",
            "target_state": "ROBUSTNESS_REVIEW_REQUIRED",
            "reason": "retest_failed_again",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-repeat",
            "artifact_hashes": {},
        },
    ]
    request["failure_memory_request"]["prior_memory"] = _prior_failure_memory(request)  # type: ignore[index]
    _bind_failure_memory_request(request)

    result = _run(request)

    assert result["final_status"] == "REJECTED"
    assert result["loop_result"]["repeated_proposal_detected"] is True
    assert result["failure_memory_result"]["novel_failure_recorded"] is False
    assert result["failure_memory_result"]["duplicate_failure_detected"] is True


def test_iteration_budget_exhaustion():
    request = _request()
    request["experiment_manifest_request"] = _experiment_manifest_request(iteration_budget=2)  # type: ignore[index]
    _bind_failure_memory_request(request)

    result = _run(request)

    assert result["final_status"] == "EXHAUSTED"
    assert result["human_approval_result"] is None


def test_revision_budget_exhaustion():
    request = _request()
    request["experiment_manifest_request"] = _experiment_manifest_request(revision_budget=0)  # type: ignore[index]
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["passed"] = False  # type: ignore[index]
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["minimum_required"] = 200  # type: ignore[index]
    _bind_failure_memory_request(request)

    result = _run(request)

    assert result["final_status"] == "EXHAUSTED"
    assert result["loop_result"]["final_state"] == "EXHAUSTED"


def test_retry_budget_exhaustion():
    request = _request()
    request["experiment_manifest_request"] = _experiment_manifest_request(retry_budget=0)  # type: ignore[index]
    request["revise_retest_request"]["steps"] = [  # type: ignore[index]
        {
            "step_id": "retry-0",
            "target_state": "REVISION_REQUIRED",
            "reason": "candidate_revision_required",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-v1",
            "artifact_hashes": {},
        },
        {
            "step_id": "retry-1",
            "target_state": "RETEST_REQUIRED",
            "reason": "candidate_revised",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-v2",
            "artifact_hashes": {},
        }
    ]
    _bind_failure_memory_request(request)

    result = _run(request)

    assert result["final_status"] == "EXHAUSTED"
    assert result["loop_result"]["retry_count"] == 0


def test_invalid_state_transition_fails_closed():
    request = _request()
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["passed"] = False  # type: ignore[index]
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["effective_sample_metadata"]["minimum_required"] = 200  # type: ignore[index]
    request["revise_retest_request"]["steps"] = [  # type: ignore[index]
        {
            "step_id": "bad-step-1",
            "target_state": "REVISION_REQUIRED",
            "reason": "candidate_revision_required",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": "proposal-v1",
            "artifact_hashes": {},
        },
        {
            "step_id": "bad-step-2",
            "target_state": "HUMAN_APPROVAL_REQUIRED",
            "reason": "skip_retest",
            "parent_output_sha256": "STATE_FROM_PREVIOUS_STEP",
            "proposal_fingerprint": None,
            "artifact_hashes": {},
        }
    ]

    with pytest.raises(ValueError, match="transition from REVISION_REQUIRED to HUMAN_APPROVAL_REQUIRED"):
        _run(request)


def test_approval_invalid_after_upstream_artifact_mutation():
    request = _request()
    request["human_approval_request"] = _human_approval_request(prior_approval=_approved_prior_approval())  # type: ignore[index]
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["pbo_cscv_result"]["observed_value"] = 0.09  # type: ignore[index]

    result = _run(request)

    assert result["human_approval_result"]["approval_status"] == "FAILED_VALIDATION"
    assert result["final_status"] == "FAILED_VALIDATION"


def test_human_rejection_remains_final():
    request = _request()
    request["human_approval_request"] = _human_approval_request(approval_action="APPROVE", prior_approval=_rejected_prior_approval())  # type: ignore[index]

    result = _run(request)

    assert result["final_status"] == "REJECTED"
    assert result["human_approval_result"]["approval_status"] in {"REJECTED_BY_HUMAN", "FAILED_VALIDATION"}


def test_manifest_identity_mismatch():
    request = _request()
    request["robustness_pipeline_request"]["strategy_identity"]["strategy_id"] = "DIFFERENT"  # type: ignore[index]

    with pytest.raises(ValueError, match="robustness_pipeline_request.strategy_identity.strategy_id"):
        _run(request)


def test_robustness_decision_identity_mismatch():
    request = _request()
    manifest = execution.build_experiment_manifest_contract(
        {**request["experiment_manifest_request"], "provenance": request["provenance"]}
    )
    request["failure_memory_request"] = _failure_memory_request(  # type: ignore[index]
        manifest_hash=manifest["output_payload_sha256"],
        robustness_hash="f" * 64,
    )
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["drawdown_stress_result"]["passed"] = False  # type: ignore[index]

    with pytest.raises(ValueError, match="robustness decision hash"):
        _run(request)


def test_selected_variant_mismatch():
    request = _request()
    request["human_approval_request"]["selected_variant_id"] = "BASELINE"  # type: ignore[index]

    with pytest.raises(ValueError, match="selected_variant_id"):
        _run(request)


def test_malformed_failure_memory_lineage():
    request = _request()
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["drawdown_stress_result"]["passed"] = False  # type: ignore[index]
    _bind_failure_memory_request(request)
    request["failure_memory_request"]["prior_memory"] = {  # type: ignore[index]
        "memory_contract_version": "research_failure_memory_contract_v1",
        "experiment_id": "EXP-20260712-ORCH-001",
        "strategy_identity": {
            "strategy_id": "STFP_BASE",
            "strategy_builder": "swing_trend_filtered_pullback",
            "strategy_version": "v1",
        },
        "failure_records": [
            {
                "observation_id": "OBS-001",
                "variant_id": "SIMPLER_SAFE",
                "failure_category": "rejected_variant",
                "parameter_region": {"fast_sma": 4},
                "lineage_hashes": {
                    "experiment_manifest_output_sha256": "d" * 64,
                    "robustness_decision_output_sha256": "b" * 64,
                },
                "evidence_hashes": {
                    "primary_evidence_sha256": "c" * 64,
                },
                "notes": ["review_only_failure"],
                "failure_fingerprint": _canonical_sha256(
                    {
                        "variant_id": "SIMPLER_SAFE",
                        "failure_category": "rejected_variant",
                        "parameter_region": {"fast_sma": 4},
                        "lineage_hashes": {
                            "experiment_manifest_output_sha256": "d" * 64,
                            "robustness_decision_output_sha256": "b" * 64,
                        },
                    }
                ),
            }
        ],
        "latest_failure_fingerprint": "e" * 64,
        "novel_failure_recorded": True,
        "duplicate_failure_detected": False,
        "duplicate_identity_detected": False,
        "execution_authority_granted": False,
        "persistence_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "production_runtime_supported": False,
        "input_sha256": "f" * 64,
        "provenance": {"source": "unit_test"},
        "output_payload_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="failure lineage"):
        _run(request)


def test_malformed_nested_request():
    request = _request()
    request["human_approval_request"]["expiry_policy"]["validation_timestamp"] = "invalid"  # type: ignore[index]

    with pytest.raises(ValueError, match="RFC3339"):
        _run(request)


def test_unknown_top_level_field_rejection():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)


def test_deterministic_canonical_hashes():
    first = _run(_request())
    second = _run(_request())

    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_no_side_effects_on_failure_path():
    request = _request()
    request["robustness_pipeline_request"]["robustness_decision_inputs"]["drawdown_stress_result"]["passed"] = False  # type: ignore[index]
    _bind_failure_memory_request(request)
    result = _run(request)

    assert result["final_status"] == "REJECTED"
    assert result["provider_calls_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_gate_run"] is False
    assert result["promotion_performed"] is False
    assert result["hermes_state_touched"] is False
    assert result["hetzner_state_touched"] is False
    assert result["generated_code_executed"] is False
    assert result["external_data_used"] is False
    assert result["automatic_strategy_application_performed"] is False
    assert result["production_runtime_supported"] is False
