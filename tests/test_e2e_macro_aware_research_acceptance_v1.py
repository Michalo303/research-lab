from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import (
    run_e2e_macro_aware_research_acceptance,
)
from research_lab.execution.immutable_macro_snapshot_contract_v1 import (
    build_immutable_macro_snapshot_contract,
)
from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.macro_feature_set_contract_v1 import (
    build_macro_feature_set_contract,
)
from research_lab.execution.macro_market_asof_alignment_contract_v1 import (
    build_macro_market_asof_alignment_contract,
)
from research_lab.execution.macro_regime_filter_candidate_v1 import (
    build_macro_regime_filter_candidate,
)
from research_lab.execution.macro_series_contract_v1 import (
    build_macro_series_contract,
)
from research_lab.execution.macro_strategy_filter_evaluator_v1 import (
    build_macro_strategy_filter_evaluator,
)
from research_lab.execution.strategy_execution_capability_bridge_v1 import (
    build_strategy_execution_bridge_request,
)
from research_lab.execution.swing_trend_filtered_pullback_strategy_contract_v1 import (
    build_swing_trend_filtered_pullback_strategy_contract,
)


MODULE_PATH = Path("research_lab/execution/e2e_macro_aware_research_acceptance_v1.py")


def _market_data_request() -> dict[str, object]:
    rows = [
        ("2026-01-01T21:00:00Z", 100.0, 100.0),
        ("2026-01-02T21:00:00Z", 100.0, 102.0),
        ("2026-01-03T21:00:00Z", 102.0, 104.0),
        ("2026-01-06T21:00:00Z", 104.0, 103.0),
        ("2026-01-07T21:00:00Z", 103.0, 101.0),
        ("2026-01-08T21:00:00Z", 101.0, 99.0),
        ("2026-01-09T21:00:00Z", 99.0, 100.0),
        ("2026-01-10T21:00:00Z", 100.0, 101.0),
    ]
    return {
        "version": "isolated_real_data_adapter_contract_request_v1",
        "symbol": "spy",
        "input_bars": [
            {
                "timestamp": timestamp,
                "open": open_price,
                "high": max(open_price, close_price) + 1.0,
                "low": min(open_price, close_price) - 1.0,
                "close": close_price,
                "volume": 1000.0,
            }
            for timestamp, open_price, close_price in rows
        ],
        "provenance": {"source": "unit_test"},
    }


def _macro_series_requests() -> list[dict[str, object]]:
    return [
        {
            "version": "macro_series_contract_request_v1",
            "provider": "FRED",
            "series_id": "GROWTH",
            "frequency": "monthly",
            "units": "index",
            "observations": [
                {
                    "observation_date": "2025-12-01",
                    "value": 1.2,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2025-12-31",
                        "available_timestamp_utc": "2025-12-31T18:00:00Z",
                    },
                },
                {
                    "observation_date": "2026-01-01",
                    "value": 0.0,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2026-01-01",
                        "available_timestamp_utc": "2026-01-01T18:00:00Z",
                    },
                },
                {
                    "observation_date": "2026-01-02",
                    "value": -1.3,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2026-01-03",
                        "available_timestamp_utc": "2026-01-03T18:00:00Z",
                    },
                },
            ],
            "provenance": {"source": "unit_test"},
        },
        {
            "version": "macro_series_contract_request_v1",
            "provider": "FRED",
            "series_id": "INFLATION_STATE",
            "frequency": "monthly",
            "units": "bucket",
            "observations": [
                {
                    "observation_date": "2025-12-01",
                    "value": 0.0,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2025-12-31",
                        "available_timestamp_utc": "2025-12-31T18:00:00Z",
                    },
                },
                {
                    "observation_date": "2026-01-01",
                    "value": 1.0,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2026-01-01",
                        "available_timestamp_utc": "2026-01-01T18:00:00Z",
                    },
                },
                {
                    "observation_date": "2026-01-02",
                    "value": 2.0,
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": "2026-01-03",
                        "available_timestamp_utc": "2026-01-03T18:00:00Z",
                    },
                },
            ],
            "provenance": {"source": "unit_test"},
        },
    ]


def _macro_snapshot_request() -> dict[str, object]:
    return {
        "version": "immutable_macro_snapshot_contract_request_v1",
        "snapshot_id": "macro-snapshot-2026-01-10",
        "snapshot_date": "2026-01-10",
    }


def _macro_alignment_request() -> dict[str, object]:
    return {
        "version": "macro_market_asof_alignment_contract_request_v1",
        "market_timezone": "America/New_York",
        "decision_timestamp_convention": "LOCAL_TIME_ON_BAR_DATE",
        "decision_time_local": "09:30:00",
        "macro_availability_convention": "AT_START_OF_DAY",
        "minimum_release_lag_minutes": 0,
        "maximum_staleness_days": 40,
        "missing_data_policy": "MARK_MISSING",
        "unsafe_series_policy": "REJECT",
    }


def _macro_feature_request() -> dict[str, object]:
    return {
        "version": "macro_feature_set_contract_request_v1",
        "feature_definitions": [
            {"feature_id": "growth_z", "operation": "level", "source_series_id": "FRED:GROWTH", "minimum_observations": 1},
            {
                "feature_id": "inflation_state",
                "operation": "bounded_categorical_state",
                "source_series_id": "FRED:INFLATION_STATE",
                "bounds": [0.5, 1.5],
                "labels": ["LOW", "MID", "HIGH"],
                "minimum_observations": 1,
            },
        ],
        "missing_data_policy": "MARK_MISSING",
        "clipping_policy": {"mode": "NONE"},
    }


def _macro_regime_request() -> dict[str, object]:
    return {
        "version": "macro_regime_filter_candidate_request_v1",
        "candidate_id": "macro-regime-v1",
        "mode": "deterministic_rules",
        "state_policy": {
            "allowed_regime_labels": [
                "RISK_SUPPORTIVE",
                "NEUTRAL",
                "RISK_RESTRICTIVE",
                "INSUFFICIENT_EVIDENCE",
            ],
            "label_policies": {
                "RISK_SUPPORTIVE": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {"feature_id": "growth_z", "operation": "greater_than", "threshold": 0.5, "weight": 1.0},
                        {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "LOW", "weight": 0.6},
                    ],
                },
                "NEUTRAL": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {"feature_id": "growth_z", "operation": "between_inclusive", "lower": -0.25, "upper": 0.25, "weight": 1.0},
                        {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "MID", "weight": 0.5},
                    ],
                },
                "RISK_RESTRICTIVE": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {"feature_id": "growth_z", "operation": "less_than", "threshold": -0.5, "weight": 1.0},
                        {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "HIGH", "weight": 0.6},
                    ],
                },
            },
        },
        "minimum_supporting_features": 1,
        "minimum_available_features": 1,
        "transition_policy": {"count_label_changes": True},
        "confidence_policy": {"max_feature_age_days": 2},
    }


def _strategy_request() -> dict[str, object]:
    return {
        "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
        "strategy_parameters": {
            "fast_sma": 2,
            "slow_sma": 3,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
        },
    }


def _macro_filter_evaluation_request() -> dict[str, object]:
    return {
        "version": "macro_strategy_filter_evaluator_request_v1",
        "evaluation_id": "macro-filter-eval-1",
        "strategy_identity": {
            "strategy_id": "STFP_BASE",
            "strategy_version": "swing_trend_filtered_pullback_strategy_contract_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": "SYNTH_SPY",
            "allows_short": False,
        },
        "baseline_variant_identity": "BASELINE_SAFE",
        "market_data_identity": "spy-synth-daily-v1",
        "filter_policy": {
            "regime_action_map": {
                "RISK_SUPPORTIVE": {"action": "ALLOW_ENTRY"},
                "NEUTRAL": {"action": "REDUCE_EXPOSURE", "factor": 0.5},
                "RISK_RESTRICTIVE": {"action": "BLOCK_ENTRY"},
                "INSUFFICIENT_EVIDENCE": {"action": "LEAVE_UNCHANGED"},
            },
        },
        "ablation_policy": {
            "enable_inverse_filter": True,
            "inverse_regime_action_map": {
                "RISK_SUPPORTIVE": {"action": "BLOCK_ENTRY"},
                "NEUTRAL": {"action": "REDUCE_EXPOSURE", "factor": 1.0},
                "RISK_RESTRICTIVE": {"action": "ALLOW_ENTRY"},
                "INSUFFICIENT_EVIDENCE": {"action": "LEAVE_UNCHANGED"},
            },
        },
        "evaluation_windows": [
            {"window_id": "full", "start_timestamp": "2026-01-01", "end_timestamp": "2026-01-10"}
        ],
        "chronological_folds": [
            {"fold_id": "fold-1", "start_timestamp": "2026-01-01", "end_timestamp": "2026-01-06", "min_total_return": 0.0, "max_drawdown_limit": 0.05, "min_trade_count": 1},
            {"fold_id": "fold-2", "start_timestamp": "2026-01-07", "end_timestamp": "2026-01-10", "min_total_return": -0.05, "max_drawdown_limit": 0.05, "min_trade_count": 0},
        ],
        "transaction_cost_assumptions": {"per_unit_turnover_cost": 0.001},
        "slippage_assumptions": {"per_unit_turnover_slippage": 0.001},
        "execution_policy": {
            "initial_capital": 100000.0,
            "fill_convention": "next_open",
            "decision_to_fill_delay_bars": 1,
            "allow_same_bar_fill": False,
        },
        "classification_policy": {
            "risk": {"min_drawdown_improvement": 0.02, "max_return_degradation": 0.02},
            "return": {"min_return_improvement": 0.02, "max_drawdown_degradation": 0.02},
            "mixed": {"min_drawdown_improvement": 0.018383634, "min_return_improvement": 0.02},
            "no_value": {"max_abs_return_delta": 0.000001, "max_abs_drawdown_delta": 0.000001},
            "unstable": {"min_fold_pass_rate": 0.5},
        },
        "minimum_evidence_policy": {
            "min_candidate_trade_count": 1,
            "min_fold_pass_rate": 0.5,
            "min_regime_observations": 2,
        },
    }


def _request() -> dict[str, object]:
    request = {
        "version": "e2e_macro_aware_research_acceptance_request_v1",
        "acceptance_id": "macro-aware-acceptance-1",
        "market_data_request": _market_data_request(),
        "macro_series_requests": _macro_series_requests(),
        "macro_snapshot_request": _macro_snapshot_request(),
        "macro_alignment_request": _macro_alignment_request(),
        "macro_feature_request": _macro_feature_request(),
        "macro_regime_request": _macro_regime_request(),
        "strategy_request": _strategy_request(),
        "macro_filter_evaluation_request": _macro_filter_evaluation_request(),
        "expected_identities": {
            "market_data_identity": "spy-synth-daily-v1",
            "market_symbol": "SYNTH_SPY",
            "strategy_id": "STFP_BASE",
            "strategy_builder": "swing_trend_filtered_pullback",
            "baseline_variant_identity": "BASELINE_SAFE",
        },
        "expected_hashes": {},
        "provenance": {"source": "unit_test"},
    }
    request["expected_hashes"] = _expected_hashes(request)
    return request


def _macro_series_adapter_result(series_result: dict[str, object]) -> dict[str, object]:
    provider = str(series_result["provider"])
    series_id = str(series_result["series_id"])
    classifications = {item["point_in_time"]["classification"] for item in series_result["observations"]}
    pit_classification = "VINTAGE_AWARE" if "vintage_date_only" in classifications else "RELEASE_AWARE"
    return {
        "version": "fred_alfred_readonly_adapter_result_v1",
        "adapter_version": "fred_alfred_readonly_adapter_v1",
        "status": "SUCCESS",
        "provider": provider,
        "series_id": series_id,
        "response_sha256": series_result["input_sha256"],
        "macro_series_contract": series_result,
        "network_used": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "provenance": {"source": "unit_test"},
        "input_sha256": series_result["input_sha256"],
        "output_payload_sha256": series_result["output_payload_sha256"],
        "point_in_time_classification": pit_classification,
    }


def _expected_hashes(request: dict[str, object]) -> dict[str, str]:
    market_adapter = build_isolated_real_data_adapter_contract(copy.deepcopy(request["market_data_request"]))
    series_results = [build_macro_series_contract(copy.deepcopy(item)) for item in request["macro_series_requests"]]
    adapter_results = [_macro_series_adapter_result(item) for item in series_results]
    snapshot_result = build_immutable_macro_snapshot_contract(
        {
            **copy.deepcopy(request["macro_snapshot_request"]),
            "series_adapter_results": adapter_results,
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    alignment_result = build_macro_market_asof_alignment_contract(
        {
            **copy.deepcopy(request["macro_alignment_request"]),
            "market_bars": copy.deepcopy(request["market_data_request"]["input_bars"]),
            "macro_series_results": adapter_results,
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    feature_result = build_macro_feature_set_contract(
        {
            **copy.deepcopy(request["macro_feature_request"]),
            "aligned_macro_result": alignment_result,
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    regime_result = build_macro_regime_filter_candidate(
        {
            **copy.deepcopy(request["macro_regime_request"]),
            "macro_feature_set": feature_result,
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    strategy_result = build_swing_trend_filtered_pullback_strategy_contract(
        {
            **copy.deepcopy(request["strategy_request"]),
            "symbol": market_adapter["symbol"],
            "synthetic_bars": market_adapter["synthetic_bars"],
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    evaluator_result = build_macro_strategy_filter_evaluator(
        {
            **copy.deepcopy(request["macro_filter_evaluation_request"]),
            "baseline_signal_sequence": copy.deepcopy(strategy_result["strategy_signal_plan"]),
            "market_bars": copy.deepcopy(market_adapter["synthetic_bars"]),
            "market_data_sha256": market_adapter["output_payload_sha256"],
            "macro_snapshot_sha256": snapshot_result["output_payload_sha256"],
            "alignment_output_sha256": alignment_result["output_payload_sha256"],
            "feature_set_output_sha256": feature_result["output_payload_sha256"],
            "macro_regime_candidate_output_sha256": regime_result["output_payload_sha256"],
            "macro_regime_candidate_result": regime_result,
            "provenance": copy.deepcopy(request["provenance"]),
        }
    )
    return {
        "market_data_sha256": market_adapter["output_payload_sha256"],
        "macro_snapshot_sha256": snapshot_result["output_payload_sha256"],
        "alignment_output_sha256": alignment_result["output_payload_sha256"],
        "feature_set_output_sha256": feature_result["output_payload_sha256"],
        "macro_regime_candidate_output_sha256": regime_result["output_payload_sha256"],
        "evaluator_output_sha256": evaluator_result["output_payload_sha256"],
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_e2e_macro_aware_research_acceptance(copy.deepcopy(request))


def test_deterministic_success_and_replay_equality():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["status"] == "ACCEPTED_REVIEW_ONLY"
    assert first["evaluator_classification"] == "CANDIDATE_MIXED"
    assert first["baseline_preservation_proof"]["baseline_unchanged"] is True
    assert first["protective_exit_preservation_proof"]["protective_exits_preserved"] is True
    assert first["no_look_ahead_proof"]["no_future_release_used"] is True
    assert first["no_look_ahead_proof"]["no_future_feature_used"] is True
    assert first["no_look_ahead_proof"]["no_future_regime_used"] is True
    assert first["no_look_ahead_proof"]["no_future_market_fill_used"] is True
    assert first["safety_flags"]["provider_calls_used"] == 0
    assert first["safety_flags"]["network_used"] is False
    assert first["safety_flags"]["registry_write_performed"] is False
    assert first["safety_flags"]["broker_actions_used"] == 0
    assert first["safety_flags"]["deployment_performed"] is False
    assert first["safety_flags"]["promotion_performed"] is False
    assert first["safety_flags"]["generated_code_executed"] is False
    assert first["safety_flags"]["automatic_strategy_application_performed"] is False
    assert first["safety_flags"]["production_runtime_supported"] is False


def test_review_required_classifications_propagate():
    no_value = _request()
    no_value["macro_filter_evaluation_request"]["filter_policy"]["regime_action_map"] = {
        label: {"action": "ALLOW_ENTRY"} for label in no_value["macro_filter_evaluation_request"]["filter_policy"]["regime_action_map"]
    }
    no_value["expected_hashes"] = _expected_hashes(no_value)
    no_value_result = _run(no_value)
    assert no_value_result["status"] == "REVIEW_REQUIRED"
    assert no_value_result["evaluator_classification"] == "CANDIDATE_NO_VALUE"

    insufficient = _request()
    insufficient["macro_filter_evaluation_request"]["minimum_evidence_policy"]["min_candidate_trade_count"] = 5
    insufficient["expected_hashes"] = _expected_hashes(insufficient)
    insufficient_result = _run(insufficient)
    assert insufficient_result["status"] == "REVIEW_REQUIRED"
    assert insufficient_result["evaluator_classification"] == "INSUFFICIENT_EVIDENCE"


def test_hash_mismatch_and_identity_mismatch_fail_closed():
    bad_hash = _request()
    bad_hash["expected_hashes"]["macro_snapshot_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="macro snapshot hash"):
        _run(bad_hash)

    bad_identity = _request()
    bad_identity["expected_identities"]["strategy_id"] = "BROKEN"
    with pytest.raises(ValueError, match="strategy identity"):
        _run(bad_identity)


def test_rejects_production_provider_and_automatic_application_child_results(monkeypatch):
    import research_lab.execution.e2e_macro_aware_research_acceptance_v1 as module

    original_snapshot = module.build_immutable_macro_snapshot_contract
    original_regime = module.build_macro_regime_filter_candidate

    def bad_snapshot(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        result["safe_flags"]["provider_calls_used"] = 1
        return result

    monkeypatch.setattr(module, "build_immutable_macro_snapshot_contract", bad_snapshot)
    with pytest.raises(ValueError, match="provider_calls_used"):
        _run(_request())

    monkeypatch.setattr(module, "build_immutable_macro_snapshot_contract", original_snapshot)

    def bad_regime(*args, **kwargs):
        result = original_regime(*args, **kwargs)
        result["production_runtime_supported"] = True
        result["automatic_strategy_application_performed"] = True
        return result

    monkeypatch.setattr(module, "build_macro_regime_filter_candidate", bad_regime)
    with pytest.raises(ValueError, match="automatic_strategy_application_performed"):
        _run(_request())


def test_input_is_not_mutated_and_module_avoids_network_and_filesystem_imports():
    request = _request()
    before = copy.deepcopy(request)
    _run(request)
    assert request == before

    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "pathlib",
        "subprocess",
        "shutil",
        "os",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
