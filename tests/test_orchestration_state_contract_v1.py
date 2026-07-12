from __future__ import annotations

import copy
import hashlib
import json

import pytest

import research_lab.execution as execution


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest() -> dict[str, object]:
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
            "experiment_id": "EXP-20260712-STATE-001",
            "strategy_identity": strategy_identity,
            "immutable_input_hashes": immutable_input_hashes,
            "dataset_identity": dataset_identity,
            "evaluation_period_identity": evaluation_period_identity,
            "parameter_schema": parameter_schema,
            "baseline_parameter_set": baseline_parameter_set,
            "permitted_variants": permitted_variants,
            "required_evaluators": [
                "e2e_review_pipeline_acceptance_v1",
            ],
            "robustness_policy": robustness_policy,
            "complexity_budget": complexity_budget,
            "iteration_budget": 2,
            "revision_budget": 1,
            "retry_budget": 1,
            "knowledge_note_ids": ["KNIH-001"],
            "required_human_gates": ["FINAL_REVIEW_ONLY_APPROVAL"],
            "provenance": {"source": "unit_test"},
        }
    )


def _request(
    *,
    previous_state: dict[str, object] | None = None,
    target_state: str,
    reason: str,
    artifact_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "version": "orchestration_state_contract_request_v1",
        "experiment_manifest": _manifest(),
        "previous_state": previous_state,
        "target_state": target_state,
        "reason": reason,
        "artifact_hashes": artifact_hashes or {"experiment_manifest_output_sha256": _manifest()["output_payload_sha256"]},
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_orchestration_state_contract(copy.deepcopy(request))


def test_creates_deterministic_initial_state():
    first = _run(_request(target_state="CREATED", reason="initialize_manifest"))
    second = _run(_request(target_state="CREATED", reason="initialize_manifest"))

    assert first == second
    assert first["state_contract_version"] == "orchestration_state_contract_v1"
    assert first["current_state"] == "CREATED"
    assert first["transition_history"][0]["from_state"] is None
    assert first["transition_history"][0]["to_state"] == "CREATED"
    assert first["production_runtime_supported"] is False
    assert first["persistence_performed"] is False
    assert first["execution_authority_granted"] is False
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_allows_forward_progression_and_appends_transition_records():
    created = _run(_request(target_state="CREATED", reason="initialize_manifest"))
    baseline = _run(
        _request(
            previous_state=created,
            target_state="BASELINE_REVIEW_REQUIRED",
            reason="baseline_review_ready",
        )
    )
    robustness = _run(
        _request(
            previous_state=baseline,
            target_state="ROBUSTNESS_REVIEW_REQUIRED",
            reason="baseline_review_complete",
            artifact_hashes={
                **baseline["artifact_hashes"],
                "baseline_review_output_sha256": "a" * 64,
            },
        )
    )
    approval = _run(
        _request(
            previous_state=robustness,
            target_state="HUMAN_APPROVAL_REQUIRED",
            reason="robustness_review_passed",
            artifact_hashes={
                **robustness["artifact_hashes"],
                "robustness_decision_output_sha256": "b" * 64,
            },
        )
    )
    accepted = _run(
        _request(
            previous_state=approval,
            target_state="ACCEPTED_REVIEW_ONLY",
            reason="human_approval_recorded",
            artifact_hashes={
                **approval["artifact_hashes"],
                "human_approval_output_sha256": "c" * 64,
            },
        )
    )

    assert accepted["current_state"] == "ACCEPTED_REVIEW_ONLY"
    assert [item["to_state"] for item in accepted["transition_history"]] == [
        "CREATED",
        "BASELINE_REVIEW_REQUIRED",
        "ROBUSTNESS_REVIEW_REQUIRED",
        "HUMAN_APPROVAL_REQUIRED",
        "ACCEPTED_REVIEW_ONLY",
    ]
    assert accepted["transition_history"][-1]["reason"] == "human_approval_recorded"
    assert accepted["artifact_hashes"]["human_approval_output_sha256"] == "c" * 64


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("CREATED", "HUMAN_APPROVAL_REQUIRED"),
        ("BASELINE_REVIEW_REQUIRED", "ACCEPTED_REVIEW_ONLY"),
        ("ROBUSTNESS_REVIEW_REQUIRED", "BASELINE_REVIEW_REQUIRED"),
        ("REVISION_REQUIRED", "CREATED"),
        ("RETEST_REQUIRED", "HUMAN_APPROVAL_REQUIRED"),
        ("ACCEPTED_REVIEW_ONLY", "REJECTED"),
    ],
)
def test_rejects_skipped_or_backwards_transitions(from_state: str, to_state: str):
    current = _run(_request(target_state="CREATED", reason="initialize_manifest"))
    if from_state != "CREATED":
        current = _run(_request(previous_state=current, target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_review_ready"))
    if from_state == "ROBUSTNESS_REVIEW_REQUIRED":
        current = _run(_request(previous_state=current, target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_review_complete"))
    if from_state == "REVISION_REQUIRED":
        current = _run(_request(previous_state=current, target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_review_complete"))
        current = _run(_request(previous_state=current, target_state="REVISION_REQUIRED", reason="needs_revision"))
    if from_state == "RETEST_REQUIRED":
        current = _run(_request(previous_state=current, target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_review_complete"))
        current = _run(_request(previous_state=current, target_state="RETEST_REQUIRED", reason="rerun_required"))
    if from_state == "ACCEPTED_REVIEW_ONLY":
        current = _run(_request(previous_state=current, target_state="ROBUSTNESS_REVIEW_REQUIRED", reason="baseline_review_complete"))
        current = _run(_request(previous_state=current, target_state="HUMAN_APPROVAL_REQUIRED", reason="robustness_review_passed"))
        current = _run(_request(previous_state=current, target_state="ACCEPTED_REVIEW_ONLY", reason="human_approval_recorded"))

    with pytest.raises(ValueError, match="transition"):
        _run(_request(previous_state=current, target_state=to_state, reason="invalid_transition"))


def test_rejects_experiment_identity_mutation():
    created = _run(_request(target_state="CREATED", reason="initialize_manifest"))
    request = _request(previous_state=created, target_state="BASELINE_REVIEW_REQUIRED", reason="baseline_review_ready")
    request["previous_state"]["experiment_id"] = "EXP-MUTATED"  # type: ignore[index]

    with pytest.raises(ValueError, match="experiment_id"):
        _run(request)


def test_rejects_prior_artifact_hash_mutation():
    created = _run(_request(target_state="CREATED", reason="initialize_manifest"))
    request = _request(
        previous_state=created,
        target_state="BASELINE_REVIEW_REQUIRED",
        reason="baseline_review_ready",
        artifact_hashes={"experiment_manifest_output_sha256": "f" * 64},
    )

    with pytest.raises(ValueError, match="artifact_hashes"):
        _run(request)
