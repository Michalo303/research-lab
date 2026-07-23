import json

import pandas as pd
import pytest

from research_lab.data import DataBundle
from research_lab.runner import run_daily_research
from research_lab.strategies.baselines import (
    StrategySpec,
    _proven_eodhd_result_rejection_reason,
    recovery_manifest_specs,
    select_daily_experiment_candidates,
    strategy_execution_fingerprint,
)

pytestmark = pytest.mark.usefixtures("hermetic_provider_guard")


def test_data_snapshot_identity_is_canonical_and_materially_sensitive():
    import research_lab.runner as runner

    manifest = {
        "name": "daily_universe",
        "source": "eodhd",
        "provider": "eodhd",
        "symbols": ["SPY", "QQQ"],
        "start": "2020-01-02",
        "end": "2026-07-22",
        "fallback_used": False,
        "rows": 1000,
        "created_at": "2026-07-23T00:00:00+00:00",
    }

    identity = runner._data_snapshot_identity(manifest)
    reordered = {key: manifest[key] for key in reversed(manifest)}
    assert runner._data_snapshot_identity(reordered) == identity

    for key, changed_value in (
        ("source", "synthetic"),
        ("provider", "other"),
        ("symbols", ["QQQ", "SPY"]),
        ("start", "2020-01-03"),
        ("end", "2026-07-23"),
        ("fallback_used", True),
    ):
        changed = {**manifest, key: changed_value}
        assert runner._data_snapshot_identity(changed) != identity

    without_fallback = dict(manifest)
    without_fallback.pop("fallback_used")
    assert runner._data_snapshot_identity(without_fallback) != identity


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {
            "source": "eodhd",
            "symbols": ["SPY"],
            "start": "x",
            "end": "y",
            "fallback_used": "false",
        },
        {
            "source": "eodhd",
            "symbols": ["SPY"],
            "start": "x",
            "end": "y",
            "fallback_used": "unknown",
        },
        {
            "source": "eodhd",
            "symbols": [],
            "start": "x",
            "end": "y",
            "fallback_used": False,
        },
    ],
)
def test_data_snapshot_identity_rejects_ambiguous_manifests(manifest):
    import research_lab.runner as runner

    with pytest.raises(ValueError):
        runner._data_snapshot_identity(manifest)


def test_daily_runner_skips_llm_queue_on_same_data_snapshot(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    bundle = _bundle(panel, source="eodhd", provider="eodhd")
    spec = _spec("H1")
    snapshot_identity = runner._data_snapshot_identity(bundle.manifest)
    experiments = tmp_path / "registry" / "experiments.jsonl"
    experiments.parent.mkdir(parents=True)
    experiments.write_text(
        json.dumps(
            {
                "strategy_id": "PRIOR_H1",
                "family": spec.family,
                "asset_class": spec.asset_class,
                "timeframe": spec.timeframe,
                "short_name": spec.short_name,
                "builder": spec.builder,
                "parameters": spec.parameters,
                "data_manifest": bundle.manifest,
                "data_snapshot_identity": snapshot_identity,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured_metadata = {}

    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *_args, **_kwargs: {
            "specs": [spec],
            "diagnostics": {
                "selection_mode": "normal_daily",
                "proposed": 1,
                "selected": 1,
            },
        },
    )
    monkeypatch.setattr(
        runner, "_load_daily_data_bundle", lambda _config, symbols=None: bundle
    )
    monkeypatch.setattr(
        runner,
        "build_weights",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("same-snapshot hypothesis must skip before evaluation")
        ),
    )
    monkeypatch.setattr(runner, "write_leaderboard", lambda *_args: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *_args: None)

    def capture_report(_root, _results, *, extra_metadata):
        captured_metadata.update(extra_metadata["daily_experiment_selection"])
        return {"latest_report_path": tmp_path / "daily.md"}

    monkeypatch.setattr(runner, "write_daily_report_artifacts", capture_report)

    assert run_daily_research(tmp_path) == []
    assert captured_metadata["same_snapshot_skipped"] == 1
    assert captured_metadata["attempted"] == 0
    assert strategy_execution_fingerprint(spec)


def test_daily_runner_retests_llm_queue_when_data_snapshot_changes(
    tmp_path, monkeypatch
):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    bundle = _bundle(panel, source="eodhd", provider="eodhd")
    spec = _spec("H1")
    prior_manifest = {**bundle.manifest, "end": "2025-12-31"}
    experiments = tmp_path / "registry" / "experiments.jsonl"
    experiments.parent.mkdir(parents=True)
    experiments.write_text(
        json.dumps(
            {
                "strategy_id": "PRIOR_H1",
                "family": spec.family,
                "asset_class": spec.asset_class,
                "timeframe": spec.timeframe,
                "short_name": spec.short_name,
                "builder": spec.builder,
                "parameters": spec.parameters,
                "data_manifest": prior_manifest,
                "data_snapshot_identity": runner._data_snapshot_identity(
                    prior_manifest
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *_args, **_kwargs: {
            "specs": [spec],
            "diagnostics": {
                "selection_mode": "normal_daily",
                "proposed": 1,
                "selected": 1,
            },
        },
    )
    monkeypatch.setattr(
        runner, "_load_daily_data_bundle", lambda _config, symbols=None: bundle
    )
    close = panel.xs("close", level=1, axis=1)
    monkeypatch.setattr(
        runner,
        "build_weights",
        lambda *_args: pd.DataFrame({"SPY": 1.0}, index=close.index),
    )
    _stub_completed_backtest(monkeypatch, runner, close)
    monkeypatch.setattr(
        runner,
        "write_daily_report_artifacts",
        lambda *_args, **_kwargs: {"latest_report_path": tmp_path / "daily.md"},
    )

    results = run_daily_research(tmp_path)

    assert len(results) == 1
    assert results[0]["data_snapshot_identity"] == runner._data_snapshot_identity(
        bundle.manifest
    )
    assert results[0]["data_snapshot_identity"] != runner._data_snapshot_identity(
        prior_manifest
    )


def test_daily_runner_skips_duplicate_executable_specs_before_evaluation(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    close = panel.xs("close", level=1, axis=1)
    first = _spec("H1")
    duplicate = _spec("H2")
    evaluated = []

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {"specs": [first, duplicate], "diagnostics": {"proposed": 2, "budget_selected": 2}},
    )

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
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"})

    results = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert len(results) == 1
    assert evaluated == ["H1"]


def test_daily_runner_surfaces_queued_candidate_dedupe_diagnostics_in_report_metadata(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    close = panel.xs("close", level=1, axis=1)
    evaluated = []
    first = _spec("H1")
    diagnostics = {
        "budget": 18,
        "recent_window": 50,
        "proposed": 5,
        "family_filtered": 1,
        "source_filtered": 1,
        "recent_duplicate_skipped": 1,
        "in_batch_duplicate_skipped": 1,
        "budget_selected": 1,
        "queue_rows_consumed": False,
        "retained_count": 1,
        "skipped_count": 2,
        "reasons": {
            "recent_executable_duplicate": 1,
            "effective_parameter_duplicate": 1,
        },
        "recovery_target": 1,
        "selected_new": 1,
        "covered_by_recent_real": 0,
        "recovery_resolved": 1,
        "recovery_shortfall": 0,
    }
    metadata = {}

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {"specs": [first], "diagnostics": diagnostics},
    )

    def fake_build_weights(spec, daily, intraday):
        evaluated.append(spec.parameters["source_hypothesis_id"])
        return pd.DataFrame({"SPY": 1.0}, index=close.index)

    def fake_report_artifacts(root, results, **kwargs):
        metadata.update(kwargs.get("extra_metadata", {}))
        return {"latest_report_path": tmp_path / "daily.md"}

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
    monkeypatch.setattr(runner, "write_daily_report_artifacts", fake_report_artifacts)

    results = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert len(results) == 1
    assert evaluated == ["H1"]
    assert metadata["daily_experiment_selection"]["budget_selected"] == 1
    assert metadata["daily_experiment_selection"]["recent_duplicate_skipped"] == 1
    assert metadata["daily_experiment_selection"]["queue_rows_consumed"] is False
    assert metadata["daily_experiment_selection"]["retained_count"] == 1
    assert metadata["daily_experiment_selection"]["skipped_count"] == 2
    assert metadata["daily_experiment_selection"]["reasons"] == {
        "recent_executable_duplicate": 1,
        "effective_parameter_duplicate": 1,
    }


def test_daily_runner_execution_accounting_reconciles_selected_candidates(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    close = panel.xs("close", level=1, axis=1)
    available = _spec("AVAILABLE")
    missing = StrategySpec(
        family="SWING",
        asset_class="ETF",
        timeframe="1D",
        short_name="MISSING",
        hypothesis="missing",
        parameters={**available.parameters, "symbol": "MISSING"},
        rules="same",
        builder="swing_trend_filtered_pullback",
    )
    metadata = {}

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {
            "specs": [available, missing],
            "diagnostics": {"proposed": 2, "selected": 2, "budget_selected": 2},
        },
    )
    monkeypatch.setattr(runner, "build_weights", lambda *args: pd.DataFrame({"SPY": 1.0}, index=close.index))
    _stub_completed_backtest(monkeypatch, runner, close)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda root, results, **kwargs: metadata.update(kwargs["extra_metadata"]) or {"latest_report_path": tmp_path / "daily.md"})

    results = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)
    counts = metadata["daily_experiment_selection"]
    assert len(results) == 1
    assert counts["selected"] == counts["attempted"] + counts["missing_data_skipped"] == 2
    assert counts["attempted"] == counts["completed"] == 1
    assert "execution_failed" not in counts


def test_daily_runner_preserves_fail_fast_backtest_exception_contract(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    panel = _panel()
    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel))
    monkeypatch.setattr(
        runner,
        "select_daily_experiment_candidates",
        lambda root, recovery_day: {"specs": [_spec("H1")], "diagnostics": {"proposed": 1, "selected": 1}},
    )
    monkeypatch.setattr(runner, "build_weights", lambda *args: (_ for _ in ()).throw(RuntimeError("backtest failed")))
    report_called = False

    def unexpected_report(*args, **kwargs):
        nonlocal report_called
        report_called = True

    monkeypatch.setattr(runner, "write_daily_report_artifacts", unexpected_report)

    with pytest.raises(RuntimeError, match="backtest failed"):
        run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)
    assert report_called is False


def test_daily_runner_stamps_section_level_provenance_without_mutating_source_dicts(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    spec = recovery_manifest_specs(1)[0]
    panel = _panel_for_spec(spec)
    close = panel.xs("close", level=1, axis=1)
    split_metrics = _split_metrics()
    cost_stress_result = _cost_stress()
    walk_forward_result = {"method": "true_rolling_oos", "status": "ok"}
    persisted = []

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel, source="eodhd", provider="eodhd"))
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: {
            "specs": [spec],
            "diagnostics": {
                "selection_mode": "bounded_recovery",
                "recovery_target": 1,
                "selected_new": 1,
                "covered_by_recent_real": 0,
                "recovery_resolved": 1,
                "recovery_shortfall": 0,
            },
        },
    )
    monkeypatch.setattr(runner, "build_weights", lambda *args: pd.DataFrame({"SPY": 1.0}, index=close.index))
    monkeypatch.setattr(
        runner,
        "weighted_backtest",
        lambda *args: {
            "metrics": {"cagr": 0.1},
            "split_metrics": split_metrics,
            "equity": pd.Series([1.0, 1.1], index=close.index),
            "returns": pd.Series([0.0, 0.1], index=close.index),
            "average_turnover": 0.0,
            "average_exposure": 1.0,
        },
    )
    monkeypatch.setattr(runner, "cost_stress", lambda *args: cost_stress_result)
    monkeypatch.setattr(runner, "compute_drawdown_diagnostics", lambda *args, **kwargs: {"max_drawdown": 0.0})
    monkeypatch.setattr(runner, "run_true_walk_forward", lambda *args, **kwargs: walk_forward_result)
    monkeypatch.setattr(runner, "classify_strategy", lambda *args: ("C", "test"))
    monkeypatch.setattr(runner, "_persist_result", lambda root, result: persisted.append(result))
    monkeypatch.setattr(runner, "_persist_hypothesis_result", lambda *args: None)
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args: None)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"})

    [result] = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert persisted == [result]
    assert result["data_source"] == "eodhd"
    assert result["split_metrics"]["data_source"] == "eodhd"
    assert result["cost_stress"]["data_source"] == "eodhd"
    assert result["walk_forward"]["data_source"] == "eodhd"
    assert "data_source" not in split_metrics
    assert "data_source" not in cost_stress_result
    assert "data_source" not in walk_forward_result


def test_runner_stamped_eodhd_result_counts_as_recent_real_coverage(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    spec = recovery_manifest_specs(1)[0]
    panel = _panel_for_spec(spec)
    close = panel.xs("close", level=1, axis=1)
    registry_result = {}

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel, source="eodhd", provider="eodhd"))
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: {
            "specs": [spec],
            "diagnostics": {
                "selection_mode": "bounded_recovery",
                "recovery_target": 1,
                "selected_new": 1,
                "covered_by_recent_real": 0,
                "recovery_resolved": 1,
                "recovery_shortfall": 0,
            },
        },
    )
    monkeypatch.setattr(runner, "build_weights", lambda *args: pd.DataFrame({str(panel.columns[0][0]): 1.0}, index=close.index))
    monkeypatch.setattr(
        runner,
        "weighted_backtest",
        lambda *args: {
                "metrics": {"cagr": 0.1},
                "split_metrics": {
                    "train": {"cagr": 0.1},
                    "validation": {"cagr": 0.1},
                    "unseen": {
                        "cagr": 0.05,
                        "sharpe": 1.0,
                        "mar": 0.6,
                        "max_drawdown": -0.08,
                        "profit_factor": 1.2,
                        "trade_count": 42,
                    },
                },
            "equity": pd.Series([1.0, 1.1], index=close.index),
            "returns": pd.Series([0.0, 0.1], index=close.index),
            "average_turnover": 0.0,
            "average_exposure": 1.0,
        },
    )
    monkeypatch.setattr(
        runner,
        "cost_stress",
        lambda *args: {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": True,
            "double_unseen_cagr": 0.04,
        },
    )
    monkeypatch.setattr(runner, "compute_drawdown_diagnostics", lambda *args, **kwargs: {"max_drawdown": 0.0})
    monkeypatch.setattr(
        runner,
        "run_true_walk_forward",
        lambda *args, **kwargs: {"window_count": 4, "pass_rate": 0.75},
    )
    monkeypatch.setattr(runner, "classify_strategy", lambda *args: ("C", "test"))
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args: None)
    monkeypatch.setattr(runner, "_persist_result", lambda root, result: registry_result.update(result))
    monkeypatch.setattr(runner, "_persist_hypothesis_result", lambda *args: None)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"})

    run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    experiments_path = tmp_path / "registry" / "experiments.jsonl"
    experiments_path.parent.mkdir(parents=True, exist_ok=True)
    experiments_path.write_text(json.dumps(registry_result) + "\n", encoding="utf-8")

    selection = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=50)

    assert selection["diagnostics"]["covered_by_recent_real"] == 1
    assert selection["diagnostics"]["selected_new"] == 3


def test_runner_stamps_non_eodhd_sources_and_keeps_them_ineligible_for_real_coverage(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    spec = recovery_manifest_specs(1)[0]
    panel = _panel_for_spec(spec)
    close = panel.xs("close", level=1, axis=1)
    persisted = []

    monkeypatch.setattr(runner, "_load_daily_data_bundle", lambda config, symbols=None: _bundle(panel, source="synthetic"))
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: {
            "specs": [spec],
            "diagnostics": {
                "selection_mode": "bounded_recovery",
                "recovery_target": 1,
                "selected_new": 1,
                "covered_by_recent_real": 0,
                "recovery_resolved": 1,
                "recovery_shortfall": 0,
            },
        },
    )
    monkeypatch.setattr(runner, "build_weights", lambda *args: pd.DataFrame({"SPY": 1.0}, index=close.index))
    _stub_completed_backtest(monkeypatch, runner, close)
    monkeypatch.setattr(runner, "_persist_result", lambda root, result: persisted.append(result))
    monkeypatch.setattr(runner, "_persist_hypothesis_result", lambda *args: None)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"})

    [result] = run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert result["data_source"] == "synthetic"
    assert result["split_metrics"]["data_source"] == "synthetic"
    assert result["cost_stress"]["data_source"] == "synthetic"
    assert result["walk_forward"]["data_source"] == "synthetic"
    assert _proven_eodhd_result_rejection_reason(result) == "non_real_eodhd_source"
    assert persisted == [result]


def test_unresolved_recovery_fails_before_data_loading_or_artifact_mutation(tmp_path, monkeypatch):
    import research_lab.runner as runner

    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: {
            "specs": [_spec("H1")],
            "diagnostics": {
                "selection_mode": "bounded_recovery",
                "recovery_target": 4,
                "selected_new": 1,
                "covered_by_recent_real": 0,
                "recovery_resolved": 1,
                "recovery_shortfall": 3,
            },
        },
    )

    def blocked(*args, **kwargs):
        raise AssertionError("mutation or execution path reached")

    monkeypatch.setattr(runner, "ensure_project_structure", blocked)
    monkeypatch.setattr(runner, "_load_daily_data_bundle", blocked)
    monkeypatch.setattr(runner, "write_daily_report_artifacts", blocked)
    monkeypatch.setattr(runner, "append_jsonl", blocked)

    with pytest.raises(RuntimeError, match="recovery.*shortfall|resolve all",):
        run_daily_research(tmp_path, recovery_mode=True, recovery_day=1)

    assert list(tmp_path.iterdir()) == []


def test_normal_daily_preserves_structure_setup_before_selection(tmp_path, monkeypatch):
    import research_lab.runner as runner

    order = []
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setattr(runner, "ensure_project_structure", lambda root: order.append("ensure"))
    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: order.append("select")
        or {
            "specs": [],
            "diagnostics": {
                "selection_mode": "normal_daily",
                "proposed": 0,
                "selected": 0,
            },
        },
    )
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner,
        "write_daily_report_artifacts",
        lambda *args, **kwargs: {"latest_report_path": tmp_path / "daily.md"},
    )

    assert run_daily_research(tmp_path) == []
    assert order == ["ensure", "select"]


def test_used_note_ids_stream_queue_once_for_all_selected_candidates(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import research_lab.runner as runner

    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        "\n".join(
            json.dumps({"hypothesis_id": hypothesis_id, "used_note_ids": [f"note-{index:016x}"]})
            for index, hypothesis_id in enumerate(("H1", "H2"), start=1)
        ),
        encoding="utf-8",
    )
    real_open = Path.open
    opens = 0

    def tracking_open(path, *args, **kwargs):
        nonlocal opens
        if path == queue:
            opens += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    found = runner._load_used_note_ids(tmp_path, {"H1", "H2"})
    assert opens == 1
    assert set(found) == {"H1", "H2"}


def _stub_completed_backtest(monkeypatch, runner, close):
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


def _panel_for_spec(spec: StrategySpec):
    symbols = []
    for key in ("symbol", "risk_symbol"):
        value = spec.parameters.get(key)
        if value and value not in symbols:
            symbols.append(value)
    for key in ("symbols", "risk_assets", "defensive_assets"):
        for value in spec.parameters.get(key, []):
            if value not in symbols:
                symbols.append(value)
    index = pd.bdate_range("2026-01-01", periods=2)
    return pd.concat(
        {
            symbol: pd.DataFrame(
                {
                    "open": [100.0, 101.0],
                    "high": [100.0, 101.0],
                    "low": [100.0, 101.0],
                    "close": [100.0, 101.0],
                    "volume": [1_000_000, 1_000_000],
                },
                index=index,
            )
            for symbol in symbols
        },
        axis=1,
    )


def _bundle(panel, *, source="synthetic", provider=None):
    return DataBundle(
        "daily_universe",
        "1D",
        panel,
        {
            "name": "daily_universe",
            "source": source,
            **({"provider": provider} if provider is not None else {}),
            "symbols": list(panel.columns.get_level_values(0).unique()),
            "rows": len(panel),
            "start": str(panel.index.min()),
            "end": str(panel.index.max()),
            "years": 0.01,
            "fallback_used": False,
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
@pytest.mark.parametrize(
    ("selection_mode", "queue_inspected", "candidate_source", "recovery_kwargs"),
    [
        ("bounded_recovery", False, "internal_recovery_manifest", {"recovery_mode": True, "recovery_day": 1}),
        ("normal_daily", True, "normal_baseline_guided_queue", {}),
    ],
)
def test_zero_selected_run_writes_completed_report_without_loading_data(
    tmp_path, monkeypatch, selection_mode, queue_inspected, candidate_source, recovery_kwargs
):
    import json
    from datetime import datetime, timezone
    from research_lab import runner
    from research_lab import reports

    fixed_timestamp = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(reports, "_utc_timestamp", lambda value: fixed_timestamp)

    monkeypatch.setattr(
        runner,
        "select_daily_candidates",
        lambda *args, **kwargs: {
            "specs": [],
            "diagnostics": {
                "proposed": 0,
                "selected": 0,
                "selection_mode": selection_mode,
                "queue_inspected": queue_inspected,
                "queue_consumed": False,
                "candidate_source": candidate_source,
                **(
                    {
                        "proposed": 4,
                        "recovery_target": 4,
                        "selected_new": 0,
                        "covered_by_recent_real": 4,
                        "recovery_resolved": 4,
                        "recovery_shortfall": 0,
                        "covered_recent_results": [],
                    }
                    if selection_mode == "bounded_recovery"
                    else {}
                ),
            },
        },
    )
    monkeypatch.setattr(
        runner,
        "_load_daily_data_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("zero selection must not load data")),
    )
    monkeypatch.setattr(runner, "write_leaderboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_allocation_model", lambda *args, **kwargs: None)

    assert run_daily_research(tmp_path, **recovery_kwargs) == []
    expected_report_path = tmp_path / "reports" / "daily" / "2026-07-04.md"
    assert expected_report_path.exists()
    metadata_paths = list((tmp_path / "reports" / "runs" / "2026-07-04").glob("*/run_metadata.json"))
    assert len(metadata_paths) == 1
    metadata = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
    assert metadata["latest_report_path"] == "reports/daily/2026-07-04.md"
    report_path = tmp_path / metadata["run_report_path"]
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    counts = metadata["daily_experiment_selection"]
    assert counts["selected"] == counts["attempted"] == counts["completed"] == 0
    assert counts["missing_data_skipped"] == 0
    assert "execution_failed" not in counts
    assert "execution_failed" not in metadata
    assert "execution_failed" not in report
    funnel = metadata["daily_experiment_funnel"]
    assert funnel["queue_inspected"] is queue_inspected
    assert funnel["queue_consumed"] is False
    assert funnel["candidate_source"] == candidate_source
    assert f"queue inspected: {str(queue_inspected).lower()}" in report
