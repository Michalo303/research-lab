import pandas as pd
import pytest

from research_lab.data import DataBundle
from research_lab.runner import run_daily_research
from research_lab.strategies.baselines import StrategySpec

pytestmark = pytest.mark.usefixtures("hermetic_provider_guard")


def test_daily_results_persist_true_walk_forward(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("EODHD_API_KEY", "fake-must-not-be-used")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {
            "specs": [
                StrategySpec(
                    family="LONGTERM",
                    asset_class="ETF",
                    timeframe="1D",
                    short_name="TEST_FAST",
                    hypothesis="test",
                    parameters={"symbol": "SPY", "sma": 1},
                    rules="test",
                    builder="long_term_trend_filter",
                )
            ],
            "diagnostics": {"proposed": 1, "budget_selected": 1},
        },
    )

    results = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert results
    assert all(result["walk_forward"]["method"] == "true_rolling_oos" for result in results)


def test_daily_runner_emits_progress_timing_logs(tmp_path, monkeypatch, capsys):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    close = panel.xs("close", level=1, axis=1)
    spec = StrategySpec(
        family="LONGTERM",
        asset_class="ETF",
        timeframe="1D",
        short_name="TEST_FAST",
        hypothesis="test",
        parameters={"symbol": "SPY"},
        rules="test",
        builder="long_term_trend_filter",
    )

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {"specs": [spec], "diagnostics": {"proposed": 1, "budget_selected": 1}},
    )
    monkeypatch.setattr(runner, "build_weights", lambda spec, daily, intraday: pd.DataFrame({"SPY": 1.0}, index=close.index))
    monkeypatch.setattr(
        runner,
        "weighted_backtest",
        lambda *args: {
            "metrics": {"cagr": 0.1},
            "split_metrics": _split_metrics(),
            "equity": pd.Series([1.0, 1.1], index=close.index),
            "returns": pd.Series([0.0, 0.1], index=close.index),
            "average_turnover": 0.0,
            "average_exposure": 1.0,
        },
    )
    monkeypatch.setattr(runner, "cost_stress", lambda *args: _cost_stress())
    monkeypatch.setattr(runner, "compute_drawdown_diagnostics", lambda *args, **kwargs: {"max_drawdown": 0.0})
    monkeypatch.setattr(runner, "classify_strategy", lambda *args: ("C", "test"))
    monkeypatch.setattr(runner, "_persist_result", lambda *args: None)
    monkeypatch.setattr(runner, "_persist_hypothesis_result", lambda *args: None)
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args: None)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"})

    def fake_walk_forward(*args, **kwargs):
        progress_log = kwargs.get("progress_log")
        if progress_log:
            progress_log("true walk-forward start strategy=TEST_FAST windows=2")
            progress_log("true walk-forward done strategy=TEST_FAST windows=2 elapsed=0.25s")
        return {"method": "true_rolling_oos", "status": "ok", "window_count": 2}

    monkeypatch.setattr(runner, "run_true_walk_forward", fake_walk_forward)

    results = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    out = capsys.readouterr().out
    assert results
    for signal in (
        "[daily] start",
        "[daily] running experiment",
        "[daily] true walk-forward start",
        "[daily] true walk-forward done",
        "[daily] writing daily report",
        "[daily] completed",
    ):
        assert signal in out


def _panel():
    index = pd.bdate_range("2026-01-01", periods=2)
    return pd.concat(
        {
            "SPY": pd.DataFrame(
                {
                    "open": [100.0, 101.0],
                    "high": [100.0, 101.0],
                    "low": [100.0, 101.0],
                    "close": [100.0, 101.0],
                    "volume": [1_000_000, 1_000_000],
                },
                index=index,
            )
        },
        axis=1,
    )


def _bundle(panel):
    return DataBundle(
        "daily_universe",
        "1D",
        panel,
        {
            "name": "daily_universe",
            "source": "synthetic",
            "symbols": ["SPY"],
            "rows": len(panel),
            "start": str(panel.index.min()),
            "end": str(panel.index.max()),
            "years": 0.01,
        },
    )


def _split_metrics():
    return {
        "train": {"cagr": 0.1},
        "validation": {"cagr": 0.1},
        "unseen": {
            "cagr": 0.1,
            "sharpe": 1.0,
            "mar": 1.0,
            "max_drawdown": -0.01,
            "profit_factor": 1.2,
            "trade_count": 1,
        },
    }


def _cost_stress():
    return {
        "normal_cost_bps": 5.0,
        "double_cost_bps": 10.0,
        "survives_double_cost": True,
        "double_unseen_cagr": 0.05,
    }
