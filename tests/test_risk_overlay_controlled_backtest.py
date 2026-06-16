from __future__ import annotations

import copy
import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_controlled_backtest_v1.py"


def _load_module(monkeypatch):
    fake_pandas = types.ModuleType("pandas")
    fake_pandas.notna = lambda value: value == value

    fake_backtest = types.ModuleType("research_lab.backtest")

    def _unexpected_runtime_call(*args, **kwargs):
        raise AssertionError("test must not call runtime backtest helpers")

    fake_backtest.close_frame = _unexpected_runtime_call
    fake_backtest.cost_stress = _unexpected_runtime_call
    fake_backtest.weighted_backtest = _unexpected_runtime_call

    fake_baselines = types.ModuleType("research_lab.strategies.baselines")

    class StrategySpec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_baselines.StrategySpec = StrategySpec
    fake_baselines.build_weights = _unexpected_runtime_call

    monkeypatch.setitem(sys.modules, "pandas", fake_pandas)
    monkeypatch.setitem(sys.modules, "research_lab.backtest", fake_backtest)
    monkeypatch.setitem(sys.modules, "research_lab.strategies.baselines", fake_baselines)

    spec = importlib.util.spec_from_file_location("risk_overlay_controlled_backtest_v1_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _artifact() -> dict:
    return {
        "version": "risk_overlay_execution_spec_artifact_v1",
        "adapter_version": "risk_overlay_execution_adapter_v1",
        "execution_spec_supported": True,
        "appendable_to_registry": False,
        "requires_human_review": True,
        "source_runtime_supported": False,
        "execution_spec": {
            "builder": "risk_overlay_execution_adapter_v1",
            "parameters": {
                "appendable_to_registry": False,
                "requires_human_review": True,
                "source_runtime_supported": False,
                "source_hypothesis_id": "RISK_OVERLAY_TEST",
                "source_note_ids": ["note-1"],
                "base_strategy": {
                    "family": "LONGTERM",
                    "asset_class": "ETF",
                    "timeframe": "1D",
                    "short_name": "TREND_VOL_CAP",
                    "builder": "long_term_vol_target_cap",
                    "parameters": {"symbol": "SPY", "sma": 200},
                    "rules": "Hold SPY above SMA200; otherwise hold cash.",
                },
                "base_strategy_selection": {
                    "allowed_to_modify_signals": False,
                    "allowed_to_modify_entries": False,
                    "allowed_to_modify_exits": False,
                },
                "risk_overlay": {
                    "position_sizing": {
                        "type": "fixed_fractional",
                        "risk_per_trade_pct_candidates": [0.25, 0.5, 0.75],
                    },
                    "portfolio_drawdown_circuit_breaker": {
                        "type": "staged_derisking",
                        "thresholds": [
                            {"drawdown_pct": 5, "gross_exposure_multiplier": 0.75},
                            {"drawdown_pct": 8, "gross_exposure_multiplier": 0.50},
                            {"drawdown_pct": 10, "gross_exposure_multiplier": 0.0},
                        ],
                        "reentry_rule": {
                            "type": "equity_recovery",
                            "recovery_from_peak_pct": 2,
                            "cooldown_days": 10,
                        },
                    },
                    "loser_addition_rule": {"add_to_losers_allowed": False},
                },
            },
        },
    }


def test_valid_review_artifact_parameters_validate_without_runtime_calls(monkeypatch):
    module = _load_module(monkeypatch)

    parameters = module._validated_parameters(_artifact())

    assert parameters["appendable_to_registry"] is False
    assert parameters["requires_human_review"] is True
    assert parameters["source_runtime_supported"] is False
    assert parameters["source_hypothesis_id"] == "RISK_OVERLAY_TEST"


def test_appendable_to_registry_true_fails_closed(monkeypatch):
    module = _load_module(monkeypatch)
    artifact = _artifact()
    artifact["appendable_to_registry"] = True

    with pytest.raises(ValueError, match="appendable_to_registry=false"):
        module._validated_parameters(artifact)


@pytest.mark.parametrize(
    ("field_name", "mutate"),
    [
        (
            "risk_per_trade_pct_candidates",
            lambda artifact: artifact["execution_spec"]["parameters"]["risk_overlay"]["position_sizing"].__setitem__(
                "risk_per_trade_pct_candidates", [True, 0.5]
            ),
        ),
        (
            "drawdown_pct",
            lambda artifact: artifact["execution_spec"]["parameters"]["risk_overlay"][
                "portfolio_drawdown_circuit_breaker"
            ]["thresholds"][0].__setitem__("drawdown_pct", True),
        ),
        (
            "gross_exposure_multiplier",
            lambda artifact: artifact["execution_spec"]["parameters"]["risk_overlay"][
                "portfolio_drawdown_circuit_breaker"
            ]["thresholds"][0].__setitem__("gross_exposure_multiplier", False),
        ),
        (
            "cooldown_days",
            lambda artifact: artifact["execution_spec"]["parameters"]["risk_overlay"][
                "portfolio_drawdown_circuit_breaker"
            ]["reentry_rule"].__setitem__("cooldown_days", True),
        ),
    ],
)
def test_boolean_numeric_overlay_fields_fail_closed(monkeypatch, field_name, mutate):
    module = _load_module(monkeypatch)
    artifact = copy.deepcopy(_artifact())
    mutate(artifact)

    with pytest.raises(ValueError, match=field_name):
        module._validated_parameters(artifact)
