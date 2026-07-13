from __future__ import annotations

import copy
import hashlib

import pytest

from research_lab.execution.portfolio_backtest_acceptance_v1 import (
    run_portfolio_backtest_acceptance,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _bars(symbol: str, base: float) -> list[dict[str, object]]:
    result = []
    for index, day in enumerate(("01", "02", "03", "04")):
        price = base + index
        result.append(
            {
                "timestamp": f"2026-01-{day}T16:00:00Z",
                "open": price,
                "high": price + 2.0,
                "low": price - 2.0,
                "close": price + 1.0,
                "volume": 1_000_000.0,
                "source_input_sha256": _hash(f"{symbol}-{day}"),
            }
        )
    return result


def _decision(
    decision_id: str,
    strategy_id: str,
    symbol: str,
    timestamp: str,
    *,
    intent: str = "LONG",
    quantity: float = 10.0,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "decision_timestamp": timestamp,
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "target_intent": intent,
        "target_quantity": quantity if intent == "LONG" else 0.0,
        "protective_exit_price": 95.0 if intent == "LONG" else None,
        "stage_lineage": {
            "aggregation_sha256": _hash(f"{decision_id}-aggregation"),
            "capital_allocation_sha256": _hash(f"{decision_id}-allocation"),
            "risk_overlay_sha256": _hash(f"{decision_id}-overlay"),
            "position_sizing_sha256": _hash(f"{decision_id}-sizing"),
        },
        "rejected_signals": [f"{decision_id}-stale"],
        "rejected_allocations": [],
        "risk_limit_events": [f"{decision_id}-risk-reviewed"],
        "provenance": {"source": "unit_test"},
    }


def _request() -> dict[str, object]:
    return {
        "version": "portfolio_backtest_acceptance_request_v1",
        "synthetic_data_only": True,
        "initial_cash": 100_000.0,
        "execution_policy": {
            "fill_delay_bars": 1,
            "same_bar_fill": False,
            "slippage_bps": 10.0,
            "commission_per_fill": 1.0,
        },
        "market_data": {
            "SPY.US": _bars("SPY.US", 100.0),
            "QQQ.US": _bars("QQQ.US", 200.0),
        },
        "decisions": [
            _decision("spy-long", "A", "SPY.US", "2026-01-01T16:00:00Z"),
            _decision("qqq-long", "B", "QQQ.US", "2026-01-01T16:00:00Z", quantity=5.0),
            _decision(
                "spy-flat",
                "A",
                "SPY.US",
                "2026-01-03T16:00:00Z",
                intent="FLAT",
            ),
        ],
        "provenance": {"source": "synthetic_unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_portfolio_backtest_acceptance(copy.deepcopy(request))


def test_multi_asset_portfolio_backtest_is_deterministic_and_reconciled():
    request = _request()
    expected = _run(request)
    reordered = _request()
    reordered["decisions"] = list(reversed(reordered["decisions"]))
    reordered["market_data"] = {
        "QQQ.US": reordered["market_data"]["QQQ.US"],
        "SPY.US": reordered["market_data"]["SPY.US"],
    }
    assert _run(reordered) == expected
    assert expected["acceptance_status"] == "ACCEPTED_REVIEW_ONLY"
    assert all(fill["fill_timestamp"] > fill["decision_timestamp"] for fill in expected["fills"])
    assert expected["transaction_costs"] > 0.0
    assert expected["slippage_costs"] > 0.0
    assert expected["turnover"] > 0.0
    assert expected["cash_reconciled"] is True
    assert expected["equity_reconciled"] is True
    assert expected["chronological_execution_proof"] is True
    assert expected["no_future_data_used"] is True
    assert expected["rejected_signals"] == [
        "qqq-long-stale",
        "spy-flat-stale",
        "spy-long-stale",
    ]
    assert len(expected["risk_limit_events"]) == 3
    assert expected["broker_integration_used"] is False
    assert expected["broker_orders_emitted"] is False
    assert expected["paper_trading_performed"] is False
    assert expected["optimization_performed"] is False
    assert expected["production_runtime_supported"] is False


def test_protective_exit_fills_on_later_bar_not_entry_bar():
    request = _request()
    request["market_data"] = {"SPY.US": _bars("SPY.US", 100.0)}
    request["market_data"]["SPY.US"][2]["low"] = 94.0
    request["decisions"] = [
        _decision("spy-long", "A", "SPY.US", "2026-01-01T16:00:00Z")
    ]
    result = _run(request)
    exits = [fill for fill in result["fills"] if fill["reason"] == "PROTECTIVE_EXIT"]
    assert len(exits) == 1
    assert exits[0]["fill_timestamp"] == "2026-01-03T16:00:00Z"
    assert result["ending_positions"] == []


def test_unfilled_last_bar_decision_is_reported():
    request = _request()
    request["decisions"] = [
        _decision("late", "A", "SPY.US", "2026-01-04T16:00:00Z")
    ]
    result = _run(request)
    assert result["fills"] == []
    assert result["unfilled_decisions"][0]["decision_id"] == "late"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request.update(synthetic_data_only=False), "synthetic"),
        (lambda request: request["execution_policy"].update(same_bar_fill=True), "same_bar"),
        (lambda request: request["execution_policy"].update(fill_delay_bars=0), "fill_delay"),
        (lambda request: request["market_data"]["SPY.US"][0].update(low=103.0), "OHLC"),
        (lambda request: request["decisions"][0].update(protective_exit_price=None), "protective_exit"),
        (lambda request: request["decisions"][0].update(target_intent="SHORT"), "target_intent"),
        (lambda request: request["decisions"][0]["stage_lineage"].update(aggregation_sha256="a" * 63), "SHA-256"),
        (lambda request: request["decisions"].append(copy.deepcopy(request["decisions"][0])), "duplicate"),
        (
            lambda request: request["decisions"][0].update(
                decision_timestamp="2026-01-01T15:00:00Z"
            ),
            "market bar",
        ),
        (lambda request: request.update(initial_cash=float("nan")), "initial_cash"),
    ],
)
def test_invalid_execution_market_and_lineage_inputs_fail_closed(mutate, message):
    request = _request()
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_insufficient_cash_rejects_fill_without_implicit_leverage():
    request = _request()
    request["initial_cash"] = 100.0
    request["decisions"] = [
        _decision("spy-long", "A", "SPY.US", "2026-01-01T16:00:00Z", quantity=10.0)
    ]
    result = _run(request)
    assert result["fills"] == []
    assert result["rejected_allocations"][0]["reason"] == "INSUFFICIENT_CASH"
    assert result["ending_cash"] == 100.0
