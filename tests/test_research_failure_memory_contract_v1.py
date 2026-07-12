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
    parameter_schema = {"parameters": [{"name": "fast_sma", "type": "int", "minimum": 2, "maximum": 10}]}
    baseline_parameter_set = {"fast_sma": 3}
    permitted_variants = [{"variant_id": "BASELINE", "parameter_overrides": {}}]
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
            "experiment_id": "EXP-20260712-MEM-001",
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


def _failure_observation(
    *,
    observation_id: str = "OBS-001",
    failure_category: str = "excessive_pbo",
    variant_id: str = "BASELINE",
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "variant_id": variant_id,
        "failure_category": failure_category,
        "parameter_region": {"fast_sma": 3},
        "lineage_hashes": {
            "experiment_manifest_output_sha256": "a" * 64,
            "robustness_decision_output_sha256": "b" * 64,
        },
        "evidence_hashes": {
            "primary_evidence_sha256": "c" * 64,
        },
        "notes": ["review_only_failure"],
    }


def _request(
    *,
    prior_memory: dict[str, object] | None = None,
    failure_observation: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "version": "research_failure_memory_contract_request_v1",
        "experiment_manifest": _manifest(),
        "prior_memory": prior_memory,
        "failure_observation": failure_observation or _failure_observation(),
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_research_failure_memory_contract(copy.deepcopy(request))


def test_records_novel_failure_deterministically():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["memory_contract_version"] == "research_failure_memory_contract_v1"
    assert first["strategy_identity"] == _manifest()["strategy_identity"]
    assert first["novel_failure_recorded"] is True
    assert first["duplicate_failure_detected"] is False
    assert len(first["failure_records"]) == 1
    assert first["production_runtime_supported"] is False


def test_rejects_duplicate_failures_as_nonnovel_discoveries():
    first = _run(_request())
    second = _run(_request(prior_memory=first))

    assert second["novel_failure_recorded"] is False
    assert second["duplicate_failure_detected"] is True
    assert len(second["failure_records"]) == 1


def test_distinguishes_semantic_duplicate_from_exact_duplicate_identity():
    first = _run(_request())
    semantic_duplicate = _run(
        _request(
            prior_memory=first,
            failure_observation=_failure_observation(observation_id="OBS-002"),
        )
    )

    assert semantic_duplicate["duplicate_failure_detected"] is True
    assert semantic_duplicate["duplicate_identity_detected"] is False


def test_rejects_experiment_identity_mutation():
    first = _run(_request())
    request = _request(prior_memory=first)
    request["prior_memory"]["experiment_id"] = "EXP-MUTATED"  # type: ignore[index]

    with pytest.raises(ValueError, match="experiment_id"):
        _run(request)


def test_rejects_lineage_regression():
    first = _run(_request())
    request = _request(
        prior_memory=first,
        failure_observation=_failure_observation(
            observation_id="OBS-003",
            failure_category="drawdown_stress_failure",
        ),
    )
    request["failure_observation"]["lineage_hashes"]["experiment_manifest_output_sha256"] = "d" * 64  # type: ignore[index]

    with pytest.raises(ValueError, match="lineage"):
        _run(request)


def test_rejects_strategy_identity_mutation_in_prior_memory():
    first = _run(_request())
    request = _request(prior_memory=first)
    request["prior_memory"]["strategy_identity"]["strategy_version"] = "mutated"  # type: ignore[index]

    with pytest.raises(ValueError, match="strategy_identity"):
        _run(request)


def test_rejects_unknown_failure_category():
    request = _request(
        failure_observation=_failure_observation(
            failure_category="not_a_supported_failure_category",
        )
    )

    with pytest.raises(ValueError, match="failure_category"):
        _run(request)
