from __future__ import annotations

import ast
import copy
import math
from pathlib import Path

import pytest

from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    build_risk_overlay_execution_spec,
)
from research_lab.orchestration.risk_overlay_hypothesis_queue import (
    build_risk_overlay_hypothesis_queue_entry,
)

from research_lab.execution.risk_overlay_candidate_synthetic_acceptance_v1 import (
    run_candidate_synthetic_acceptance,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "risk_overlay_candidate_synthetic_acceptance_v1.py"


def _draft() -> dict[str, object]:
    return {
        "version": "candidate_experiment_draft_v1",
        "source": {
            "blocker": "drawdown_fail",
            "source_notes": [
                {
                    "note_id": "note-acceptance-0001",
                    "book_id": "book-risk-control-2002",
                    "book_title": "Money Management Risk Control For Traders (2002)",
                    "page_start": 44,
                    "page_end": 46,
                    "confidence": "medium",
                    "promotion_status": "not_promoted",
                    "extracted_claim": "Risk sizing and staged derisking reduce drawdown severity.",
                    "why_relevant_to_blocker": "The source candidate is explicitly about drawdown survival.",
                    "risk_controls": ["fixed fractional sizing", "drawdown circuit breaker"],
                }
            ],
        },
        "hypothesis": "Fixed-fractional sizing plus staged derisking improves drawdown containment.",
        "target_failure_mode": "drawdown_fail",
        "base_strategy_selection": {
            "mode": "explicit_base_strategy",
            "allowed_to_modify_signals": False,
            "allowed_to_modify_entries": False,
            "allowed_to_modify_exits": False,
        },
        "base_strategy": {
            "family": "LONGTERM",
            "asset_class": "ETF",
            "timeframe": "1D",
            "short_name": "TREND_VOL_CAP",
            "builder": "long_term_vol_target_cap",
            "parameters": {
                "symbol": "SPY",
                "sma": 200,
                "vol_window": 63,
                "target_vol": 0.10,
                "max_weight": 0.75,
            },
            "rules": "Hold SPY above SMA200 with realized-volatility targeting capped at 75% exposure; otherwise hold cash.",
        },
        "risk_overlay": {
            "position_sizing": {
                "type": "fixed_fractional",
                "risk_per_trade_pct_candidates": [1.0],
            },
            "portfolio_drawdown_circuit_breaker": {
                "type": "staged_derisking",
                "thresholds": [
                    {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75},
                    {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.5},
                ],
                "reentry_rule": {
                    "type": "equity_recovery",
                    "recovery_from_peak_pct": 2.0,
                    "cooldown_days": 2,
                },
            },
            "loser_addition_rule": {"add_to_losers_allowed": False},
        },
        "validation_plan": {
            "primary_metrics": ["max_drawdown"],
            "secondary_metrics": ["CAGR"],
            "comparison": "same signals with and without risk overlay",
            "required_gates": ["walk_forward", "drawdown"],
        },
        "safety": {
            "promotion_allowed": False,
            "registry_write_allowed": False,
            "backtest_allowed_in_this_step": False,
            "strategy_code_modification_allowed": False,
            "requires_manual_review": True,
        },
    }


def _candidate() -> dict[str, object]:
    entry = build_risk_overlay_hypothesis_queue_entry(
        _draft(),
        source_draft="tmp/risk_overlay_candidate_draft.json",
    )
    return build_risk_overlay_execution_spec(entry, source_artifact_path="tmp/review_candidate.json")


def _request() -> dict[str, object]:
    return {
        "version": "risk_overlay_candidate_synthetic_acceptance_request_v1",
        "candidate": _candidate(),
        "synthetic_scenario": {
            "symbol": "SYNTH",
            "initial_equity": 100_000.0,
            "price_series": [
                {"timestamp": "2026-01-01", "price": 100.0},
                {"timestamp": "2026-01-02", "price": 80.0},
                {"timestamp": "2026-01-03", "price": 100.0},
                {"timestamp": "2026-01-04", "price": 110.0},
            ],
            "events": [
                {
                    "timestamp": "2026-01-01",
                    "event_id": "event-1",
                    "event_type": "entry",
                    "direction": "long",
                    "protective_exit": {
                        "type": "fixed_stop",
                        "stop_price": 95.0,
                    },
                },
                {
                    "timestamp": "2026-01-04",
                    "event_id": "event-2",
                    "event_type": "exit",
                    "direction": "long",
                },
            ],
        },
        "executor_config": {
            "runtime_contract_version": "risk_execution_contract_v1",
            "fractional_units_allowed": False,
            "strategy_position_cap_fraction": 1.0,
            "portfolio_exposure_cap_fraction": 1.0,
            "output_mode": "full_result",
        },
        "provenance": {
            "review_id": "review-001",
        },
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return run_candidate_synthetic_acceptance(copy.deepcopy(request))


def test_valid_reviewed_candidate_builds_executor_request_and_completes():
    result = _run(_request())

    assert result["version"] == "risk_overlay_candidate_synthetic_acceptance_result_v1"
    assert result["acceptance_bridge_version"] == "risk_overlay_candidate_synthetic_acceptance_v1"
    assert result["execution_status"] == "completed"
    assert result["failure_reason"] is None
    assert result["synthetic_data_used"] is True
    assert result["real_data_used"] is False
    assert result["registry_write_performed"] is False
    assert result["deployment_gate_run"] is False
    assert result["promotion_performed"] is False
    assert result["provider_calls_used"] == 0
    assert result["broker_actions_used"] == 0
    assert result["hermes_write_performed"] is False
    assert result["backtest_run_performed"] is False
    assert result["candidate_summary"]["candidate_version"] == "risk_overlay_execution_spec_artifact_v1"
    assert result["candidate_summary"]["source_candidate_id"]
    assert result["candidate_summary"]["selected_risk_per_trade_pct"] == pytest.approx(1.0)
    assert result["executor_request"]["version"] == "risk_overlay_isolated_execution_request_v1"
    assert result["executor_request"]["runtime_contract_version"] == "risk_execution_contract_v1"
    assert result["executor_request"]["symbol"] == "SYNTH"
    assert result["executor_request"]["strategy_events"][0]["target_direction"] == "long"
    assert result["executor_request"]["strategy_events"][1]["target_direction"] == "flat"
    assert result["executor_request"]["protective_exits_by_event_id"]["event-1"]["per_unit_loss_to_protective_exit"] == pytest.approx(5.0)


def test_executor_result_is_produced_by_the_isolated_executor():
    result = _run(_request())

    assert result["executor_result"]["version"] == "risk_overlay_isolated_execution_result_v1"
    assert result["executor_result"]["executor_version"] == "risk_overlay_isolated_executor_v1"
    assert result["executor_result"]["execution_status"] == "completed"


def test_selected_risk_in_executor_request_equals_the_single_reviewed_value():
    low_risk = _request()
    high_risk = _request()
    low_risk["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["risk_per_trade_pct_candidates"] = [1.0]
    high_risk["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["risk_per_trade_pct_candidates"] = [2.0]

    low = _run(low_risk)
    high = _run(high_risk)

    assert low["executor_request"]["fixed_fractional_config"]["selected_risk_per_trade_pct"] == pytest.approx(1.0)
    assert high["executor_request"]["fixed_fractional_config"]["selected_risk_per_trade_pct"] == pytest.approx(2.0)
    assert low["executor_result"]["event_log"][0]["executed_units"] < high["executor_result"]["event_log"][0]["executed_units"]


def test_protective_stop_distance_changes_executed_units():
    narrow = _request()
    wide = _request()
    narrow["synthetic_scenario"]["events"][0]["protective_exit"]["stop_price"] = 99.0
    wide["synthetic_scenario"]["events"][0]["protective_exit"]["stop_price"] = 90.0

    narrow_result = _run(narrow)
    wide_result = _run(wide)

    assert narrow_result["executor_result"]["event_log"][0]["executed_units"] > wide_result["executor_result"]["event_log"][0]["executed_units"]


def test_circuit_breaker_thresholds_are_passed_through_and_can_trigger_derisking():
    request = _request()
    request["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["risk_per_trade_pct_candidates"] = [2.0]

    result = _run(request)

    assert result["executor_request"]["circuit_breaker_thresholds"] == [
        {"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75},
        {"drawdown_pct": 8.0, "gross_exposure_multiplier": 0.5},
    ]
    assert result["acceptance_metrics"]["circuit_breaker_activation_count"] == 1
    assert result["acceptance_metrics"]["derisking_action_count"] == 1


def test_reentry_rule_is_passed_through():
    result = _run(_request())

    assert result["executor_request"]["reentry_rule"] == {
        "type": "equity_recovery",
        "recovery_from_peak_pct": 2.0,
        "cooldown_days": 2,
    }


def test_hash_determinism_repeated_identical_input_gives_identical_result():
    first = _run(_request())
    second = _run(_request())

    assert first["acceptance_input_sha256"] == second["acceptance_input_sha256"]
    assert first["executor_request_sha256"] == second["executor_request_sha256"]
    assert first == second


def test_unknown_top_level_request_field_fails():
    request = _request()
    request["unknown"] = "value"

    with pytest.raises(ValueError, match="request contains unknown field"):
        _run(request)


def test_unknown_candidate_field_fails():
    request = _request()
    request["candidate"]["unknown"] = True

    with pytest.raises(ValueError, match="candidate contains unknown field"):
        _run(request)


def test_non_drawdown_blocker_fails():
    request = _request()
    request["candidate"]["provenance"]["blocker"] = "negative_unseen_result"

    with pytest.raises(ValueError, match="drawdown_fail"):
        _run(request)


def test_target_failure_mode_mismatch_fails_before_executor_execution():
    request = _request()
    request["candidate"]["execution_spec"]["parameters"]["target_failure_mode"] = "negative_unseen_result"

    with pytest.raises(ValueError, match="target_failure_mode"):
        _run(request)


def test_non_fixed_fractional_overlay_fails():
    request = _request()
    request["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["type"] = "kelly"

    with pytest.raises(ValueError, match="fixed_fractional"):
        _run(request)


def test_zero_risk_candidates_fail():
    request = _request()
    request["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["risk_per_trade_pct_candidates"] = []

    with pytest.raises(ValueError, match="exactly one"):
        _run(request)


def test_multiple_risk_candidates_fail():
    request = _request()
    request["candidate"]["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"]["risk_per_trade_pct_candidates"] = [1.0, 2.0]

    with pytest.raises(ValueError, match="exactly one"):
        _run(request)


def test_missing_stop_for_entry_fails():
    request = _request()
    del request["synthetic_scenario"]["events"][0]["protective_exit"]

    with pytest.raises(ValueError, match="protective_exit is required for entry"):
        _run(request)


def test_stop_above_or_equal_entry_price_for_long_fails():
    equal_stop = _request()
    equal_stop["synthetic_scenario"]["events"][0]["protective_exit"]["stop_price"] = 100.0
    with pytest.raises(ValueError, match="below entry price"):
        _run(equal_stop)

    above_stop = _request()
    above_stop["synthetic_scenario"]["events"][0]["protective_exit"]["stop_price"] = 101.0
    with pytest.raises(ValueError, match="below entry price"):
        _run(above_stop)


def test_multiple_events_same_timestamp_fail():
    request = _request()
    request["synthetic_scenario"]["events"].append(
        {
            "timestamp": "2026-01-01",
            "event_id": "event-3",
            "event_type": "rebalance",
            "direction": "long",
            "protective_exit": {
                "type": "fixed_stop",
                "stop_price": 94.0,
            },
        }
    )

    with pytest.raises(ValueError, match="at most one event"):
        _run(request)


def test_event_timestamp_missing_from_price_series_fails():
    request = _request()
    request["synthetic_scenario"]["events"][1]["timestamp"] = "2026-01-05"

    with pytest.raises(ValueError, match="is not present in price_series"):
        _run(request)


def test_duplicate_event_id_fails_at_bridge_validation():
    request = _request()
    request["synthetic_scenario"]["price_series"].append({"timestamp": "2026-01-05", "price": 111.0})
    request["synthetic_scenario"]["events"].append(
        {
            "timestamp": "2026-01-05",
            "event_id": "event-1",
            "event_type": "rebalance",
            "direction": "long",
        }
    )

    with pytest.raises(ValueError, match="duplicate event_id"):
        _run(request)


def test_exit_event_carrying_protective_exit_fails_at_bridge_validation():
    request = _request()
    request["synthetic_scenario"]["events"][1]["protective_exit"] = {
        "type": "fixed_stop",
        "stop_price": 100.0,
    }

    with pytest.raises(ValueError, match="exit events must not provide protective exits"):
        _run(request)


def test_old_absolute_sounding_cap_names_fail_closed():
    request = _request()
    request["executor_config"]["strategy_position_cap"] = 1.0
    request["executor_config"]["portfolio_exposure_cap"] = 1.0

    with pytest.raises(ValueError, match="unknown field"):
        _run(request)


def test_missing_strategy_position_cap_fraction_fails_at_bridge_validation():
    request = _request()
    del request["executor_config"]["strategy_position_cap_fraction"]

    with pytest.raises(ValueError, match="strategy_position_cap_fraction"):
        _run(request)


def test_missing_portfolio_exposure_cap_fraction_fails_at_bridge_validation():
    request = _request()
    del request["executor_config"]["portfolio_exposure_cap_fraction"]

    with pytest.raises(ValueError, match="portfolio_exposure_cap_fraction"):
        _run(request)


def test_cap_fraction_fields_are_mapped_to_notional_caps():
    request = _request()
    request["executor_config"]["strategy_position_cap_fraction"] = 0.5
    request["executor_config"]["portfolio_exposure_cap_fraction"] = 0.25

    result = _run(request)

    assert result["executor_request"]["strategy_position_cap"] == pytest.approx(50_000.0)
    assert result["executor_request"]["portfolio_exposure_cap"] == pytest.approx(25_000.0)


def test_cap_fraction_fields_must_be_within_zero_and_one():
    request = _request()
    request["executor_config"]["strategy_position_cap_fraction"] = 1.5

    with pytest.raises(ValueError, match="within \\(0, 1\\]"):
        _run(request)


def test_adapter_version_is_semantically_validated():
    request = _request()
    request["candidate"]["adapter_version"] = "risk_overlay_execution_adapter_v0"

    with pytest.raises(ValueError, match="adapter_version"):
        _run(request)


@pytest.mark.parametrize("field", ["dataset", "provider", "ticker", "file_path", "data_source"])
def test_real_data_references_fail_if_present(field: str):
    request = _request()
    request["synthetic_scenario"][field] = "REAL_SOURCE"

    with pytest.raises(ValueError, match="synthetic_scenario contains unknown field"):
        _run(request)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("candidate", "execution_spec", "parameters", "risk_overlay", "position_sizing", "risk_per_trade_pct_candidates"), [True], "must not be boolean"),
        (("synthetic_scenario", "initial_equity"), math.nan, "must be finite"),
        (("synthetic_scenario", "price_series"), [{"timestamp": "2026-01-01", "price": math.inf}], "must be finite"),
        (("synthetic_scenario", "events"), [{"timestamp": "2026-01-01", "event_id": "event-1", "event_type": "entry", "direction": True, "protective_exit": {"type": "fixed_stop", "stop_price": 95.0}}], "direction is required"),
    ],
)
def test_nan_infinity_and_boolean_numerics_fail(path, value, match):
    request = _request()
    target = request
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=match):
        _run(request)


def test_result_flags_are_all_safe():
    result = _run(_request())

    assert result["synthetic_data_used"] is True
    assert result["real_data_used"] is False
    assert result["registry_write_performed"] is False
    assert result["deployment_gate_run"] is False
    assert result["promotion_performed"] is False
    assert result["provider_calls_used"] == 0
    assert result["broker_actions_used"] == 0
    assert result["hermes_write_performed"] is False
    assert result["backtest_run_performed"] is False


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
        "qlib",
        "rdagent",
        "ultracode",
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
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"{MODULE_PATH.name} imported forbidden module {import_name}"
