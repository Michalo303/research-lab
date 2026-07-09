from __future__ import annotations

import ast
import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.execution.risk_overlay_isolated_executor_v1 import (
    run_isolated_risk_overlay_execution,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "risk_overlay_isolated_executor_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "run_risk_overlay_isolated_executor.py"


def _request() -> dict[str, object]:
    return {
        "version": "risk_overlay_isolated_execution_request_v1",
        "runtime_contract_version": "risk_execution_contract_v1",
        "symbol": "SPY",
        "initial_equity": 100_000.0,
        "synthetic_price_series": [
            {"timestamp": "2026-01-05", "symbol": "SPY", "price": 100.0},
            {"timestamp": "2026-01-06", "symbol": "SPY", "price": 110.0},
        ],
        "strategy_events": [
            {
                "timestamp": "2026-01-05",
                "event_type": "entry",
                "symbol": "SPY",
                "target_direction": "long",
                "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
                "event_id": "evt-entry-001",
                "reason_code": "initial_entry",
            },
            {
                "timestamp": "2026-01-06",
                "event_type": "exit",
                "symbol": "SPY",
                "target_direction": "flat",
                "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
                "event_id": "evt-exit-001",
                "reason_code": "scheduled_exit",
            },
        ],
        "protective_exits_by_event_id": {
            "evt-entry-001": {
                "entry_price": 100.0,
                "protective_exit_price": 97.5,
                "per_unit_loss_to_protective_exit": 2.5,
                "protective_exit_type": "atr_stop",
                "strategy_provenance": "synthetic.stop.atr",
            }
        },
        "fixed_fractional_config": {
            "selected_risk_per_trade_pct": 1.0,
        },
        "strategy_position_cap": 100_000.0,
        "portfolio_exposure_cap": 100_000.0,
        "circuit_breaker_thresholds": [
            {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75},
            {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.50},
            {"drawdown_pct": 10.0, "gross_exposure_multiplier": 0.0},
        ],
        "reentry_rule": {
            "type": "equity_recovery",
            "recovery_from_peak_pct": 2.0,
            "cooldown_days": 2,
        },
        "fractional_units_allowed": False,
        "output_mode": "full_result",
        "provenance": {
            "request_origin": "unit_test",
            "manual_output_path": "excluded/from/run_configuration/hash.json",
        },
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_isolated_risk_overlay_execution(copy.deepcopy(request))


def test_valid_synthetic_entry_exit_path_completes_with_deterministic_accounting():
    result = _run(_request())

    assert result["version"] == "risk_overlay_isolated_execution_result_v1"
    assert result["executor_version"] == "risk_overlay_isolated_executor_v1"
    assert result["runtime_contract_version"] == "risk_execution_contract_v1"
    assert result["execution_status"] == "completed"
    assert result["failure_reason"] is None
    assert result["synthetic_data_used"] is True
    assert result["execution_performed"] is True
    assert result["registry_write_performed"] is False
    assert result["deployment_gate_run"] is False
    assert result["promotion_performed"] is False
    assert result["provider_calls_used"] == 0
    assert result["broker_actions_used"] == 0
    assert result["protective_exit_execution_supported"] is False
    assert result["final_state"]["cash"] == pytest.approx(104_000.0)
    assert result["final_state"]["position_units"] == 0
    assert result["final_state"]["market_value"] == pytest.approx(0.0)
    assert result["final_state"]["current_equity"] == pytest.approx(104_000.0)
    assert result["final_state"]["realized_pnl"] == pytest.approx(4_000.0)
    assert result["final_state"]["unrealized_pnl"] == pytest.approx(0.0)
    assert result["final_state"]["position_average_price"] is None
    assert result["final_state"]["current_protective_exit"] is None
    assert result["metrics"]["initial_equity"] == pytest.approx(100_000.0)
    assert result["metrics"]["final_equity"] == pytest.approx(104_000.0)
    assert result["metrics"]["total_return"] == pytest.approx(0.04)
    assert result["metrics"]["trade_count"] == 2
    assert result["metrics"]["entry_count"] == 1
    assert result["metrics"]["exit_count"] == 1
    assert result["metrics"]["rebalance_count"] == 0
    assert result["metrics"]["circuit_breaker_activation_count"] == 0
    assert result["metrics"]["escalation_count"] == 0
    assert result["metrics"]["derisking_action_count"] == 0
    assert result["metrics"]["reentry_permitted_count"] == 2
    assert result["event_log"][0]["action"] == "entry"
    assert result["event_log"][0]["executed_units"] == 400
    assert result["event_log"][0]["execution_price"] == pytest.approx(100.0)
    assert result["event_log"][1]["action"] == "exit"
    assert result["event_log"][1]["executed_units"] == 400
    assert result["event_log"][1]["execution_price"] == pytest.approx(110.0)


def test_protective_exit_distance_affects_executed_units():
    narrow = _request()
    wide = _request()
    narrow["protective_exits_by_event_id"]["evt-entry-001"]["protective_exit_price"] = 99.0
    narrow["protective_exits_by_event_id"]["evt-entry-001"]["per_unit_loss_to_protective_exit"] = 1.0
    wide["protective_exits_by_event_id"]["evt-entry-001"]["protective_exit_price"] = 95.0
    wide["protective_exits_by_event_id"]["evt-entry-001"]["per_unit_loss_to_protective_exit"] = 5.0

    narrow_result = _run(narrow)
    wide_result = _run(wide)

    assert narrow_result["event_log"][0]["executed_units"] > wide_result["event_log"][0]["executed_units"]


def test_fractional_and_integer_unit_modes_are_both_supported():
    integer_result = _run(_request())
    fractional_request = _request()
    fractional_request["fractional_units_allowed"] = True
    fractional_request["protective_exits_by_event_id"]["evt-entry-001"]["protective_exit_price"] = 97.0
    fractional_request["protective_exits_by_event_id"]["evt-entry-001"]["per_unit_loss_to_protective_exit"] = 3.0

    fractional_result = _run(fractional_request)

    assert integer_result["event_log"][0]["executed_units"] == 400
    assert fractional_result["event_log"][0]["executed_units"] == pytest.approx(333.3333333333333)


def test_no_leverage_and_caps_bind_sizing():
    request = _request()
    request["fixed_fractional_config"]["selected_risk_per_trade_pct"] = 10.0
    request["strategy_position_cap"] = 8_000.0
    request["portfolio_exposure_cap"] = 7_000.0

    result = _run(request)

    assert result["event_log"][0]["executed_notional"] == pytest.approx(7_000.0)
    assert result["sizing_diagnostics"][0]["binding_cap"] == "portfolio_exposure_cap"


def test_entry_while_already_long_exit_while_flat_and_multiple_same_timestamp_events_fail_closed():
    entry_while_long = _request()
    entry_while_long["strategy_events"] = [
        entry_while_long["strategy_events"][0],
        {
            "timestamp": "2026-01-06",
            "event_type": "entry",
            "symbol": "SPY",
            "target_direction": "long",
            "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
            "event_id": "evt-entry-002",
            "reason_code": "invalid_duplicate_entry",
        },
    ]
    entry_while_long["protective_exits_by_event_id"]["evt-entry-002"] = {
        "entry_price": 110.0,
        "protective_exit_price": 107.5,
        "per_unit_loss_to_protective_exit": 2.5,
        "protective_exit_type": "atr_stop",
        "strategy_provenance": "synthetic.stop.atr",
    }
    with pytest.raises(ValueError, match="entry event requires a flat position"):
        _run(entry_while_long)

    exit_while_flat = _request()
    exit_while_flat["strategy_events"] = [exit_while_flat["strategy_events"][1]]
    exit_while_flat["protective_exits_by_event_id"] = {}
    with pytest.raises(ValueError, match="exit event requires an open position"):
        _run(exit_while_flat)

    same_timestamp = _request()
    same_timestamp["strategy_events"] = [
        same_timestamp["strategy_events"][0],
        {
            "timestamp": "2026-01-05",
            "event_type": "exit",
            "symbol": "SPY",
            "target_direction": "flat",
            "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
            "event_id": "evt-exit-002",
            "reason_code": "ambiguous_same_day",
        },
    ]
    with pytest.raises(ValueError, match="at most one event per symbol and timestamp"):
        _run(same_timestamp)


def test_rebalance_uses_target_total_position_and_reductions_do_not_require_new_protective_exit():
    request = _request()
    request["fixed_fractional_config"]["selected_risk_per_trade_pct"] = 2.0
    request["synthetic_price_series"] = [
        {"timestamp": "2026-01-05", "symbol": "SPY", "price": 100.0},
        {"timestamp": "2026-01-06", "symbol": "SPY", "price": 110.0},
    ]
    request["strategy_events"] = [
        request["strategy_events"][0],
        {
            "timestamp": "2026-01-06",
            "event_type": "rebalance",
            "symbol": "SPY",
            "target_direction": "long",
            "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
            "event_id": "evt-rebalance-001",
            "reason_code": "risk_reduction_only",
        },
    ]
    request["strategy_position_cap"] = 85_000.0
    request["portfolio_exposure_cap"] = 85_000.0

    result = _run(request)

    assert result["event_log"][0]["post_trade_units"] == 800
    assert result["event_log"][1]["action"] == "rebalance"
    assert result["event_log"][1]["side"] == "sell"
    assert result["event_log"][1]["executed_units"] == 28
    assert result["event_log"][1]["post_trade_units"] == 772
    assert result["event_log"][1]["executed_notional"] == pytest.approx(3_080.0)
    assert result["final_state"]["realized_pnl"] == pytest.approx(280.0)
    assert result["final_state"]["current_protective_exit"] == request["protective_exits_by_event_id"]["evt-entry-001"]
    assert result["sizing_diagnostics"][1]["event_type"] == "rebalance"
    assert result["sizing_diagnostics"][1]["post_trade_units"] == 772
    assert result["sizing_diagnostics"][1]["binding_cap"] == "strategy_position_cap"


def test_risk_increasing_rebalance_requires_protective_exit():
    request = _request()
    request["synthetic_price_series"] = [
        {"timestamp": "2026-01-05", "symbol": "SPY", "price": 100.0},
        {"timestamp": "2026-01-06", "symbol": "SPY", "price": 100.0},
    ]
    request["strategy_events"] = [
        {
            "timestamp": "2026-01-06",
            "event_type": "rebalance",
            "symbol": "SPY",
            "target_direction": "long",
            "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
            "event_id": "evt-rebalance-001",
            "reason_code": "increase_risk",
        },
    ]
    request["protective_exits_by_event_id"] = {}

    with pytest.raises(ValueError, match="protective exit is required for risk-increasing rebalance"):
        _run(request)


def test_staged_threshold_activation_escalation_and_held_shallower_recovery_reduce_exposure_without_auto_reentry():
    request = _request()
    request["fixed_fractional_config"]["selected_risk_per_trade_pct"] = 10.0
    request["reentry_rule"]["cooldown_days"] = 0
    request["circuit_breaker_thresholds"] = [
        {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.5},
        {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.25},
        {"drawdown_pct": 10.0, "gross_exposure_multiplier": 0.125},
    ]
    request["synthetic_price_series"] = [
        {"timestamp": "2026-01-05", "symbol": "SPY", "price": 100.0},
        {"timestamp": "2026-01-06", "symbol": "SPY", "price": 92.0},
        {"timestamp": "2026-01-07", "symbol": "SPY", "price": 76.0},
        {"timestamp": "2026-01-08", "symbol": "SPY", "price": 84.0},
        {"timestamp": "2026-01-09", "symbol": "SPY", "price": 160.0},
    ]
    request["strategy_events"] = [request["strategy_events"][0]]

    result = _run(request)

    assert [item["transition_reason"] for item in result["overlay_transition_log"]] == [
        "no_transition",
        "threshold_activation",
        "threshold_escalation",
        "threshold_held",
        "reentry",
    ]
    assert [item["post_derisk_units"] for item in result["event_log"] if item["action"] == "overlay_derisk"] == [250, 125]
    assert result["metrics"]["circuit_breaker_activation_count"] == 1
    assert result["metrics"]["escalation_count"] == 1
    assert result["metrics"]["derisking_action_count"] == 2
    assert result["final_state"]["position_units"] == 125
    assert result["final_state"]["overlay_state"]["reentry_eligible"] is True


def test_cooldown_and_recovery_alone_do_not_recreate_closed_or_reduced_positions_but_new_event_can_reenter():
    request = _request()
    request["fixed_fractional_config"]["selected_risk_per_trade_pct"] = 10.0
    request["synthetic_price_series"] = [
        {"timestamp": "2026-01-05", "symbol": "SPY", "price": 100.0},
        {"timestamp": "2026-01-06", "symbol": "SPY", "price": 92.0},
        {"timestamp": "2026-01-07", "symbol": "SPY", "price": 104.0},
        {"timestamp": "2026-01-08", "symbol": "SPY", "price": 104.0},
        {"timestamp": "2026-01-09", "symbol": "SPY", "price": 104.0},
    ]
    request["strategy_events"] = [
        request["strategy_events"][0],
        {
            "timestamp": "2026-01-09",
            "event_type": "rebalance",
            "symbol": "SPY",
            "target_direction": "long",
            "strategy_identity": "SYNTHETIC_RISK_OVERLAY_V1",
            "event_id": "evt-rebalance-001",
            "reason_code": "reenter_after_recovery",
        },
    ]
    request["protective_exits_by_event_id"]["evt-rebalance-001"] = {
        "entry_price": 99.5,
        "protective_exit_price": 97.0,
        "per_unit_loss_to_protective_exit": 2.5,
        "protective_exit_type": "atr_stop",
        "strategy_provenance": "synthetic.stop.atr",
    }

    result = _run(request)

    derisk_events = [item for item in result["event_log"] if item["action"] == "overlay_derisk"]
    assert derisk_events
    assert derisk_events[-1]["post_derisk_units"] == 500
    assert result["overlay_transition_log"][-2]["transition_reason"] == "reentry"
    assert result["event_log"][-1]["action"] == "rebalance"
    assert result["event_log"][-1]["executed_units"] > 0


def test_duplicate_ids_unordered_timestamps_missing_protective_exit_exit_with_protective_exit_unknown_fields_and_bad_numerics_fail():
    duplicate_ids = _request()
    duplicate_ids["strategy_events"][1]["event_id"] = "evt-entry-001"
    with pytest.raises(ValueError, match="duplicate event_id"):
        _run(duplicate_ids)

    unordered = _request()
    unordered["synthetic_price_series"] = list(reversed(unordered["synthetic_price_series"]))
    with pytest.raises(ValueError, match="timestamps must be strictly ordered"):
        _run(unordered)

    missing_protective_exit = _request()
    missing_protective_exit["protective_exits_by_event_id"] = {}
    with pytest.raises(ValueError, match="protective exit is required for entry"):
        _run(missing_protective_exit)

    exit_with_protective_exit = _request()
    exit_with_protective_exit["protective_exits_by_event_id"]["evt-exit-001"] = {
        "entry_price": 110.0,
        "protective_exit_price": 107.5,
        "per_unit_loss_to_protective_exit": 2.5,
        "protective_exit_type": "atr_stop",
        "strategy_provenance": "synthetic.stop.atr",
    }
    with pytest.raises(ValueError, match="exit events must not provide protective exits"):
        _run(exit_with_protective_exit)

    unknown_field = _request()
    unknown_field["look_ahead_price"] = 120.0
    with pytest.raises(ValueError, match="unknown field"):
        _run(unknown_field)

    bad_numeric = _request()
    bad_numeric["initial_equity"] = math.nan
    with pytest.raises(ValueError, match="initial_equity"):
        _run(bad_numeric)


def test_wrong_runtime_contract_version_fails_closed():
    request = _request()
    request["runtime_contract_version"] = "risk_execution_contract_v0"

    with pytest.raises(ValueError, match="runtime_contract_version"):
        _run(request)


def test_result_is_deterministic_and_hashes_follow_the_contract():
    first_request = _request()
    second_request = _request()
    second_request["provenance"]["manual_output_path"] = "different/output/location.json"

    first = _run(first_request)
    second = _run(second_request)

    assert first["run_configuration_sha256"] == second["run_configuration_sha256"]
    assert first["input_sha256"] != second["input_sha256"]
    assert first == _run(first_request)


def test_semantically_equivalent_threshold_and_reentry_payloads_hash_identically():
    first_request = _request()
    second_request = _request()
    second_request["circuit_breaker_thresholds"] = [
        {"drawdown_pct": 5, "gross_exposure_multiplier": 0.75},
        {"drawdown_pct": 8, "gross_exposure_multiplier": 0.5},
        {"drawdown_pct": 10, "gross_exposure_multiplier": 0},
    ]
    second_request["reentry_rule"] = {
        "type": "equity_recovery",
        "recovery_from_peak_pct": 2,
        "cooldown_days": 2,
    }

    first = _run(first_request)
    second = _run(second_request)

    assert first["input_sha256"] == second["input_sha256"]
    assert first["run_configuration_sha256"] == second["run_configuration_sha256"]
    assert first == second


def test_cli_refuses_overwrite_and_paths_inside_repository(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(_request(), indent=2) + "\n", encoding="utf-8")

    outside_output = tmp_path / "outside-output.json"
    outside_output.write_text("already here\n", encoding="utf-8")
    overwrite = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(outside_output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    overwrite_stdout = json.loads(overwrite.stdout.strip())
    assert overwrite.returncode != 0
    assert overwrite_stdout["failure_reason"] == "overwrite_forbidden"

    inside_repo_output = ROOT / "tests" / "fixtures" / "unsafe-risk-overlay-output.json"
    unsafe = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(inside_repo_output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    unsafe_stdout = json.loads(unsafe.stdout.strip())
    assert unsafe.returncode != 0
    assert unsafe_stdout["failure_reason"] == "unsafe_output_path"


def test_module_and_cli_do_not_import_provider_registry_backtest_or_deployment_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "research_lab.llm",
        "requests",
        "aiohttp",
        "urllib",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
            ), f"{path.name} imported forbidden module {import_name}"
