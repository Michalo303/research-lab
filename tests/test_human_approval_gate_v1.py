from __future__ import annotations

import copy
import hashlib
import json

import pytest

import research_lab.execution as execution


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_output_sha256(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


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
    parameter_schema = {"parameters": [{"name": "fast_sma", "type": "int", "minimum": 2, "maximum": 10}]}
    baseline_parameter_set = {"fast_sma": 3}
    permitted_variants = [{"variant_id": "BASELINE", "parameter_overrides": {}}, {"variant_id": "SIMPLER_SAFE", "parameter_overrides": {"fast_sma": 4}}]
    robustness_policy = {"min_walk_forward_windows": 3}
    complexity_budget = {"max_parameter_count": 1, "max_complexity_score": 2.0}
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
            "experiment_id": "EXP-20260712-HUMAN-001",
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
            "iteration_budget": 4,
            "revision_budget": 1,
            "retry_budget": 1,
            "knowledge_note_ids": ["KNIH-001"],
            "required_human_gates": ["FINAL_REVIEW_ONLY_APPROVAL"],
            "provenance": {"source": "unit_test"},
        }
    )


def _robustness_decision_result(*, selected_variant_id: str = "SIMPLER_SAFE") -> dict[str, object]:
    return _with_output_sha256(
        {
            "version": "robustness_decision_gate_result_v1",
            "gate_version": "robustness_decision_gate_v1",
            "strategy_identity": {
                "strategy_id": "SWING_TREND_PULLBACK_BASE",
                "strategy_builder": "swing_trend_filtered_pullback",
                "symbol": "SYNTH_SPY",
                "baseline_variant_id": "BASELINE",
            },
            "decision_status": "PASS_WITH_SIMPLIFICATION",
            "selected_variant_id": selected_variant_id,
            "recommended_variant_id": selected_variant_id,
            "accepted_variants": [{"variant_id": selected_variant_id}],
            "rejected_variants": [],
            "ablation_classifications": [],
            "weak_parameters": [],
            "fold_failures": [],
            "selection_bias_findings": {"required_checks": [], "blocking_reasons": []},
            "drawdown_findings": {"required_checks": [], "blocking_reasons": []},
            "complexity_findings": {"required_parameter_checks": [], "complexity_budget": {}},
            "knowledge_note_ids_used": ["KNIH-001"],
            "missing_evidence": [],
            "blocking_reasons": [],
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_gate_run": False,
            "hermes_state_touched": False,
            "hetzner_state_touched": False,
            "promotion_performed": False,
            "production_runtime_supported": False,
            "input_sha256": "a" * 64,
            "provenance": {"source": "unit_test"},
        }
    )


def _orchestration_state(*, manifest: dict[str, object], robustness_result: dict[str, object]) -> dict[str, object]:
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
            "provenance": {"source": "unit_test"},
        }
    )
    baseline_review = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": created,
            "target_state": "BASELINE_REVIEW_REQUIRED",
            "reason": "baseline_review_required",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": {"source": "unit_test"},
        }
    )
    robustness_review = execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": baseline_review,
            "target_state": "ROBUSTNESS_REVIEW_REQUIRED",
            "reason": "robustness_review_required",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
            },
            "provenance": {"source": "unit_test"},
        }
    )
    return execution.build_orchestration_state_contract(
        {
            "version": "orchestration_state_contract_request_v1",
            "experiment_manifest": manifest,
            "previous_state": robustness_review,
            "target_state": "HUMAN_APPROVAL_REQUIRED",
            "reason": "robustness_passed",
            "artifact_hashes": {
                "experiment_manifest_output_sha256": manifest["output_payload_sha256"],
                "robustness_decision_output_sha256": robustness_result["output_payload_sha256"],
            },
            "provenance": {"source": "unit_test"},
        }
    )


def _request(
    *,
    approval_action: str = "REQUEST_APPROVAL",
    prior_approval: dict[str, object] | None = None,
) -> dict[str, object]:
    manifest = _manifest()
    robustness_result = _robustness_decision_result()
    orchestration_state = _orchestration_state(manifest=manifest, robustness_result=robustness_result)
    return {
        "version": "human_approval_gate_request_v1",
        "experiment_manifest": manifest,
        "robustness_decision_result": robustness_result,
        "orchestration_state": orchestration_state,
        "selected_variant_id": "SIMPLER_SAFE",
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
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_human_approval_gate(copy.deepcopy(request))


def test_request_requires_explicit_human_decision():
    result = _run(_request())

    assert result["approval_status"] == "APPROVAL_REQUIRED"
    assert result["selected_variant_id"] == "SIMPLER_SAFE"
    assert result["bound_artifact_hashes"]["robustness_decision_output_sha256"] == _request()["robustness_decision_result"]["output_payload_sha256"]
    assert result["production_runtime_supported"] is False


def test_approve_binds_exact_artifacts_and_reviewer_inputs():
    result = _run(_request(approval_action="APPROVE"))

    assert result["approval_status"] == "APPROVED_FOR_NEXT_REVIEW_STAGE"
    assert result["reviewer_identity"] == {
        "reviewer_id": "reviewer-001",
        "reviewer_role": "research_reviewer",
    }
    assert result["approval_timestamp"] == "2026-07-12T08:10:00Z"
    assert result["expiry_policy"]["expiry_timestamp"] == "2026-07-15T08:10:00Z"


def test_reject_records_explicit_human_rejection():
    result = _run(_request(approval_action="REJECT"))

    assert result["approval_status"] == "REJECTED_BY_HUMAN"
    assert result["invalidation_reasons"] == []


def test_expires_when_validation_timestamp_exceeds_expiry():
    request = _request(approval_action="APPROVE")
    request["expiry_policy"]["validation_timestamp"] = "2026-07-16T08:10:00Z"  # type: ignore[index]

    result = _run(request)

    assert result["approval_status"] == "EXPIRED"
    assert result["invalidation_reasons"] == ["approval_expired"]


def test_invalidates_approval_when_upstream_artifact_changes():
    approved = _run(_request(approval_action="APPROVE"))
    request = _request(approval_action="APPROVE", prior_approval=approved)
    request["orchestration_state"]["artifact_hashes"]["robustness_decision_output_sha256"] = "f" * 64  # type: ignore[index]

    result = _run(request)

    assert result["approval_status"] == "FAILED_VALIDATION"
    assert result["invalidation_reasons"] == ["upstream_artifact_mutated"]


def test_rejects_unknown_field():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)
