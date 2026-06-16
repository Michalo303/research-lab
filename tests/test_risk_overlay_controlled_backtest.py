from __future__ import annotations

import builtins
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
    fake_pandas.notna = lambda value: value == value and value not in (float("inf"), float("-inf"))
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


def _load_module_with_import_guard(monkeypatch):
    imported_names = []
    original_import = builtins.__import__
    banned_prefixes = (
        "research_lab.runner",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.providers",
        "research_lab.brokers",
        "research_lab.trading",
    )

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        imported_names.append(name)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in banned_prefixes):
            raise AssertionError(f"banned import attempted: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = _load_module(monkeypatch)
    return module, imported_names


def _runtime_module(monkeypatch):
    fake_pandas = types.ModuleType("pandas")
    events = {"build_weights_calls": 0, "weighted_backtest_calls": 0, "cost_stress_calls": 0}
    fake_backtest = types.ModuleType("research_lab.backtest")

    class FakeIndex(list):
        def mean(self):
            return sum(self) / len(self) if self else 0.0

    class FakeRow:
        def __init__(self, data):
            self._data = dict(data)

        def __mul__(self, other):
            if isinstance(other, FakeRow):
                keys = self._data.keys() | other._data.keys()
                return FakeRow({key: self._data.get(key, 0.0) * other._data.get(key, 0.0) for key in keys})
            return FakeRow({key: value * other for key, value in self._data.items()})

        __rmul__ = __mul__

        def sum(self):
            return sum(self._data.values())

        def items(self):
            return self._data.items()

    class _LocAccessor:
        def __init__(self, frame):
            self._frame = frame

        def __getitem__(self, key):
            return self._frame._rows[key]

    class FakeDataFrame:
        def __init__(self, rows=None, index=None, columns=None, data=None):
            if data is not None:
                index = list(index or [])
                columns = list(data.keys())
                rows = []
                for offset, ts in enumerate(index):
                    rows.append({column: values[offset] for column, values in data.items()})
            self.index = list(index or [])
            self.columns = list(columns or [])
            self._rows = {
                ts: FakeRow(
                    rows[offset]._data
                    if isinstance(rows[offset], FakeRow)
                    else rows[offset]
                    if isinstance(rows[offset], dict)
                    else dict(zip(self.columns, rows[offset]))
                )
                for offset, ts in enumerate(self.index)
            }
            self.loc = _LocAccessor(self)

        def __getitem__(self, columns):
            return FakeDataFrame(
                rows=[{column: self._rows[ts]._data[column] for column in columns} for ts in self.index],
                index=self.index,
                columns=columns,
            )

        def __mul__(self, other):
            return FakeDataFrame(
                rows=[{column: value * other for column, value in self._rows[ts]._data.items()} for ts in self.index],
                index=self.index,
                columns=self.columns,
            )

        __rmul__ = __mul__

        def reindex(self, index):
            return FakeDataFrame(
                rows=[self._rows.get(ts, FakeRow({column: 0.0 for column in self.columns}))._data for ts in index],
                index=index,
                columns=self.columns,
            )

        def fillna(self, value):
            return self

        def clip(self, lower=None, upper=None):
            rows = []
            for ts in self.index:
                row = {}
                for column, item in self._rows[ts]._data.items():
                    clipped = item
                    if lower is not None and clipped < lower:
                        clipped = lower
                    if upper is not None and clipped > upper:
                        clipped = upper
                    row[column] = clipped
                rows.append(row)
            return FakeDataFrame(rows=rows, index=self.index, columns=self.columns)

        def pct_change(self):
            rows = []
            previous = None
            for ts in self.index:
                current = self._rows[ts]._data
                if previous is None:
                    rows.append({column: 0.0 for column in self.columns})
                else:
                    rows.append(
                        {
                            column: 0.0 if previous[column] == 0 else (current[column] - previous[column]) / previous[column]
                            for column in self.columns
                        }
                    )
                previous = current
            return FakeDataFrame(rows=rows, index=self.index, columns=self.columns)

        def iterrows(self):
            for ts in self.index:
                yield ts, self._rows[ts]

        def sum(self, axis=0):
            if axis != 1:
                raise AssertionError("test fake only supports axis=1")
            return FakeIndex([sum(self._rows[ts]._data.values()) for ts in self.index])

    def fake_datetime(values):
        return list(values)

    fake_pandas.notna = lambda value: value == value and value not in (float("inf"), float("-inf"))
    fake_pandas.DataFrame = FakeDataFrame
    fake_pandas.to_datetime = fake_datetime

    def close_frame(panel):
        return panel[["SPY"]]

    def weighted_backtest(close, weights, cost_bps, periods_per_year):
        events["weighted_backtest_calls"] += 1
        exposure = float(weights.sum(axis=1).mean())
        return {
            "metrics": {
                "cagr": 0.10 + exposure,
                "max_drawdown": 0.05 + exposure / 10.0,
            },
            "split_metrics": {"train": {"cagr": 0.10 + exposure}},
            "average_turnover": exposure / 2.0,
            "average_exposure": exposure,
        }

    def cost_stress(close, weights, cost_bps, periods_per_year):
        events["cost_stress_calls"] += 1
        return {"0_bps": {"net_cagr": float(weights.sum(axis=1).mean())}}

    fake_backtest.close_frame = close_frame
    fake_backtest.weighted_backtest = weighted_backtest
    fake_backtest.cost_stress = cost_stress

    fake_baselines = types.ModuleType("research_lab.strategies.baselines")

    class StrategySpec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def build_weights(spec, daily_panel, context):
        events["build_weights_calls"] += 1
        return fake_pandas.DataFrame(data={"SPY": [1.0, 1.0, 0.5]}, index=daily_panel.index)

    fake_baselines.StrategySpec = StrategySpec
    fake_baselines.build_weights = build_weights

    monkeypatch.setitem(sys.modules, "pandas", fake_pandas)
    monkeypatch.setitem(sys.modules, "research_lab.backtest", fake_backtest)
    monkeypatch.setitem(sys.modules, "research_lab.strategies.baselines", fake_baselines)

    spec = importlib.util.spec_from_file_location("risk_overlay_controlled_backtest_v1_runtime_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, events


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


def _daily_panel(module):
    return module.pd.DataFrame(
        data={
            "SPY": [100.0, 102.0, 101.0],
            "CLOSE": [100.0, 102.0, 101.0],
        },
        index=module.pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )


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


@pytest.mark.parametrize("value", [True, "1", None, -1, 101])
def test_recovery_from_peak_pct_malformed_values_fail_closed(monkeypatch, value):
    module = _load_module(monkeypatch)
    artifact = copy.deepcopy(_artifact())
    artifact["execution_spec"]["parameters"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["reentry_rule"][
        "recovery_from_peak_pct"
    ] = value

    with pytest.raises(ValueError, match="recovery_from_peak_pct"):
        module._validated_parameters(artifact)


@pytest.mark.parametrize("value", [0, 2.5, 100])
def test_recovery_from_peak_pct_valid_numeric_values_are_accepted(monkeypatch, value):
    module = _load_module(monkeypatch)
    artifact = copy.deepcopy(_artifact())
    artifact["execution_spec"]["parameters"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["reentry_rule"][
        "recovery_from_peak_pct"
    ] = value

    parameters = module._validated_parameters(artifact)

    assert parameters["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["reentry_rule"]["recovery_from_peak_pct"] == value


def test_controlled_backtest_run_returns_research_only_deterministic_result(monkeypatch):
    module, events = _runtime_module(monkeypatch)
    artifact = copy.deepcopy(_artifact())
    daily_panel = _daily_panel(module)

    first = module.run_risk_overlay_controlled_backtest(artifact, daily_panel)
    second = module.run_risk_overlay_controlled_backtest(artifact, daily_panel)

    assert first == second
    assert first["research_only"] is True
    assert first["production_paths"] == []
    assert first["file_outputs"] == []
    assert first["safety"]["registry_write_allowed"] is False
    assert first["safety"]["promotion_allowed"] is False
    assert first["safety"]["deployment_allowed"] is False
    assert first["safety"]["report_write_allowed"] is False
    assert first["safety"]["leaderboard_write_allowed"] is False
    assert first["safety"]["daily_research_run_allowed"] is False
    assert first["safety"]["file_write_allowed"] is False
    assert len(first["overlay_candidates"]) == 3
    assert first["overlay_candidates"][0]["risk_per_trade_pct"] == 0.25
    assert events["build_weights_calls"] == 2
    assert events["weighted_backtest_calls"] == 8
    assert events["cost_stress_calls"] == 8


def test_module_does_not_import_banned_production_modules(monkeypatch):
    _, imported_names = _load_module_with_import_guard(monkeypatch)

    banned_prefixes = (
        "research_lab.runner",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.providers",
        "research_lab.brokers",
        "research_lab.trading",
    )
    assert not [
        name
        for name in imported_names
        if any(name == prefix or name.startswith(prefix + ".") for prefix in banned_prefixes)
    ]
