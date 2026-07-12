from __future__ import annotations

import copy
import hashlib
import json

import pytest

import research_lab.execution as execution


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(*, iteration_budget: int = 8, revision_budget: int = 1, retry_budget: int = 1) -> dict[str, object]:
    strategy_identity = {
        "strategy_id": "SWING_TREND_PULLBACK_BASE",
        "strategy_builder": "swing_trend_filtered_pullback",
        "strategy_version": "v1",
    }
    dataset_identity = {
        "dataset_id": "SYNTH_SPY_2020_2024",
        "data_source": "synthetic_local_bars",
        "symbol": "SYNTH_SPY",
        "bar_count": 252,
    }
    evaluation_period_identity = {
        "window_id": "WF_2020_2024",
        "train_start": "2020-01-01",
        "train_end": "2022-12-31",
        "test_start": "2023-01-01",
        "test_end": "2024-12-31",
    }
    parameter_schema = {
        "parameters": [
            {"name": "fast_sma", "type": "int", "minimum": 2, "maximum": 10},
            {"name": "slow_sma", "type": "int", "minimum": 5, "maximum": 30},
        ]
    }
    baseline_parameter_set = {
        "fast_sma": 3,
        "slow_sma": 8,
    }
    permitted_variants = [
        {"variant_id": "BASELINE", "parameter_overrides": {}},
        {"variant_id": "Faster", "parameter_overrides": {"fast_sma": 4}},
    ]
    robustness_policy = {
        "min_walk_forward_windows": 3,
        "min_walk_forward_pass_rate": 0.67,
    }
    complexity_budget = {
        "max_parameter_count": 2,
        "max_complexity_score": 4.0,
    }
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
    return execution.build_experiment_manifest_contract(
        {
            "version": "experiment_manifest_contract_request_v1",
            "experiment_id": "EXP-20260712-LOOP-001",
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
            "provenance": {"source": "unit_test"},
        }
    )


def _initial_state(manifest: dict[str, object]) -> dict[str, object]:
    return execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": None,
            "target_state": "CREATED",
            "reason": "initialize_manifest",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": {"source": "unit_test"},
        }
    )


def _step(
    *,
    step_id: str,
    target_state: str,
    reason: str,
    parent_output_sha256: str,
    proposal_fingerprint: str | None = None,
    artifact_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "step_id": step_id,
        "target_state": target_state,
        "reason": reason,
        "parent_output_sha256": parent_output_sha256,
        "artifact_hashes": artifact_hashes or {},
    }
    if proposal_fingerprint is not None:
        payload["proposal_fingerprint"] = proposal_fingerprint
    return payload


def _request(
    *,
    manifest: dict[str, object],
    initial_state: dict[str, object],
    steps: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "version": "bounded_revise_retest_loop_request_v1",
        "experiment_manifest": manifest,
        "initial_state": initial_state,
        "steps": steps,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.run_bounded_revise_retest_loop(copy.deepcopy(request))


def test_immediate_acceptance_is_deterministic_and_review_only():
    manifest = _manifest()
    created = _initial_state(manifest)
    baseline = _step(
        step_id="step-1",
        target_state="BASELINE_REVIEW_REQUIRED",
        reason="baseline_ready",
        parent_output_sha256=created["output_payload_sha256"],
    )
    request = _request(
        manifest=manifest,
        initial_state=created,
        steps=[
            baseline,
            _step(
                step_id="step-2",
                target_state="ROBUSTNESS_REVIEW_REQUIRED",
                reason="baseline_complete",
                parent_output_sha256="STATE_FROM_PREVIOUS_STEP",
                proposal_fingerprint="proposal-a",
                artifact_hashes={"baseline_review_output_sha256": "a" * 64},
            ),
            _step(
                step_id="step-3",
                target_state="HUMAN_APPROVAL_REQUIRED",
                reason="robustness_passed",
                parent_output_sha256="STATE_FROM_PREVIOUS_STEP",
                artifact_hashes={"robustness_decision_output_sha256": "b" * 64},
            ),
            _step(
                step_id="step-4",
                target_state="ACCEPTED_REVIEW_ONLY",
                reason="human_approval_recorded",
                parent_output_sha256="STATE_FROM_PREVIOUS_STEP",
                artifact_hashes={"human_approval_output_sha256": "c" * 64},
            ),
        ],
    )

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["final_state"] == "ACCEPTED_REVIEW_ONLY"
    assert first["iteration_count"] == 4
    assert first["revision_count"] == 0
    assert first["retry_count"] == 0
    assert first["repeated_proposal_detected"] is False
    assert first["production_runtime_supported"] is False
    assert first["generated_code_executed"] is False


def test_one_revision_then_acceptance():
    manifest = _manifest(iteration_budget=8, revision_budget=1, retry_budget=1)
    created = _initial_state(manifest)
    request = _request(
        manifest=manifest,
        initial_state=created,
        steps=[
            _step(step_id="step-1", target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_ready", parent_output_sha256=created["output_payload_sha256"]),
            _step(step_id="step-2", target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_complete", parent_output_sha256="STATE_FROM_PREVIOUS_STEP", proposal_fingerprint="proposal-a"),
            _step(step_id="step-3", target_state="REVISION_REQUIRED", reason="needs_revision", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
            _step(step_id="step-4", target_state="RETEST_REQUIRED", reason="revision_submitted", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
            _step(step_id="step-5", target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="retest_complete", parent_output_sha256="STATE_FROM_PREVIOUS_STEP", proposal_fingerprint="proposal-b"),
            _step(step_id="step-6", target_state="HUMAN_APPROVAL_REQUIRED", reason="robustness_passed", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
            _step(step_id="step-7", target_state="ACCEPTED_REVIEW_ONLY", reason="human_approval_recorded", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
        ],
    )

    result = _run(request)

    assert result["final_state"] == "ACCEPTED_REVIEW_ONLY"
    assert result["revision_count"] == 1
    assert result["retry_count"] == 1


def test_repeated_identical_proposal_is_rejected():
    manifest = _manifest()
    created = _initial_state(manifest)
    result = _run(
        _request(
            manifest=manifest,
            initial_state=created,
            steps=[
                _step(step_id="step-1", target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_ready", parent_output_sha256=created["output_payload_sha256"]),
                _step(step_id="step-2", target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_complete", parent_output_sha256="STATE_FROM_PREVIOUS_STEP", proposal_fingerprint="proposal-a"),
                _step(step_id="step-3", target_state="REVISION_REQUIRED", reason="needs_revision", parent_output_sha256="STATE_FROM_PREVIOUS_STEP", proposal_fingerprint="proposal-a"),
            ],
        )
    )

    assert result["final_state"] == "REJECTED"
    assert result["repeated_proposal_detected"] is True


def test_revision_budget_exhaustion_transitions_to_exhausted():
    manifest = _manifest(revision_budget=0)
    created = _initial_state(manifest)

    result = _run(
        _request(
            manifest=manifest,
            initial_state=created,
            steps=[
                _step(step_id="step-1", target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_ready", parent_output_sha256=created["output_payload_sha256"]),
                _step(step_id="step-2", target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_complete", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
                _step(step_id="step-3", target_state="REVISION_REQUIRED", reason="needs_revision", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
            ],
        )
    )

    assert result["final_state"] == "EXHAUSTED"


def test_retry_budget_exhaustion_transitions_to_exhausted():
    manifest = _manifest(retry_budget=0)
    created = _initial_state(manifest)

    result = _run(
        _request(
            manifest=manifest,
            initial_state=created,
            steps=[
                _step(step_id="step-1", target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_ready", parent_output_sha256=created["output_payload_sha256"]),
                _step(step_id="step-2", target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_complete", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
                _step(step_id="step-3", target_state="RETEST_REQUIRED", reason="rerun_required", parent_output_sha256="STATE_FROM_PREVIOUS_STEP"),
            ],
        )
    )

    assert result["final_state"] == "EXHAUSTED"


def test_malformed_lineage_fails_closed():
    manifest = _manifest()
    created = _initial_state(manifest)

    with pytest.raises(ValueError, match="lineage"):
        _run(
            _request(
                manifest=manifest,
                initial_state=created,
                steps=[
                    _step(step_id="step-1", target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_ready", parent_output_sha256="0" * 64),
                ],
            )
        )


def test_state_transition_violation_fails_closed():
    manifest = _manifest()
    created = _initial_state(manifest)

    with pytest.raises(ValueError, match="transition"):
        _run(
            _request(
                manifest=manifest,
                initial_state=created,
                steps=[
                    _step(step_id="step-1", target_state="ACCEPTED_REVIEW_ONLY", reason="skip_ahead", parent_output_sha256=created["output_payload_sha256"]),
                ],
            )
        )
