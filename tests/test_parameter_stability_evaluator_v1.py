from __future__ import annotations

import copy

import pytest

from research_lab.execution.parameter_stability_evaluator_v1 import (
    evaluate_parameter_stability,
)


def _request() -> dict[str, object]:
    return {
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
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return evaluate_parameter_stability(copy.deepcopy(request))


def test_classifies_broad_plateau_deterministically():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["stability_classification"] == "BROAD_PLATEAU"
    assert first["provider_calls_used"] == 0
    assert first["promotion_performed"] is False
    assert first["production_runtime_supported"] is False


def test_classifies_narrow_plateau():
    request = _request()
    request["one_dimensional_results"] = [
        {"value": 1, "score": 0.10},
        {"value": 2, "score": 0.20},
        {"value": 3, "score": 0.40},
        {"value": 4, "score": 0.39},
        {"value": 5, "score": 0.15},
    ]

    result = _run(request)

    assert result["stability_classification"] == "NARROW_PLATEAU"


def test_classifies_edge_of_range():
    request = _request()
    request["baseline_value"] = 1
    request["one_dimensional_results"] = [
        {"value": 1, "score": 0.40},
        {"value": 2, "score": 0.35},
        {"value": 3, "score": 0.20},
    ]

    result = _run(request)

    assert result["stability_classification"] == "EDGE_OF_RANGE"


def test_classifies_isolated_spike():
    request = _request()
    request["one_dimensional_results"] = [
        {"value": 1, "score": 0.10},
        {"value": 2, "score": 0.11},
        {"value": 3, "score": 0.40},
        {"value": 4, "score": 0.12},
        {"value": 5, "score": 0.10},
    ]

    result = _run(request)

    assert result["stability_classification"] == "ISOLATED_SPIKE"


def test_classifies_monotonic_no_optimum():
    request = _request()
    request["one_dimensional_results"] = [
        {"value": 1, "score": 0.10},
        {"value": 2, "score": 0.20},
        {"value": 3, "score": 0.30},
        {"value": 4, "score": 0.40},
        {"value": 5, "score": 0.50},
    ]

    result = _run(request)

    assert result["stability_classification"] == "MONOTONIC_NO_OPTIMUM"


def test_classifies_unstable_when_pair_interactions_conflict():
    request = _request()
    request["pair_interactions"] = [
        {"other_parameter": "slow_sma", "other_value": 5, "score_delta": 0.01},
        {"other_parameter": "slow_sma", "other_value": 6, "score_delta": -0.12},
    ]

    result = _run(request)

    assert result["stability_classification"] == "UNSTABLE"


def test_rejects_malformed_or_incomplete_results():
    request = _request()
    request["one_dimensional_results"][0].pop("score")

    with pytest.raises(ValueError, match="score"):
        _run(request)
