from __future__ import annotations

import copy
import hashlib
import itertools

import pytest

from research_lab.execution.portfolio_capital_allocation_contract_v1 import (
    build_portfolio_capital_allocation_contract,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(
    strategy_id: str,
    symbol: str,
    *,
    score: float = 0.5,
    loss_fraction: float = 0.05,
) -> dict[str, object]:
    return {
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "target_intent": "LONG",
        "score": score,
        "risk_evidence": {
            "estimated_loss_fraction": loss_fraction,
            "protective_exit_sha256": _hash(f"{strategy_id}-exit"),
            "source_input_sha256": _hash(f"{strategy_id}-source"),
        },
        "asset_lineage": {
            "dataset_id": f"synthetic-{symbol}",
            "symbol": symbol,
            "market_data_sha256": _hash(symbol),
        },
        "provenance": {"source": "unit_test"},
    }


def _request(
    candidates: list[dict[str, object]], *, policy: str = "EQUAL_CAPITAL"
) -> dict[str, object]:
    strategy_ids = sorted({str(item["strategy_id"]) for item in candidates})
    return {
        "version": "portfolio_capital_allocation_request_v1",
        "policy": policy,
        "total_research_capital": 100_000.0,
        "cash_reserve": 10_000.0,
        "per_strategy_maximum": 60_000.0,
        "per_asset_maximum": 60_000.0,
        "minimum_allocation": 100.0,
        "maximum_aggregate_allocation": 90_000.0,
        "leverage_policy": {"allowed": False, "maximum_gross_multiplier": 1.0},
        "deterministic_rounding": {"increment": 0.01, "mode": "FLOOR"},
        "fixed_strategy_weights": {
            strategy_id: index + 1.0 for index, strategy_id in enumerate(strategy_ids)
        },
        "candidates": candidates,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_portfolio_capital_allocation_contract(copy.deepcopy(request))


def test_equal_capital_is_order_independent_and_reconciles_cash():
    candidates = [_candidate("A", "SPY.US"), _candidate("B", "QQQ.US")]
    expected = _run(_request(candidates))
    for permutation in itertools.permutations(candidates):
        assert _run(_request(list(permutation))) == expected
    assert [item["allocated_capital"] for item in expected["research_allocations"]] == [
        45_000.0,
        45_000.0,
    ]
    assert expected["allocated_capital"] == 90_000.0
    assert expected["residual_cash"] == 10_000.0
    assert expected["capital_reconciled"] is True
    assert expected["quantities_emitted"] is False
    assert expected["broker_orders_emitted"] is False
    assert expected["automatic_allocation_application_performed"] is False
    assert expected["production_runtime_supported"] is False


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("EQUAL_CAPITAL", [45_000.0, 45_000.0]),
        ("EQUAL_RISK_BUDGET", [60_000.0, 30_000.0]),
        ("FIXED_STRATEGY_WEIGHTS", [30_000.0, 60_000.0]),
        ("BOUNDED_SCORE_WEIGHTED", [60_000.0, 30_000.0]),
    ],
)
def test_supported_policies_are_deterministic(policy, expected):
    candidates = [
        _candidate("A", "SPY.US", score=0.8, loss_fraction=0.025),
        _candidate("B", "QQQ.US", score=0.4, loss_fraction=0.05),
    ]
    request = _request(candidates, policy=policy)
    request["fixed_strategy_weights"] = {"A": 1.0, "B": 2.0}
    result = _run(request)
    assert [item["allocated_capital"] for item in result["research_allocations"]] == expected


def test_strategy_and_asset_caps_bind_without_hidden_residual():
    request = _request(
        [
            _candidate("A", "SPY.US"),
            _candidate("A", "QQQ.US"),
            _candidate("B", "SPY.US"),
        ]
    )
    request["per_strategy_maximum"] = 40_000.0
    request["per_asset_maximum"] = 35_000.0
    result = _run(request)
    assert sum(item["allocated_capital"] for item in result["research_allocations"]) == 65_000.0
    assert result["residual_cash"] == 35_000.0
    assert set(result["binding_constraints"]) == {
        "per_asset_maximum:SPY.US",
        "per_strategy_maximum:A",
    }
    assert result["capital_reconciled"] is True


def test_below_minimum_allocation_is_rejected_and_retained_as_cash():
    request = _request([_candidate("A", "SPY.US"), _candidate("B", "QQQ.US")])
    request["maximum_aggregate_allocation"] = 150.0
    result = _run(request)
    assert result["research_allocations"] == []
    assert [item["strategy_id"] for item in result["rejected_allocations"]] == ["A", "B"]
    assert result["residual_cash"] == 100_000.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request.update(policy="RANDOM"), "policy"),
        (lambda request: request.update(total_research_capital=-1.0), "total_research_capital"),
        (lambda request: request.update(cash_reserve=100_001.0), "cash_reserve"),
        (
            lambda request: request.update(
                leverage_policy={"allowed": True, "maximum_gross_multiplier": 2.0}
            ),
            "leverage",
        ),
        (lambda request: request["candidates"][0].pop("risk_evidence"), "risk_evidence"),
        (
            lambda request: request["candidates"][0]["risk_evidence"].update(
                estimated_loss_fraction=float("nan")
            ),
            "estimated_loss_fraction",
        ),
        (lambda request: request["candidates"][0].update(target_intent="SHORT"), "target_intent"),
        (
            lambda request: request["candidates"][0]["asset_lineage"].update(
                symbol="QQQ.US"
            ),
            "asset_lineage symbol",
        ),
        (lambda request: request["candidates"].append(copy.deepcopy(request["candidates"][0])), "duplicate"),
        (lambda request: request["fixed_strategy_weights"].update(A=-1.0), "weight"),
        (
            lambda request: request.update(
                maximum_aggregate_allocation=100_001.0
            ),
            "maximum_aggregate_allocation",
        ),
    ],
)
def test_invalid_capital_weights_risk_and_leverage_fail_closed(mutate, message):
    request = _request([_candidate("A", "SPY.US")])
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_fixed_policy_requires_exact_strategy_weights():
    request = _request(
        [_candidate("A", "SPY.US"), _candidate("B", "QQQ.US")],
        policy="FIXED_STRATEGY_WEIGHTS",
    )
    request["fixed_strategy_weights"] = {"A": 1.0}
    with pytest.raises(ValueError, match="weights"):
        _run(request)


def test_score_is_optional_except_for_score_weighting():
    candidate = _candidate("A", "SPY.US")
    candidate["score"] = None
    assert _run(_request([candidate]))["research_allocations"][0]["strategy_id"] == "A"

    request = _request([candidate], policy="BOUNDED_SCORE_WEIGHTED")
    with pytest.raises(ValueError, match="score"):
        _run(request)
