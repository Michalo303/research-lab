from __future__ import annotations

import copy
import hashlib
import json

import pytest

import research_lab.execution as execution


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request() -> dict[str, object]:
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
            {"name": "rsi_entry", "type": "float", "minimum": 50.0, "maximum": 90.0},
            {"name": "protective_stop_atr_multiple", "type": "float", "minimum": 0.5, "maximum": 5.0},
            {"name": "trend_filter_enabled", "type": "bool"},
        ]
    }
    baseline_parameter_set = {
        "fast_sma": 3,
        "slow_sma": 8,
        "rsi_entry": 80.0,
        "protective_stop_atr_multiple": 1.5,
        "trend_filter_enabled": True,
    }
    permitted_variants = [
        {
            "variant_id": "BASELINE",
            "parameter_overrides": {},
        },
        {
            "variant_id": "LOWER_RSI",
            "parameter_overrides": {"rsi_entry": 75.0},
        },
        {
            "variant_id": "WIDER_STOP",
            "parameter_overrides": {"protective_stop_atr_multiple": 2.0},
        },
    ]
    robustness_policy = {
        "min_walk_forward_windows": 3,
        "min_walk_forward_pass_rate": 0.67,
        "max_drawdown": -0.2,
        "max_pbo": 0.2,
    }
    complexity_budget = {
        "max_parameter_count": 5,
        "max_complexity_score": 8.0,
    }

    immutable_hash_inputs = {
        "strategy_identity": strategy_identity,
        "dataset_identity": dataset_identity,
        "evaluation_period_identity": evaluation_period_identity,
        "parameter_schema": parameter_schema,
        "baseline_parameter_set": baseline_parameter_set,
        "robustness_policy": robustness_policy,
        "complexity_budget": complexity_budget,
        "permitted_variants": permitted_variants,
    }

    immutable_input_hashes = {key: _canonical_sha256(value) for key, value in immutable_hash_inputs.items()}

    return {
        "version": "experiment_manifest_contract_request_v1",
        "experiment_id": "EXP-20260712-001",
        "strategy_identity": strategy_identity,
        "immutable_input_hashes": immutable_input_hashes,
        "dataset_identity": dataset_identity,
        "evaluation_period_identity": evaluation_period_identity,
        "parameter_schema": parameter_schema,
        "baseline_parameter_set": baseline_parameter_set,
        "permitted_variants": permitted_variants,
        "required_evaluators": [
            "e2e_review_pipeline_acceptance_v1",
            "qlib_isolated_evaluator_v1",
            "markov_hmm_regime_pilot_v1",
        ],
        "robustness_policy": robustness_policy,
        "complexity_budget": complexity_budget,
        "iteration_budget": 2,
        "revision_budget": 1,
        "retry_budget": 1,
        "knowledge_note_ids": ["KNIH-001", "KNIH-002"],
        "required_human_gates": [
            "ROBUSTNESS_REVIEW_SIGNOFF",
            "FINAL_REVIEW_ONLY_APPROVAL",
        ],
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_experiment_manifest_contract(copy.deepcopy(request))


def test_builds_deterministic_review_only_manifest():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["manifest_version"] == "experiment_manifest_contract_v1"
    assert first["experiment_id"] == "EXP-20260712-001"
    assert first["strategy_identity"]["strategy_builder"] == "swing_trend_filtered_pullback"
    assert first["production_runtime_supported"] is False
    assert first["execution_authority_granted"] is False
    assert first["persistence_performed"] is False
    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_gate_run"] is False
    assert first["promotion_performed"] is False
    assert first["hermes_state_touched"] is False
    assert first["hetzner_state_touched"] is False
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_rejects_duplicate_variant_ids():
    request = _request()
    request["permitted_variants"].append(  # type: ignore[index]
        {"variant_id": "BASELINE", "parameter_overrides": {"rsi_entry": 70.0}}
    )
    request["immutable_input_hashes"]["permitted_variants"] = _canonical_sha256(request["permitted_variants"])  # type: ignore[index]

    with pytest.raises(ValueError, match="variant_id"):
        _run(request)


def test_rejects_baseline_parameter_values_outside_schema():
    request = _request()
    request["baseline_parameter_set"]["fast_sma"] = 1  # type: ignore[index]
    request["immutable_input_hashes"]["baseline_parameter_set"] = _canonical_sha256(request["baseline_parameter_set"])  # type: ignore[index]

    with pytest.raises(ValueError, match="fast_sma"):
        _run(request)


def test_rejects_variant_overrides_outside_schema():
    request = _request()
    request["permitted_variants"][1]["parameter_overrides"]["rsi_entry"] = 95.0  # type: ignore[index]
    request["immutable_input_hashes"]["permitted_variants"] = _canonical_sha256(request["permitted_variants"])  # type: ignore[index]

    with pytest.raises(ValueError, match="rsi_entry"):
        _run(request)


def test_rejects_inconsistent_immutable_hashes():
    request = _request()
    request["immutable_input_hashes"]["dataset_identity"] = "0" * 64  # type: ignore[index]

    with pytest.raises(ValueError, match="dataset_identity"):
        _run(request)


def test_rejects_unknown_fields():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)
