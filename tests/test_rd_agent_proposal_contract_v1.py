from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

import research_lab.execution as execution


MODULE_PATH = Path("research_lab/execution/rd_agent_proposal_contract_v1.py")


def _bars() -> list[dict[str, object]]:
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
    bars = []
    ranges = [2.0] * len(closes)
    ranges[20] = 6.0
    ranges[21] = 8.0
    ranges[22] = 10.0
    for ts, close, span in zip(dates, closes, ranges, strict=True):
        bars.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d"),
                "open": close - 0.5,
                "high": close + (span / 2.0),
                "low": close - (span / 2.0),
                "close": close,
            }
        )
    return bars


def _review_artifact() -> dict[str, object]:
    adapter = execution.build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": "qqq",
            "input_bars": [{**bar, "volume": 1_000_000} for bar in _bars()],
            "provenance": {"source": "unit_test"},
        }
    )
    strategy_result = execution.build_swing_trend_filtered_pullback_strategy_contract(
        {
            "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
            "symbol": adapter["symbol"],
            "synthetic_bars": adapter["synthetic_bars"],
            "strategy_parameters": {
                "fast_sma": 2,
                "slow_sma": 3,
                "rsi_entry": 80.0,
                "rsi_exit": 85.0,
                "atr_stop": 2.0,
                "max_exposure": 0.5,
            },
            "provenance": {"source": "unit_test"},
        }
    )
    bridge = execution.build_strategy_execution_bridge_request(
        {
            "version": "strategy_execution_capability_bridge_request_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": strategy_result["symbol"],
            "synthetic_bars": strategy_result["synthetic_bars"],
            "strategy_signal_plan": strategy_result["strategy_signal_plan"],
            "provenance": {"source": "unit_test"},
        }
    )
    return execution.build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": adapter,
            "strategy_contract_result": strategy_result,
            "bridge_result": bridge,
            "provenance": {"source": "unit_test"},
        }
    )


def _qlib_result() -> dict[str, object]:
    return execution.run_qlib_isolated_evaluator(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "normalized_bars",
            "symbol": "SYNTH_QQQ",
            "normalized_bars": _bars(),
            "evaluation_mode": "deterministic_local",
            "provenance": {"source": "unit_test"},
        }
    )


def _regime_result() -> dict[str, object]:
    return execution.run_markov_hmm_regime_pilot(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "input_bars": _bars(),
            "provenance": {"source": "unit_test"},
        }
    )


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_rd_agent_proposal_contract(copy.deepcopy(request))


def test_deterministic_unavailable_result_if_rdagent_absent():
    if importlib.util.find_spec("rdagent") is not None:
        pytest.skip("local environment already has rdagent installed")
    request = {
        "version": "rd_agent_proposal_contract_request_v1",
        "review_artifact": _review_artifact(),
        "parameters": {"mode": "availability_check"},
        "provenance": {"source": "unit_test"},
    }

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["rd_agent_available"] is False
    assert first["proposal_run"] is False
    assert first["review_status"] == "UNAVAILABLE"


def test_deterministic_local_proposal_result_from_supplied_review_artifact():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["proposal_run"] is True
    assert result["review_status"] == "REVIEW_REQUIRED"
    assert result["candidate_hypotheses"]
    assert result["factor_proposals"]
    assert result["strategy_candidate_notes"]


def test_can_consume_qlib_evaluator_result():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "qlib_evaluation": _qlib_result(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert any("qlib" in item.lower() for item in result["strategy_candidate_notes"])


def test_can_consume_markov_hmm_regime_pilot_result():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "regime_pilot_result": _regime_result(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert any("regime" in item.lower() for item in result["strategy_candidate_notes"])


def test_malformed_input_fails_safely():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": {"version": "wrong"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["review_status"] == "REJECTED"
    assert result["failure_reason"] is not None


def test_no_provider_registry_broker_deployment_hermes_hetzner_promotion_or_runtime():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert result["provider_calls_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_gate_run"] is False
    assert result["hermes_state_touched"] is False
    assert result["hetzner_state_touched"] is False
    assert result["promotion_performed"] is False
    assert result["production_runtime_supported"] is False


def test_stable_schema_on_pass_unavailable_and_fail():
    passed = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )
    unavailable = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "parameters": {"mode": "availability_check"},
            "provenance": {"source": "unit_test"},
        }
    )
    failed = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": {"version": "wrong"},
            "provenance": {"source": "unit_test"},
        }
    )

    for result in (passed, unavailable, failed):
        assert "rd_agent_contract_version" in result
        assert "rd_agent_available" in result
        assert "proposal_run" in result
        assert "input_hash" in result
        assert "candidate_hypotheses" in result
        assert "factor_proposals" in result
        assert "strategy_candidate_notes" in result
        assert "review_status" in result
        assert "failure_reason" in result


def test_proposals_are_review_only_and_not_executable():
    result = _run(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": _review_artifact(),
            "parameters": {"mode": "deterministic_local"},
            "provenance": {"source": "unit_test"},
        }
    )

    assert all(isinstance(item, str) for item in result["candidate_hypotheses"])
    assert all(isinstance(item, str) for item in result["factor_proposals"])
    assert all(isinstance(item, str) for item in result["strategy_candidate_notes"])


def test_module_does_not_import_forbidden_modules():
    forbidden_roots = (
        "research_lab.provider",
        "research_lab.providers",
        "research_lab.broker",
        "research_lab.hermes",
        "research_lab.registry",
        "research_lab.deployment",
        "research_lab.orchestration.daily",
        "research_lab.backtest",
        "rdagent",
        "socket",
        "subprocess",
        "requests",
        "aiohttp",
        "urllib",
        "http",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for forbidden in forbidden_roots:
        assert forbidden not in imports
