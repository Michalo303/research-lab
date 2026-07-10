from __future__ import annotations

import ast
import copy
import math
from pathlib import Path

import pytest

from research_lab.execution.risk_overlay_isolated_executor_v1 import (
    run_isolated_risk_overlay_execution,
)
from research_lab.execution.strategy_execution_capability_bridge_v1 import (
    build_strategy_execution_bridge_request,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "strategy_execution_capability_bridge_v1.py"


def _request() -> dict[str, object]:
    return {
        "version": "strategy_execution_capability_bridge_request_v1",
        "strategy_builder": "swing_trend_filtered_pullback",
        "symbol": "SYNTH_STRATEGY",
        "synthetic_bars": [
            {"timestamp": "2026-01-01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"timestamp": "2026-01-02", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
            {"timestamp": "2026-01-03", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0},
        ],
        "strategy_signal_plan": [
            {
                "timestamp": "2026-01-02",
                "signal_id": "signal-1",
                "signal_type": "entry",
                "direction": "long",
                "protective_exit": {
                    "type": "fixed_stop",
                    "stop_price": 97.0,
                },
            }
        ],
        "provenance": {
            "source": "synthetic_contract_test",
        },
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_strategy_execution_bridge_request(copy.deepcopy(request))


def test_valid_fixed_stop_entry_creates_one_strategy_event_and_one_protective_exit():
    result = _run(_request())

    assert result["version"] == "strategy_execution_capability_bridge_result_v1"
    assert result["bridge_version"] == "strategy_execution_capability_bridge_v1"
    assert result["strategy_builder"] == "swing_trend_filtered_pullback"
    assert result["strategy_runtime_supported"] is False
    assert result["synthetic_data_used"] is True
    assert result["real_data_used"] is False
    assert result["strategy_events"] == [
        {
            "timestamp": "2026-01-02",
            "event_type": "entry",
            "symbol": "SYNTH_STRATEGY",
            "target_direction": "long",
            "strategy_identity": "STRATEGY_EXECUTION_CAPABILITY_BRIDGE_V1::swing_trend_filtered_pullback",
            "event_id": "signal-1",
            "reason_code": "synthetic_strategy_bridge_entry",
        }
    ]
    assert result["protective_exits_by_event_id"]["signal-1"]["entry_price"] == pytest.approx(102.0)
    assert result["protective_exits_by_event_id"]["signal-1"]["protective_exit_price"] == pytest.approx(97.0)
    assert result["protective_exits_by_event_id"]["signal-1"]["per_unit_loss_to_protective_exit"] == pytest.approx(5.0)


def test_valid_atr_stop_entry_creates_deterministic_stop_and_per_unit_loss():
    request = _request()
    request["strategy_signal_plan"][0]["protective_exit"] = {
        "type": "atr_stop",
        "atr": 5.0,
        "atr_multiple": 2.0,
    }

    result = _run(request)

    assert result["protective_exits_by_event_id"]["signal-1"]["protective_exit_price"] == pytest.approx(92.0)
    assert result["protective_exits_by_event_id"]["signal-1"]["per_unit_loss_to_protective_exit"] == pytest.approx(10.0)


def test_exit_creates_flat_event_and_no_protective_exit():
    request = _request()
    request["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "exit",
            "direction": "long",
        }
    ]

    result = _run(request)

    assert result["strategy_events"] == [
        {
            "timestamp": "2026-01-03",
            "event_type": "exit",
            "symbol": "SYNTH_STRATEGY",
            "target_direction": "flat",
            "strategy_identity": "STRATEGY_EXECUTION_CAPABILITY_BRIDGE_V1::swing_trend_filtered_pullback",
            "event_id": "signal-2",
            "reason_code": "synthetic_strategy_bridge_exit",
        }
    ]
    assert result["protective_exits_by_event_id"] == {}


def test_rebalance_with_valid_protective_exit_creates_rebalance_event_and_protective_exit():
    request = _request()
    request["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
            "protective_exit": {
                "type": "fixed_stop",
                "stop_price": 98.0,
            },
        }
    ]

    result = _run(request)

    assert result["strategy_events"] == [
        {
            "timestamp": "2026-01-03",
            "event_type": "rebalance",
            "symbol": "SYNTH_STRATEGY",
            "target_direction": "long",
            "strategy_identity": "STRATEGY_EXECUTION_CAPABILITY_BRIDGE_V1::swing_trend_filtered_pullback",
            "event_id": "signal-2",
            "reason_code": "synthetic_strategy_bridge_rebalance",
        }
    ]
    assert result["protective_exits_by_event_id"]["signal-2"]["protective_exit_price"] == pytest.approx(98.0)


def test_entry_without_protective_exit_fails():
    request = _request()
    del request["strategy_signal_plan"][0]["protective_exit"]

    with pytest.raises(ValueError, match="protective_exit is required for entry and rebalance signals"):
        _run(request)


def test_exit_with_protective_exit_fails():
    request = _request()
    request["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "exit",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 98.0},
        }
    ]

    with pytest.raises(ValueError, match="exit signals must not provide protective_exit"):
        _run(request)


def test_rebalance_without_protective_exit_fails_at_bridge_validation():
    request = _request()
    request["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
        }
    ]

    with pytest.raises(ValueError, match="protective_exit is required for entry and rebalance signals"):
        _run(request)


def test_stop_above_or_equal_event_price_fails():
    equal_stop = _request()
    equal_stop["strategy_signal_plan"][0]["protective_exit"]["stop_price"] = 102.0
    with pytest.raises(ValueError, match="below event price"):
        _run(equal_stop)

    above_stop = _request()
    above_stop["strategy_signal_plan"][0]["protective_exit"]["stop_price"] = 103.0
    with pytest.raises(ValueError, match="below event price"):
        _run(above_stop)


@pytest.mark.parametrize(
    ("field", "value"),
    [("atr", 0.0), ("atr", -1.0), ("atr_multiple", 0.0), ("atr_multiple", -1.0)],
)
def test_atr_and_multiple_must_be_positive(field: str, value: float):
    request = _request()
    request["strategy_signal_plan"][0]["protective_exit"] = {
        "type": "atr_stop",
        "atr": 5.0,
        "atr_multiple": 2.0,
    }
    request["strategy_signal_plan"][0]["protective_exit"][field] = value

    with pytest.raises(ValueError, match=field):
        _run(request)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("synthetic_bars", 0, "open"), True, "open must not be boolean"),
        (("synthetic_bars", 0, "close"), math.inf, "close must be finite"),
        (("strategy_signal_plan", 0, "protective_exit", "atr"), math.nan, "atr must be finite"),
    ],
)
def test_boolean_nan_and_infinity_numerics_fail(path, value, match):
    request = _request()
    if path[:4] == ("strategy_signal_plan", 0, "protective_exit", "atr"):
        request["strategy_signal_plan"][0]["protective_exit"] = {
            "type": "atr_stop",
            "atr": 5.0,
            "atr_multiple": 2.0,
        }
    target = request
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=match):
        _run(request)


def test_unknown_top_level_request_field_fails():
    request = _request()
    request["unexpected"] = True

    with pytest.raises(ValueError, match="request contains unknown field"):
        _run(request)


def test_unknown_synthetic_bar_field_fails():
    request = _request()
    request["synthetic_bars"][0]["volume"] = 1000.0

    with pytest.raises(ValueError, match="synthetic bar contains unknown field"):
        _run(request)


def test_unknown_signal_field_fails():
    request = _request()
    request["strategy_signal_plan"][0]["unexpected"] = "value"

    with pytest.raises(ValueError, match="signal contains unknown field"):
        _run(request)


def test_unknown_protective_exit_field_fails():
    request = _request()
    request["strategy_signal_plan"][0]["protective_exit"]["unexpected"] = "value"

    with pytest.raises(ValueError, match="protective_exit contains unknown field"):
        _run(request)


def test_non_synth_symbol_fails():
    request = _request()
    request["symbol"] = "SPY"

    with pytest.raises(ValueError, match="symbol must start with SYNTH"):
        _run(request)


def test_duplicate_signal_id_fails():
    request = _request()
    request["strategy_signal_plan"].append(
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-1",
            "signal_type": "rebalance",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 98.0},
        }
    )

    with pytest.raises(ValueError, match="duplicate signal_id"):
        _run(request)


def test_multiple_signals_at_same_timestamp_fail():
    request = _request()
    request["strategy_signal_plan"].append(
        {
            "timestamp": "2026-01-02",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 98.0},
        }
    )

    with pytest.raises(ValueError, match="at most one signal"):
        _run(request)


def test_signal_timestamps_not_ordered_fail():
    request = _request()
    request["strategy_signal_plan"] = [
        {
            "timestamp": "2026-01-03",
            "signal_id": "signal-1",
            "signal_type": "entry",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 98.0},
        },
        {
            "timestamp": "2026-01-02",
            "signal_id": "signal-2",
            "signal_type": "rebalance",
            "direction": "long",
            "protective_exit": {"type": "fixed_stop", "stop_price": 97.0},
        },
    ]

    with pytest.raises(ValueError, match="strategy_signal_plan timestamps must be ordered"):
        _run(request)


def test_signal_timestamp_missing_from_bars_fails():
    request = _request()
    request["strategy_signal_plan"][0]["timestamp"] = "2026-01-05"

    with pytest.raises(ValueError, match="is not present in synthetic_bars"):
        _run(request)


def test_bar_timestamps_not_strictly_increasing_fail():
    request = _request()
    request["synthetic_bars"][1]["timestamp"] = "2026-01-01"

    with pytest.raises(ValueError, match="timestamps must be strictly increasing"):
        _run(request)


def test_invalid_ohlc_consistency_fails():
    request = _request()
    request["synthetic_bars"][0]["high"] = 98.0

    with pytest.raises(ValueError, match="high must be greater than or equal"):
        _run(request)

    request = _request()
    request["synthetic_bars"][0]["high"] = 110.0
    request["synthetic_bars"][0]["low"] = 105.0

    with pytest.raises(ValueError, match="low must be less than or equal"):
        _run(request)


def test_unsupported_strategy_builder_fails_closed():
    request = _request()
    request["strategy_builder"] = "unknown_builder"

    with pytest.raises(ValueError, match="unsupported strategy_builder"):
        _run(request)


def test_existing_production_support_flags_remain_false_and_unsupported():
    result = _run(_request())

    summary = result["capability_summary"]
    assert summary["builder"] == "swing_trend_filtered_pullback"
    assert summary["production_runtime_supported"] is False
    assert summary["synthetic_signal_plan_supported"] is True
    assert summary["protective_exit_required"] is True
    assert summary["supported_protective_exit_types"] == ["fixed_stop", "atr_stop"]
    assert result["strategy_runtime_supported"] is False


def test_repeated_identical_input_is_deterministic():
    first = _run(_request())
    second = _run(_request())

    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    assert first == second


def test_bridge_output_can_feed_synthetic_isolated_executor_request():
    bridge = _run(_request())

    executor_result = run_isolated_risk_overlay_execution(
        {
            "version": "risk_overlay_isolated_execution_request_v1",
            "runtime_contract_version": "risk_execution_contract_v1",
            "symbol": "SYNTH_STRATEGY",
            "initial_equity": 100_000.0,
            "synthetic_price_series": [
                {"timestamp": "2026-01-01", "symbol": "SYNTH_STRATEGY", "price": 101.0},
                {"timestamp": "2026-01-02", "symbol": "SYNTH_STRATEGY", "price": 102.0},
                {"timestamp": "2026-01-03", "symbol": "SYNTH_STRATEGY", "price": 103.0},
            ],
            "strategy_events": bridge["strategy_events"]
            + [
                {
                    "timestamp": "2026-01-03",
                    "event_type": "exit",
                    "symbol": "SYNTH_STRATEGY",
                    "target_direction": "flat",
                    "strategy_identity": "STRATEGY_EXECUTION_CAPABILITY_BRIDGE_V1::swing_trend_filtered_pullback",
                    "event_id": "signal-2",
                    "reason_code": "synthetic_strategy_bridge_exit",
                }
            ],
            "protective_exits_by_event_id": bridge["protective_exits_by_event_id"],
            "fixed_fractional_config": {"selected_risk_per_trade_pct": 1.0},
            "strategy_position_cap": 100_000.0,
            "portfolio_exposure_cap": 100_000.0,
            "circuit_breaker_thresholds": [{"drawdown_pct": 5.0, "gross_exposure_multiplier": 0.75}],
            "reentry_rule": {"type": "equity_recovery", "recovery_from_peak_pct": 1.0, "cooldown_days": 1},
            "fractional_units_allowed": False,
            "output_mode": "full_result",
            "provenance": {"source": "bridge_integration_test"},
        }
    )

    assert executor_result["execution_status"] == "completed"
    assert executor_result["synthetic_data_used"] is True


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
