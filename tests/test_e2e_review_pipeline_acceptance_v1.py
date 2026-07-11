from __future__ import annotations

import copy

import pandas as pd

from research_lab.execution.e2e_review_pipeline_acceptance_v1 import (
    run_e2e_review_pipeline_acceptance,
)
from research_lab.execution.result_review_gate_v1 import (
    build_result_review_gate,
)


def _input_bars() -> list[dict[str, object]]:
    closes = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
        113.0,
        114.0,
        111.0,
        110.0,
        109.0,
        111.0,
        112.0,
        111.0,
        112.0,
        106.0,
    ]
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    bars: list[dict[str, object]] = []
    ranges = [2.0] * len(closes)
    ranges[20] = 6.0
    ranges[21] = 8.0
    ranges[22] = 10.0
    for index, (ts, close, span) in enumerate(zip(dates, closes, ranges, strict=True), start=1):
        bars.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
                "volume": 1_000_000 + index,
            }
        )
    return bars


def _request() -> dict[str, object]:
    return {
        "version": "e2e_review_pipeline_acceptance_request_v1",
        "symbol": "abc",
        "input_bars": _input_bars(),
        "strategy_parameters": {
            "fast_sma": 3,
            "slow_sma": 5,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
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
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_e2e_review_pipeline_acceptance(copy.deepcopy(request))


def test_full_local_review_pipeline_is_deterministic_and_review_only():
    first = _run(_request())
    second = _run(_request())

    assert first == second
    assert first["version"] == "e2e_review_pipeline_acceptance_result_v1"
    assert first["adapter_result"]["symbol"] == "SYNTH_ABC"
    assert first["strategy_contract_result"]["production_runtime_supported"] is False
    assert first["strategy_contract_result"]["supported_for_risk_overlay_execution"] is False
    assert first["bridge_result"]["version"] == "strategy_execution_capability_bridge_result_v1"
    assert first["bridge_executor_result"]["synthetic_data_used"] is True
    assert first["bridge_executor_result"]["real_data_used"] is False
    assert first["review_artifact"]["final_review_status"] == "REVIEW_REQUIRED"
    assert first["review_artifact"]["promotion_performed"] is False
    assert first["qlib_evaluation"]["final_status"] == "COMPLETED_LOCAL_STUB"
    assert first["regime_pilot_result"]["final_status"] == "COMPLETED"
    assert first["rd_agent_proposal"]["review_status"] == "REVIEW_REQUIRED"
    assert first["rd_agent_proposal"]["proposal_run"] is True
    assert all(isinstance(item, str) for item in first["rd_agent_proposal"]["candidate_hypotheses"])

    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["broker_actions_used"] == 0
    assert first["deployment_gate_run"] is False
    assert first["hermes_state_touched"] is False
    assert first["hetzner_state_touched"] is False
    assert first["promotion_performed"] is False
    assert first["production_runtime_supported"] is False


def test_failure_at_review_boundary_is_safe_and_non_promoting():
    pipeline = _run(_request())

    failed_review = build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": pipeline["adapter_result"],
            "strategy_contract_result": pipeline["strategy_contract_result"],
            "bridge_result": {**pipeline["bridge_result"], "version": "invalid"},
            "isolated_execution_result": pipeline["isolated_execution_result"],
            "provenance": {"source": "unit_test"},
        }
    )

    assert failed_review["final_review_status"] == "FAILED_VALIDATION"
    assert "bridge_result.version" in failed_review["failure_reason"]
    assert failed_review["promotion_performed"] is False
    assert failed_review["registry_write_performed"] is False
    assert failed_review["broker_actions_used"] == 0
    assert failed_review["deployment_gate_run"] is False
    assert failed_review["provider_calls_used"] == 0
    assert failed_review["hermes_state_touched"] is False
    assert failed_review["hetzner_state_touched"] is False
