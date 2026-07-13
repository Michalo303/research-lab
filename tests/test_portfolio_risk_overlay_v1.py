from __future__ import annotations

import copy
import hashlib
import itertools

import pytest

from research_lab.execution.portfolio_risk_overlay_v1 import build_portfolio_risk_overlay


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _allocation(
    allocation_id: str,
    strategy_id: str,
    symbol: str,
    *,
    capital: float = 40_000.0,
    loss_fraction: float = 0.05,
) -> dict[str, object]:
    return {
        "allocation_id": allocation_id,
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "direction": "LONG",
        "allocated_capital": capital,
        "estimated_loss_fraction": loss_fraction,
        "protective_exit": {
            "entry_price": 100.0,
            "protective_exit_price": 95.0,
            "per_unit_loss_to_protective_exit": 5.0,
            "protective_exit_type": "price_stop",
            "strategy_provenance": allocation_id,
        },
        "source_allocation_sha256": _hash(allocation_id),
        "concentration_group_ids": ["US_EQUITY"],
        "correlation_group_ids": ["RISK_ON"],
        "provenance": {"source": "unit_test"},
    }


def _request(allocations: list[dict[str, object]]) -> dict[str, object]:
    symbols = sorted({str(item["symbol"]) for item in allocations})
    return {
        "version": "portfolio_risk_overlay_request_v1",
        "as_of_timestamp": "2026-01-10T16:00:00Z",
        "current_equity": 100_000.0,
        "peak_equity": 110_000.0,
        "current_cash": 100_000.0,
        "limits": {
            "maximum_gross_exposure": 90_000.0,
            "maximum_net_exposure": 90_000.0,
            "per_asset_concentration": 60_000.0,
            "per_strategy_concentration": 60_000.0,
            "concentration_group_limits": {"US_EQUITY": 70_000.0},
            "correlation_group_limits": {"RISK_ON": 65_000.0},
            "portfolio_drawdown_limit_fraction": 0.20,
            "leverage_limit": 1.0,
            "minimum_cash": 10_000.0,
            "maximum_estimated_total_loss_at_stops": 5_000.0,
        },
        "correlation_evidence": [
            {
                "correlation_group_id": "RISK_ON",
                "symbols": symbols,
                "window_start": "2025-01-01T00:00:00Z",
                "window_end": "2026-01-09T00:00:00Z",
                "as_of_timestamp": "2026-01-10T16:00:00Z",
                "maximum_observed_correlation": 0.8,
                "evidence_sha256": _hash("risk-on-evidence"),
            }
        ],
        "allocations": allocations,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_portfolio_risk_overlay(copy.deepcopy(request))


def test_overlay_is_order_independent_and_clips_explicit_constraints():
    allocations = [_allocation("a", "A", "SPY.US"), _allocation("b", "B", "QQQ.US")]
    expected = _run(_request(allocations))
    for permutation in itertools.permutations(allocations):
        assert _run(_request(list(permutation))) == expected
    assert [item["allocated_capital"] for item in expected["accepted_allocations"]] == [40_000.0]
    assert expected["clipped_allocations"][0]["accepted_capital"] == 25_000.0
    assert expected["clipped_allocations"][0]["protective_exit"] == allocations[1]["protective_exit"]
    assert expected["clipped_allocations"][0]["correlation_group_ids"] == ["RISK_ON"]
    assert "correlation_group_limit:RISK_ON" in expected["binding_constraints"]
    assert expected["exposure_summary"]["gross_exposure"] == 65_000.0
    assert expected["estimated_loss_at_stops"] == 3_250.0
    assert expected["review_status"] == "ACCEPTED_WITH_CLIPPING"
    assert expected["execution_authority_granted"] is False
    assert expected["production_runtime_supported"] is False


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "expected_binding"),
    [
        ("maximum_gross_exposure", 50_000.0, "maximum_gross_exposure"),
        ("maximum_net_exposure", 50_000.0, "maximum_net_exposure"),
        ("per_asset_concentration", 30_000.0, "per_asset_concentration:SPY.US"),
        ("per_strategy_concentration", 30_000.0, "per_strategy_concentration:A"),
        ("minimum_cash", 70_000.0, "minimum_cash"),
        ("maximum_estimated_total_loss_at_stops", 1_000.0, "maximum_estimated_total_loss_at_stops"),
    ],
)
def test_each_portfolio_limit_clips_deterministically(limit_name, limit_value, expected_binding):
    request = _request([_allocation("a", "A", "SPY.US", capital=60_000.0)])
    request["limits"]["concentration_group_limits"] = {"US_EQUITY": 90_000.0}
    request["limits"]["correlation_group_limits"] = {"RISK_ON": 90_000.0}
    request["limits"][limit_name] = limit_value
    result = _run(request)
    assert expected_binding in result["binding_constraints"]
    assert result["clipped_allocations"]


def test_drawdown_limit_rejects_all_allocations():
    request = _request([_allocation("a", "A", "SPY.US")])
    request["limits"]["portfolio_drawdown_limit_fraction"] = 0.05
    result = _run(request)
    assert result["review_status"] == "REJECTED_DRAWDOWN_LIMIT"
    assert result["accepted_allocations"] == []
    assert result["rejected_allocations"][0]["reason"] == "PORTFOLIO_DRAWDOWN_LIMIT"


def test_explicit_group_limits_are_applied_without_symbol_inference():
    request = _request([_allocation("a", "A", "SPY.US", capital=40_000.0)])
    request["limits"]["concentration_group_limits"] = {"US_EQUITY": 20_000.0}
    result = _run(request)
    assert result["clipped_allocations"][0]["accepted_capital"] == 20_000.0
    assert result["concentration_summary"]["concentration_groups"] == [
        {"group_id": "US_EQUITY", "exposure": 20_000.0}
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request["limits"].update(leverage_limit=1.1), "leverage"),
        (lambda request: request.update(current_equity=float("nan")), "current_equity"),
        (lambda request: request["allocations"][0].update(direction="SHORT"), "direction"),
        (lambda request: request["allocations"][0].update(protective_exit=None), "protective_exit"),
        (lambda request: request["allocations"][0].update(estimated_loss_fraction=0.0), "estimated_loss_fraction"),
        (lambda request: request["allocations"][0].update(concentration_group_ids=[]), "concentration group"),
        (lambda request: request["allocations"][0].update(correlation_group_ids=[]), "correlation group"),
        (lambda request: request.update(correlation_evidence=[]), "correlation evidence"),
        (
            lambda request: request["correlation_evidence"][0].update(
                window_end="2026-01-11T00:00:00Z"
            ),
            "future",
        ),
        (
            lambda request: request["correlation_evidence"][0].update(symbols=["QQQ.US"]),
            "symbol",
        ),
        (lambda request: request["allocations"].append(copy.deepcopy(request["allocations"][0])), "duplicate"),
    ],
)
def test_invalid_limits_lineage_exit_and_correlation_evidence_fail_closed(mutate, message):
    request = _request([_allocation("a", "A", "SPY.US")])
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)
