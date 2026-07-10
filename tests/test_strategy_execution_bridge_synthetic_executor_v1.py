from __future__ import annotations

import copy

import pytest

import research_lab.execution as execution


def _request() -> dict[str, object]:
    return {
        "version": "strategy_execution_bridge_synthetic_executor_request_v1",
        "bridge_request": {
            "version": "strategy_execution_capability_bridge_request_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": "SYNTH_STRATEGY",
            "synthetic_bars": [
                {"timestamp": "2026-01-01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
                {"timestamp": "2026-01-02", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
                {"timestamp": "2026-01-03", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0},
                {"timestamp": "2026-01-04", "open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0},
            ],
            "strategy_signal_plan": [
                {
                    "timestamp": "2026-01-02",
                    "signal_id": "signal-1",
                    "signal_type": "entry",
                    "direction": "long",
                    "protective_exit": {"type": "fixed_stop", "stop_price": 97.0},
                },
                {
                    "timestamp": "2026-01-04",
                    "signal_id": "signal-2",
                    "signal_type": "exit",
                    "direction": "long",
                },
            ],
            "provenance": {"source": "bridge_executor_integration_test"},
        },
        "executor_config": {
            "runtime_contract_version": "risk_execution_contract_v1",
            "initial_equity": 100_000.0,
            "fixed_fractional_config": {"selected_risk_per_trade_pct": 1.0},
            "strategy_position_cap": 100_000.0,
            "portfolio_exposure_cap": 100_000.0,
            "circuit_breaker_thresholds": [{"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75}],
            "reentry_rule": {"type": "equity_recovery", "recovery_from_peak_pct": 1.0, "cooldown_days": 1},
            "fractional_units_allowed": False,
            "output_mode": "full_result",
        },
        "provenance": {"source": "bridge_executor_integration_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.run_strategy_execution_bridge_synthetic_executor(copy.deepcopy(request))


def test_valid_entry_flow_builds_executor_request_and_returns_executor_result():
    result = _run(_request())

    assert result["version"] == "strategy_execution_bridge_synthetic_executor_result_v1"
    assert result["execution_status"] == "completed"
    assert result["synthetic_data_used"] is True
    assert result["real_data_used"] is False
    assert result["executor_request"]["strategy_events"][0]["event_type"] == "entry"
    assert result["executor_request"]["protective_exits_by_event_id"]["signal-1"]["protective_exit_price"] == pytest.approx(97.0)
    assert result["executor_result"]["execution_status"] == "completed"


def test_valid_rebalance_flow_propagates_rebalance_protective_exit_into_executor_request():
    request = _request()
    request["bridge_request"]["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-02",
            "signal_id": "signal-1",
            "signal_type": "entry",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 97.0},
        },
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 98.0},
        },
        {
            "timestamp": "2026-01-04",
            "signal_id": "signal-3",
            "signal_type": "exit",
            "direction": "long",
        },
    ]

    result = _run(request)

    assert [item["event_type"] for item in result["executor_request"]["strategy_events"]] == ["entry", "rebalance", "exit"]
    assert set(result["executor_request"]["protective_exits_by_event_id"]) == {"signal-1", "signal-2"}
    assert result["executor_request"]["protective_exits_by_event_id"]["signal-2"]["protective_exit_price"] == pytest.approx(98.0)
    assert result["executor_result"]["metrics"]["rebalance_count"] == 1


def test_valid_exit_flow_omits_exit_protective_exit_from_executor_request():
    result = _run(_request())

    assert [item["event_type"] for item in result["executor_request"]["strategy_events"]] == ["entry", "exit"]
    assert "signal-2" not in result["executor_request"]["protective_exits_by_event_id"]
    assert set(result["executor_request"]["protective_exits_by_event_id"]) == {"signal-1"}


def test_missing_protective_exit_on_rebalance_fails_before_executor():
    request = _request()
    request["bridge_request"]["strategy_signal_plan"] = [
        request["bridge_request"]["strategy_signal_plan"][0],
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
        },
    ]

    with pytest.raises(ValueError, match="protective_exit is required for entry and rebalance signals"):
        _run(request)


def test_protective_exit_on_exit_fails_before_executor():
    request = _request()
    request["bridge_request"]["strategy_signal_plan"][1]["protective_exit"] = {"type": "fixed_stop", "stop_price": 100.0}

    with pytest.raises(ValueError, match="exit signals must not provide protective_exit"):
        _run(request)


def test_unordered_timestamps_fail_before_executor():
    request = _request()
    request["bridge_request"]["strategy_signal_plan"] = [
        request["bridge_request"]["strategy_signal_plan"][1],
        request["bridge_request"]["strategy_signal_plan"][0],
    ]

    with pytest.raises(ValueError, match="strategy_signal_plan timestamps must be ordered"):
        _run(request)


def test_no_real_data_provider_registry_broker_or_deployment_writes_occur():
    result = _run(_request())

    assert result["provider_calls_used"] == 0
    assert result["broker_actions_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["deployment_gate_run"] is False
    assert result["promotion_performed"] is False
    assert result["hermes_write_performed"] is False
    assert result["bridge_result"]["safe_flags"]["provider_calls_used"] == 0
    assert result["bridge_result"]["safe_flags"]["broker_actions_used"] == 0
    assert result["executor_result"]["provider_calls_used"] == 0
    assert result["executor_result"]["broker_actions_used"] == 0
