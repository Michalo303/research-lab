import pandas as pd

from research_lab.data import DataBundle
from research_lab.runner import run_daily_research
from research_lab.strategies.baselines import StrategySpec


def test_daily_runner_skips_duplicate_executable_specs_before_evaluation(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    close = panel.xs("close", level=1, axis=1)
    first = _spec("H1")
    duplicate = _spec("H2")
    evaluated = []

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config: _bundle(panel))
    monkeypatch.setattr(runner, "load_intraday_symbol", lambda root, symbol: _bundle(panel))
    monkeypatch.setattr(runner, "baseline_strategies", lambda: [first, duplicate])
    monkeypatch.setattr(runner, "next_run_guided_strategies", lambda root, limit: [])
    monkeypatch.setattr(runner, "queued_hypothesis_strategies", lambda root, limit: [])

    def fake_build_weights(spec, daily, intraday):
        evaluated.append(spec.parameters["source_hypothesis_id"])
        return pd.DataFrame({"SPY": 1.0}, index=close.index)

    monkeypatch.setattr(runner, "build_weights", fake_build_weights)
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
    monkeypatch.setattr(runner, "run_true_walk_forward", lambda *args, **kwargs: {"method": "true_rolling_oos", "status": "ok"})
    monkeypatch.setattr(runner, "classify_strategy", lambda *args: ("C", "test"))
    monkeypatch.setattr(runner, "_persist_result", lambda *args: None)
    monkeypatch.setattr(runner, "_persist_hypothesis_result", lambda *args: None)
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args: None)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args: {"latest_report_path": tmp_path / "daily.md"})

    results = run_daily_research(tmp_path)

    assert len(results) == 1
    assert evaluated == ["H1"]


def _spec(source_hypothesis_id: str) -> StrategySpec:
    return StrategySpec(
        family="SWING",
        asset_class="ETF",
        timeframe="1D",
        short_name="QUEUE_PULLBACK",
        hypothesis=f"queued source {source_hypothesis_id}",
        parameters={
            "symbol": "SPY",
            "fast_sma": 50,
            "slow_sma": 150,
            "rsi_entry": 40,
            "rsi_exit": 58,
            "atr_stop": 2.0,
            "source_hypothesis_id": source_hypothesis_id,
            "source_title": f"source {source_hypothesis_id}",
        },
        rules="same executable rules",
        builder="swing_trend_filtered_pullback",
    )


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
            "trade_count": 150,
        },
    }


def _cost_stress():
    return {
        "normal_cost_bps": 5.0,
        "double_cost_bps": 10.0,
        "survives_double_cost": True,
        "double_unseen_cagr": 0.05,
    }
