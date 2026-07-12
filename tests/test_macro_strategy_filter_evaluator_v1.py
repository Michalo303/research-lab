from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from research_lab.execution.macro_strategy_filter_evaluator_v1 import (
    build_macro_strategy_filter_evaluator,
)


MODULE_PATH = Path("research_lab/execution/macro_strategy_filter_evaluator_v1.py")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _market_bars() -> list[dict[str, object]]:
    rows = [
        ("2026-01-01", 100.0, 100.0),
        ("2026-01-02", 100.0, 102.0),
        ("2026-01-03", 102.0, 104.0),
        ("2026-01-04", 104.0, 103.0),
        ("2026-01-05", 103.0, 101.0),
        ("2026-01-06", 101.0, 99.0),
        ("2026-01-07", 99.0, 100.0),
        ("2026-01-08", 100.0, 101.0),
    ]
    return [
        {
            "timestamp": timestamp,
            "open": open_price,
            "high": max(open_price, close_price) + 1.0,
            "low": min(open_price, close_price) - 1.0,
            "close": close_price,
        }
        for timestamp, open_price, close_price in rows
    ]


def _baseline_signal_sequence() -> list[dict[str, object]]:
    strategy_identity = "STRAT-1"
    baseline_variant_id = "BASELINE_SAFE"
    market_data_identity = "spy-synth-daily-v1"
    return [
        {
            "timestamp": "2026-01-01",
            "signal_id": "sig-1",
            "signal_type": "entry",
            "target_direction": "long",
            "target_exposure": 1.0,
            "strategy_identity": strategy_identity,
            "baseline_variant_id": baseline_variant_id,
            "symbol": "SYNTH_SPY",
            "market_data_identity": market_data_identity,
            "protective_exit": {"type": "fixed_stop", "stop_price": 95.0},
        },
        {
            "timestamp": "2026-01-02",
            "signal_id": "sig-2",
            "signal_type": "rebalance",
            "target_direction": "long",
            "target_exposure": 1.0,
            "strategy_identity": strategy_identity,
            "baseline_variant_id": baseline_variant_id,
            "symbol": "SYNTH_SPY",
            "market_data_identity": market_data_identity,
            "protective_exit": {"type": "fixed_stop", "stop_price": 97.0},
        },
        {
            "timestamp": "2026-01-03",
            "signal_id": "sig-3",
            "signal_type": "exit",
            "target_direction": "flat",
            "target_exposure": 0.0,
            "strategy_identity": strategy_identity,
            "baseline_variant_id": baseline_variant_id,
            "symbol": "SYNTH_SPY",
            "market_data_identity": market_data_identity,
            "protective_exit": None,
        },
        {
            "timestamp": "2026-01-04",
            "signal_id": "sig-4",
            "signal_type": "entry",
            "target_direction": "long",
            "target_exposure": 1.0,
            "strategy_identity": strategy_identity,
            "baseline_variant_id": baseline_variant_id,
            "symbol": "SYNTH_SPY",
            "market_data_identity": market_data_identity,
            "protective_exit": {"type": "fixed_stop", "stop_price": 99.0},
        },
        {
            "timestamp": "2026-01-06",
            "signal_id": "sig-5",
            "signal_type": "exit",
            "target_direction": "flat",
            "target_exposure": 0.0,
            "strategy_identity": strategy_identity,
            "baseline_variant_id": baseline_variant_id,
            "symbol": "SYNTH_SPY",
            "market_data_identity": market_data_identity,
            "protective_exit": None,
        },
    ]


def _macro_regime_candidate() -> dict[str, object]:
    result = {
        "version": "macro_regime_filter_candidate_result_v1",
        "candidate_version": "macro_regime_filter_candidate_v1",
        "candidate_id": "macro-regime-v1",
        "mode": "deterministic_rules",
        "regime_observations": [
            {
                "timestamp": "2026-01-01",
                "feature_availability_timestamps_utc": {"growth_z": "2025-12-31T18:00:00Z"},
                "regime_label": "RISK_SUPPORTIVE",
                "deterministic_score": 1.5,
                "supporting_feature_ids": ["growth_z"],
                "conflicting_feature_ids": [],
                "unavailable_feature_ids": [],
            },
            {
                "timestamp": "2026-01-02",
                "feature_availability_timestamps_utc": {"growth_z": "2026-01-01T18:00:00Z"},
                "regime_label": "NEUTRAL",
                "deterministic_score": 1.0,
                "supporting_feature_ids": ["growth_z"],
                "conflicting_feature_ids": [],
                "unavailable_feature_ids": [],
            },
            {
                "timestamp": "2026-01-04",
                "feature_availability_timestamps_utc": {"growth_z": "2026-01-03T18:00:00Z"},
                "regime_label": "RISK_RESTRICTIVE",
                "deterministic_score": 1.7,
                "supporting_feature_ids": ["growth_z", "inflation_state"],
                "conflicting_feature_ids": [],
                "unavailable_feature_ids": [],
            },
        ],
        "regime_label": "RISK_RESTRICTIVE",
        "transition_count": 2,
        "unavailable_period_count": 0,
        "macro_feature_set_hash": "a" * 64,
        "hmm_source_hash": None,
        "combination_policy": {"mode": "leave_unchanged"},
        "macro_lineage": {
            "macro_snapshot_sha256": "1" * 64,
            "alignment_output_sha256": "2" * 64,
            "feature_set_output_sha256": "3" * 64,
        },
        "candidate_only": True,
        "automatic_strategy_application_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "input_sha256": "b" * 64,
        "provenance": {"source": "unit_test"},
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _refresh_candidate_hash(request: dict[str, object]) -> None:
    candidate = request["macro_regime_candidate_result"]
    candidate["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in candidate.items() if key != "output_payload_sha256"}
    )
    request["macro_regime_candidate_output_sha256"] = candidate["output_payload_sha256"]


def _classification_policy() -> dict[str, object]:
    return {
        "risk": {"min_drawdown_improvement": 0.02, "max_return_degradation": 0.02},
        "return": {"min_return_improvement": 0.02, "max_drawdown_degradation": 0.02},
        "mixed": {"min_drawdown_improvement": 0.018383634, "min_return_improvement": 0.02},
        "no_value": {"max_abs_return_delta": 0.000001, "max_abs_drawdown_delta": 0.000001},
        "unstable": {"min_fold_pass_rate": 0.5},
    }


def _request() -> dict[str, object]:
    bars = _market_bars()
    candidate = _macro_regime_candidate()
    return {
        "version": "macro_strategy_filter_evaluator_request_v1",
        "evaluation_id": "macro-filter-eval-1",
        "strategy_identity": {
            "strategy_id": "STRAT-1",
            "strategy_version": "swing_trend_filtered_pullback_strategy_contract_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": "SYNTH_SPY",
            "allows_short": False,
        },
        "baseline_variant_identity": "BASELINE_SAFE",
        "baseline_signal_sequence": _baseline_signal_sequence(),
        "market_data_identity": "spy-synth-daily-v1",
        "market_data_sha256": _canonical_sha256(bars),
        "market_bars": bars,
        "macro_snapshot_sha256": "1" * 64,
        "alignment_output_sha256": "2" * 64,
        "feature_set_output_sha256": "3" * 64,
        "macro_regime_candidate_output_sha256": candidate["output_payload_sha256"],
        "macro_regime_candidate_result": candidate,
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
            {"window_id": "full", "start_timestamp": "2026-01-01", "end_timestamp": "2026-01-08"}
        ],
        "chronological_folds": [
            {
                "fold_id": "fold-1",
                "start_timestamp": "2026-01-01",
                "end_timestamp": "2026-01-04",
                "min_total_return": 0.0,
                "max_drawdown_limit": 0.05,
                "min_trade_count": 1,
            },
            {
                "fold_id": "fold-2",
                "start_timestamp": "2026-01-05",
                "end_timestamp": "2026-01-08",
                "min_total_return": -0.05,
                "max_drawdown_limit": 0.05,
                "min_trade_count": 0,
            },
        ],
        "transaction_cost_assumptions": {"per_unit_turnover_cost": 0.001},
        "slippage_assumptions": {"per_unit_turnover_slippage": 0.001},
        "execution_policy": {
            "initial_capital": 100000.0,
            "fill_convention": "next_open",
            "decision_to_fill_delay_bars": 1,
            "allow_same_bar_fill": False,
        },
        "classification_policy": _classification_policy(),
        "minimum_evidence_policy": {
            "min_candidate_trade_count": 1,
            "min_fold_pass_rate": 0.5,
            "min_regime_observations": 2,
        },
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_macro_strategy_filter_evaluator(copy.deepcopy(request))


def test_repeatability_and_baseline_preservation():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["baseline_unchanged"] is True
    assert first["protective_exits_preserved"] is True
    assert first["variant_results"]["DISABLED_FILTER_ABLATION"]["metrics"] == first["baseline_metrics"]
    assert first["candidate_only"] is True
    assert first["automatic_strategy_application_performed"] is False
    assert first["provider_calls_used"] == 0
    assert first["network_used"] is False
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_performed"] is False
    assert first["promotion_performed"] is False
    assert first["generated_code_executed"] is False
    assert first["production_runtime_supported"] is False


def test_block_entry_reduce_exposure_and_inverse_ablation():
    result = _run(_request())

    candidate = result["variant_results"]["MACRO_FILTER_CANDIDATE"]["metrics"]
    inverse = result["variant_results"]["INVERSE_FILTER_ABLATION"]["metrics"]

    assert candidate["blocked_entry_count"] == 1
    assert candidate["reduced_exposure_count"] == 1
    assert candidate["filter_activation_count"] == 2
    assert candidate["net_performance"] > result["baseline_metrics"]["net_performance"]
    assert inverse["net_performance"] < result["baseline_metrics"]["net_performance"]


def test_factor_boundaries_and_invalid_factors():
    zero = _request()
    zero["filter_policy"]["regime_action_map"]["NEUTRAL"] = {"action": "REDUCE_EXPOSURE", "factor": 0.0}
    zero_result = _run(zero)
    assert zero_result["variant_results"]["MACRO_FILTER_CANDIDATE"]["metrics"]["reduced_exposure_count"] == 1

    one = _request()
    one["filter_policy"]["regime_action_map"]["NEUTRAL"] = {"action": "REDUCE_EXPOSURE", "factor": 1.0}
    one_result = _run(one)
    assert one_result["variant_results"]["MACRO_FILTER_CANDIDATE"]["metrics"]["reduced_exposure_count"] == 0

    below = _request()
    below["filter_policy"]["regime_action_map"]["NEUTRAL"] = {"action": "REDUCE_EXPOSURE", "factor": -0.1}
    with pytest.raises(ValueError, match="factor"):
        _run(below)

    above = _request()
    above["filter_policy"]["regime_action_map"]["NEUTRAL"] = {"action": "REDUCE_EXPOSURE", "factor": 1.1}
    with pytest.raises(ValueError, match="factor"):
        _run(above)


def test_classification_modes_and_boundaries():
    mixed = _run(_request())
    assert mixed["classification"] == "CANDIDATE_MIXED"

    risk = _request()
    risk["classification_policy"]["mixed"]["min_return_improvement"] = 1.0
    risk["classification_policy"]["return"]["min_return_improvement"] = 1.0
    risk["classification_policy"]["risk"]["min_drawdown_improvement"] = 0.018383634
    risk_result = _run(risk)
    assert risk_result["classification"] == "CANDIDATE_IMPROVES_RISK"

    ret = _request()
    ret["classification_policy"]["mixed"]["min_drawdown_improvement"] = 1.0
    ret["classification_policy"]["risk"]["min_drawdown_improvement"] = 1.0
    ret_result = _run(ret)
    assert ret_result["classification"] == "CANDIDATE_IMPROVES_RETURN"

    no_value = _request()
    no_value["filter_policy"]["regime_action_map"] = {
        label: {"action": "ALLOW_ENTRY"} for label in no_value["filter_policy"]["regime_action_map"]
    }
    no_value_result = _run(no_value)
    assert no_value_result["classification"] == "CANDIDATE_NO_VALUE"

    unstable = _request()
    unstable["classification_policy"]["mixed"]["min_return_improvement"] = 1.0
    unstable["classification_policy"]["risk"]["min_drawdown_improvement"] = 1.0
    unstable["classification_policy"]["return"]["min_return_improvement"] = 1.0
    unstable["classification_policy"]["unstable"]["min_fold_pass_rate"] = 1.1
    unstable_result = _run(unstable)
    assert unstable_result["classification"] == "CANDIDATE_UNSTABLE"

    insufficient = _request()
    insufficient["minimum_evidence_policy"]["min_candidate_trade_count"] = 5
    insufficient_result = _run(insufficient)
    assert insufficient_result["classification"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda req: req["market_bars"].__setitem__(1, {**req["market_bars"][1], "timestamp": req["market_bars"][0]["timestamp"]}), "strictly increasing"),
        (lambda req: req["baseline_signal_sequence"].__setitem__(1, {**req["baseline_signal_sequence"][1], "timestamp": req["baseline_signal_sequence"][0]["timestamp"]}), "duplicate"),
        (lambda req: req["baseline_signal_sequence"].__setitem__(0, {**req["baseline_signal_sequence"][0], "strategy_identity": "MISMATCH"}), "strategy_identity"),
        (lambda req: req.__setitem__("macro_snapshot_sha256", "9" * 64), "macro snapshot"),
        (lambda req: req.__setitem__("alignment_output_sha256", "8" * 64), "alignment"),
        (lambda req: req.__setitem__("feature_set_output_sha256", "7" * 64), "feature-set"),
        (lambda req: req.__setitem__("macro_regime_candidate_output_sha256", "6" * 64), "regime-candidate"),
    ],
)
def test_rejects_ordering_identity_and_hash_mismatches(mutator, match):
    request = _request()
    mutator(request)

    with pytest.raises(ValueError, match=match):
        _run(request)


def test_rejects_future_candidate_runtime_and_auto_application():
    future = _request()
    future["macro_regime_candidate_result"]["regime_observations"][0]["timestamp"] = "2026-01-01T12:00:00Z"
    _refresh_candidate_hash(future)
    with pytest.raises(ValueError, match="future regime"):
        _run(future)

    future_availability = _request()
    future_availability["macro_regime_candidate_result"]["regime_observations"][0]["feature_availability_timestamps_utc"]["growth_z"] = "2026-01-02T18:00:00Z"
    _refresh_candidate_hash(future_availability)
    with pytest.raises(ValueError, match="decision boundary"):
        _run(future_availability)

    production = _request()
    production["macro_regime_candidate_result"]["production_runtime_supported"] = True
    _refresh_candidate_hash(production)
    with pytest.raises(ValueError, match="production_runtime_supported"):
        _run(production)

    automatic = _request()
    automatic["macro_regime_candidate_result"]["automatic_strategy_application_performed"] = True
    _refresh_candidate_hash(automatic)
    with pytest.raises(ValueError, match="automatic_strategy_application_performed"):
        _run(automatic)


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
