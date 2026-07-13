from __future__ import annotations

import copy
import hashlib
import json

import pytest

from research_lab.execution.e2e_portfolio_research_orchestrator_acceptance_v1 import (
    replay_e2e_portfolio_research_orchestrator_acceptance,
    run_e2e_portfolio_research_orchestrator_acceptance,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _signal(strategy_id: str, symbol: str, price: float, stop: float) -> dict[str, object]:
    risk = price - stop
    return {
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "signal_timestamp": "2026-01-01T16:00:00Z",
        "decision_timestamp": "2026-01-01T16:00:00Z",
        "action": "ENTRY",
        "target_intent": "LONG",
        "confidence": 0.8,
        "score": 0.7,
        "protective_exit": {
            "entry_price": price,
            "protective_exit_price": stop,
            "per_unit_loss_to_protective_exit": risk,
            "protective_exit_type": "price_stop",
            "strategy_provenance": strategy_id,
        },
        "per_unit_loss": risk,
        "source_input_sha256": _hash(f"{strategy_id}-signal"),
        "provenance": {"source": "synthetic_unit_test"},
    }


def _bars(symbol: str, base: float) -> list[dict[str, object]]:
    return [
        {
            "timestamp": f"2026-01-0{index}T16:00:00Z",
            "open": base + index - 1,
            "high": base + index + 1,
            "low": base + index - 2,
            "close": base + index,
            "volume": 1_000_000.0,
            "source_input_sha256": _hash(f"{symbol}-{index}"),
        }
        for index in range(1, 5)
    ]


def _request() -> dict[str, object]:
    signals = [
        _signal("A", "SPY.US", 100.0, 95.0),
        _signal("B", "QQQ.US", 200.0, 190.0),
    ]
    keys = ["A|A-variant|SPY.US", "B|B-variant|QQQ.US"]
    market_data = {
        "SPY.US": _bars("SPY.US", 100.0),
        "QQQ.US": _bars("QQQ.US", 200.0),
    }
    return {
        "version": "e2e_portfolio_research_orchestrator_request_v1",
        "run_id": "SYNTHETIC-PORTFOLIO-E2E-001",
        "created_at": "2026-01-01T17:00:00Z",
        "synthetic_data_only": True,
        "aggregation_request": {
            "version": "multi_strategy_signal_aggregation_request_v1",
            "as_of_timestamp": "2026-01-01T17:00:00Z",
            "maximum_signal_age_seconds": 7200,
            "conflict_policy": "MAJORITY",
            "priority_weights": {"A": 1.0, "B": 1.0},
            "allow_short": False,
            "signals": signals,
            "provenance": {"source": "synthetic_unit_test"},
        },
        "allocation_config": {
            "policy": "EQUAL_CAPITAL",
            "total_research_capital": 100_000.0,
            "cash_reserve": 10_000.0,
            "per_strategy_maximum": 60_000.0,
            "per_asset_maximum": 60_000.0,
            "minimum_allocation": 100.0,
            "maximum_aggregate_allocation": 90_000.0,
            "leverage_policy": {"allowed": False, "maximum_gross_multiplier": 1.0},
            "deterministic_rounding": {"increment": 0.01, "mode": "FLOOR"},
            "fixed_strategy_weights": {"A": 1.0, "B": 1.0},
        },
        "allocation_evidence": {
            keys[0]: {
                "score": 0.7,
                "estimated_loss_fraction": 0.02,
                "asset_lineage": {
                    "dataset_id": "synthetic-spy-v1",
                    "symbol": "SPY.US",
                    "market_data_sha256": _canonical_hash(market_data["SPY.US"]),
                },
            },
            keys[1]: {
                "score": 0.7,
                "estimated_loss_fraction": 0.02,
                "asset_lineage": {
                    "dataset_id": "synthetic-qqq-v1",
                    "symbol": "QQQ.US",
                    "market_data_sha256": _canonical_hash(market_data["QQQ.US"]),
                },
            },
        },
        "risk_overlay_config": {
            "as_of_timestamp": "2026-01-01T17:00:00Z",
            "current_equity": 100_000.0,
            "peak_equity": 100_000.0,
            "current_cash": 100_000.0,
            "limits": {
                "maximum_gross_exposure": 90_000.0,
                "maximum_net_exposure": 90_000.0,
                "per_asset_concentration": 60_000.0,
                "per_strategy_concentration": 60_000.0,
                "concentration_group_limits": {"US_EQUITY": 90_000.0},
                "correlation_group_limits": {"RISK_ON": 90_000.0},
                "portfolio_drawdown_limit_fraction": 0.20,
                "leverage_limit": 1.0,
                "minimum_cash": 10_000.0,
                "maximum_estimated_total_loss_at_stops": 5_000.0,
            },
            "correlation_evidence": [
                {
                    "correlation_group_id": "RISK_ON",
                    "symbols": ["QQQ.US", "SPY.US"],
                    "window_start": "2025-01-01T00:00:00Z",
                    "window_end": "2025-12-31T16:00:00Z",
                    "as_of_timestamp": "2026-01-01T17:00:00Z",
                    "maximum_observed_correlation": 0.8,
                    "evidence_sha256": _hash("correlation"),
                }
            ],
        },
        "risk_group_bindings": {
            key: {
                "concentration_group_ids": ["US_EQUITY"],
                "correlation_group_ids": ["RISK_ON"],
            }
            for key in keys
        },
        "sizing_config": {
            "as_of_timestamp": "2026-01-01T17:00:00Z",
            "policy": "FIXED_FRACTIONAL_RISK",
            "total_research_capital": 100_000.0,
            "available_capital": 90_000.0,
            "policy_parameters": {
                "risk_fraction": 0.01,
                "atr_multiplier": 3.0,
                "target_annualized_volatility": 0.10,
                "kelly_enabled": False,
                "kelly_haircut": 0.50,
                "kelly_cap_fraction": 0.20,
                "kelly_minimum_sample_size": 100,
            },
            "quantity_rounding": {"increment": 1.0, "mode": "FLOOR"},
        },
        "sizing_evidence": {
            keys[0]: {
                "price_evidence": {
                    "symbol": "SPY.US",
                    "price": 100.0,
                    "observed_at": "2026-01-01T16:00:00Z",
                    "source_input_sha256": _hash("spy-price"),
                },
                "atr_evidence": None,
                "volatility_evidence": None,
                "kelly_evidence": None,
            },
            keys[1]: {
                "price_evidence": {
                    "symbol": "QQQ.US",
                    "price": 200.0,
                    "observed_at": "2026-01-01T16:00:00Z",
                    "source_input_sha256": _hash("qqq-price"),
                },
                "atr_evidence": None,
                "volatility_evidence": None,
                "kelly_evidence": None,
            },
        },
        "backtest_config": {
            "initial_cash": 100_000.0,
            "execution_policy": {
                "fill_delay_bars": 1,
                "same_bar_fill": False,
                "slippage_bps": 10.0,
                "commission_per_fill": 1.0,
            },
            "market_data": market_data,
        },
        "backtest_decision_schedule": [
            {
                "decision_id": f"{key}-long",
                "allocation_key": key,
                "decision_timestamp": "2026-01-01T16:00:00Z",
                "target_intent": "LONG",
            }
            for key in keys
        ]
        + [
            {
                "decision_id": f"{key}-flat",
                "allocation_key": key,
                "decision_timestamp": "2026-01-03T16:00:00Z",
                "target_intent": "FLAT",
            }
            for key in keys
        ],
        "provenance": {"source": "synthetic_unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_e2e_portfolio_research_orchestrator_acceptance(copy.deepcopy(request))


def test_e2e_portfolio_orchestrator_composes_all_stages_and_requires_human_approval():
    request = _request()
    original = copy.deepcopy(request)
    result = _run(request)
    assert request == original
    assert result["final_status"] == "HUMAN_APPROVAL_REQUIRED"
    assert result["human_approval_gate"]["reviewer_identity"] is None
    assert result["human_approval_gate"]["approval_timestamp"] is None
    assert result["human_approval_gate"]["approval_artifact"] is None
    assert result["human_approval_gate"]["automatic_approval_performed"] is False
    assert result["backtest_result"]["cash_reconciled"] is True
    assert result["backtest_result"]["equity_reconciled"] is True
    assert result["complete_lineage"]["backtest_sha256"] == result["backtest_result"]["output_sha256"]
    assert result["broker_orders_emitted"] is False
    assert result["paper_trading_performed"] is False
    assert result["registry_write_performed"] is False
    assert result["deployment_performed"] is False
    assert result["promotion_performed"] is False
    assert result["production_runtime_supported"] is False


def test_e2e_portfolio_orchestrator_is_deterministic_and_replays_exactly():
    request = _request()
    first = _run(request)
    second = _run(request)
    assert first == second
    replay = replay_e2e_portfolio_research_orchestrator_acceptance(
        copy.deepcopy(request), expected_output_sha256=first["output_sha256"]
    )
    assert replay["replay_status"] == "REPLAY_MATCH"
    assert replay["replayed_output_sha256"] == first["output_sha256"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request.update(synthetic_data_only=False), "synthetic"),
        (lambda request: request["allocation_evidence"].pop("A|A-variant|SPY.US"), "allocation evidence"),
        (lambda request: request["risk_group_bindings"].pop("A|A-variant|SPY.US"), "risk group"),
        (lambda request: request["sizing_evidence"].pop("A|A-variant|SPY.US"), "sizing evidence"),
        (lambda request: request["backtest_decision_schedule"][0].update(allocation_key="missing"), "allocation_key"),
        (lambda request: request["aggregation_request"].update(allow_short=True), "short"),
        (
            lambda request: request["allocation_evidence"]["A|A-variant|SPY.US"][
                "asset_lineage"
            ].update(market_data_sha256="a" * 64),
            "does not bind evaluated bars",
        ),
    ],
)
def test_e2e_portfolio_orchestrator_fails_closed_on_incomplete_or_unsafe_composition(mutate, message):
    request = _request()
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_replay_mismatch_is_explicit():
    result = replay_e2e_portfolio_research_orchestrator_acceptance(
        _request(), expected_output_sha256="a" * 64
    )
    assert result["replay_status"] == "REPLAY_MISMATCH"
