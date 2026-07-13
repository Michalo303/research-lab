from __future__ import annotations

import copy
import hashlib
import itertools

import pytest

from research_lab.execution.portfolio_position_sizing_contract_v1 import (
    build_portfolio_position_sizing_contract,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _allocation(allocation_id: str, strategy_id: str, symbol: str) -> dict[str, object]:
    return {
        "allocation_id": allocation_id,
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "allocated_capital": 50_000.0,
        "price_evidence": {
            "symbol": symbol,
            "price": 100.0,
            "observed_at": "2026-01-10T15:00:00Z",
            "source_input_sha256": _hash(f"{symbol}-price"),
        },
        "protective_exit": {
            "entry_price": 100.0,
            "protective_exit_price": 95.0,
            "per_unit_loss_to_protective_exit": 5.0,
            "protective_exit_type": "price_stop",
            "strategy_provenance": allocation_id,
        },
        "per_unit_risk": 5.0,
        "atr_evidence": {
            "symbol": symbol,
            "atr": 2.0,
            "observed_at": "2026-01-10T15:00:00Z",
            "source_input_sha256": _hash(f"{symbol}-atr"),
        },
        "volatility_evidence": {
            "symbol": symbol,
            "annualized_volatility": 0.20,
            "observed_at": "2026-01-10T15:00:00Z",
            "window_start": "2025-01-01T00:00:00Z",
            "source_input_sha256": _hash(f"{symbol}-vol"),
        },
        "kelly_evidence": {
            "symbol": symbol,
            "enabled": True,
            "candidate_only": True,
            "win_probability": 0.60,
            "payoff_ratio": 1.5,
            "sample_size": 200,
            "observed_at": "2026-01-10T15:00:00Z",
            "source_input_sha256": _hash(f"{symbol}-kelly"),
        },
        "source_allocation_sha256": _hash(allocation_id),
        "provenance": {"source": "unit_test"},
    }


def _request(
    allocations: list[dict[str, object]], *, policy: str = "FIXED_FRACTIONAL_RISK"
) -> dict[str, object]:
    return {
        "version": "portfolio_position_sizing_request_v1",
        "as_of_timestamp": "2026-01-10T16:00:00Z",
        "policy": policy,
        "total_research_capital": 100_000.0,
        "available_capital": 90_000.0,
        "policy_parameters": {
            "risk_fraction": 0.01,
            "atr_multiplier": 3.0,
            "target_annualized_volatility": 0.10,
            "kelly_enabled": policy == "BOUNDED_FRACTIONAL_KELLY_CANDIDATE",
            "kelly_haircut": 0.50,
            "kelly_cap_fraction": 0.20,
            "kelly_minimum_sample_size": 100,
        },
        "quantity_rounding": {"increment": 1.0, "mode": "FLOOR"},
        "allocations": allocations,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_portfolio_position_sizing_contract(copy.deepcopy(request))


@pytest.mark.parametrize(
    ("policy", "quantity", "notional", "risk_used"),
    [
        ("FIXED_FRACTIONAL_RISK", 100.0, 10_000.0, 500.0),
        ("ATR_SIZING", 83.0, 8_300.0, 415.0),
        ("VOLATILITY_TARGETING", 250.0, 25_000.0, 1_250.0),
        ("BOUNDED_FRACTIONAL_KELLY_CANDIDATE", 83.0, 8_300.0, 415.0),
    ],
)
def test_supported_sizing_policies_return_review_only_quantities(policy, quantity, notional, risk_used):
    result = _run(_request([_allocation("a", "A", "SPY.US")], policy=policy))
    sized = result["review_only_quantities"][0]
    assert sized["quantity"] == quantity
    assert sized["notional"] == notional
    assert sized["risk_used"] == risk_used
    assert result["residual_cash"] == 90_000.0 - notional
    assert result["capital_reconciled"] is True
    assert result["broker_order_schema_emitted"] is False
    assert result["portfolio_authority_granted"] is False
    assert result["production_runtime_supported"] is False


def test_multiple_allocations_are_order_independent_and_cash_capped():
    allocations = [_allocation("a", "A", "SPY.US"), _allocation("b", "B", "QQQ.US")]
    request = _request(allocations, policy="VOLATILITY_TARGETING")
    request["available_capital"] = 30_000.0
    expected = _run(request)
    for permutation in itertools.permutations(allocations):
        permuted = _request(list(permutation), policy="VOLATILITY_TARGETING")
        permuted["available_capital"] = 30_000.0
        assert _run(permuted) == expected
    assert [item["notional"] for item in expected["review_only_quantities"]] == [25_000.0, 5_000.0]
    assert expected["residual_cash"] == 0.0
    assert "available_capital" in expected["binding_constraints"]


def test_protective_exit_and_lineage_are_preserved_exactly():
    allocation = _allocation("a", "A", "SPY.US")
    result = _run(_request([allocation]))
    sized = result["review_only_quantities"][0]
    assert sized["protective_exit"] == allocation["protective_exit"]
    assert sized["source_allocation_sha256"] == allocation["source_allocation_sha256"]
    assert sized["strategy_id"] == "A"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request.update(policy="RANDOM"), "policy"),
        (lambda request: request.update(total_research_capital=-1.0), "total_research_capital"),
        (lambda request: request.update(available_capital=100_001.0), "available_capital"),
        (lambda request: request["allocations"][0].update(per_unit_risk=4.0), "per_unit_risk"),
        (lambda request: request["allocations"][0].update(protective_exit=None), "protective_exit"),
        (
            lambda request: request["allocations"][0]["price_evidence"].update(
                observed_at="2026-01-10T17:00:00Z"
            ),
            "future",
        ),
        (lambda request: request["allocations"][0]["price_evidence"].update(price=float("nan")), "price"),
        (lambda request: request["allocations"][0]["price_evidence"].update(symbol="QQQ.US"), "symbol"),
        (lambda request: request["allocations"].append(copy.deepcopy(request["allocations"][0])), "duplicate"),
        (lambda request: request["quantity_rounding"].update(mode="NEAREST"), "FLOOR"),
    ],
)
def test_invalid_inputs_fail_closed(mutate, message):
    request = _request([_allocation("a", "A", "SPY.US")])
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_atr_and_volatility_policies_require_point_in_time_evidence():
    atr_request = _request([_allocation("a", "A", "SPY.US")], policy="ATR_SIZING")
    atr_request["allocations"][0]["atr_evidence"] = None
    with pytest.raises(ValueError, match="atr_evidence"):
        _run(atr_request)

    vol_request = _request([_allocation("a", "A", "SPY.US")], policy="VOLATILITY_TARGETING")
    vol_request["allocations"][0]["volatility_evidence"] = None
    with pytest.raises(ValueError, match="volatility_evidence"):
        _run(vol_request)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request["policy_parameters"].update(kelly_enabled=False), "enabled"),
        (lambda request: request["allocations"][0]["kelly_evidence"].update(enabled=False), "enabled"),
        (lambda request: request["allocations"][0]["kelly_evidence"].update(candidate_only=False), "candidate_only"),
        (lambda request: request["allocations"][0]["kelly_evidence"].update(sample_size=99), "sample"),
        (lambda request: request["policy_parameters"].update(kelly_cap_fraction=1.1), "kelly_cap_fraction"),
        (lambda request: request["policy_parameters"].update(kelly_haircut=0.0), "kelly_haircut"),
    ],
)
def test_kelly_is_explicit_candidate_only_capped_haircut_and_sample_gated(mutate, message):
    request = _request(
        [_allocation("a", "A", "SPY.US")],
        policy="BOUNDED_FRACTIONAL_KELLY_CANDIDATE",
    )
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)
