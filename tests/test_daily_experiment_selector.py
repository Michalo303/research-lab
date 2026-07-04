import json
from datetime import date

import pytest

from research_lab.reports import build_daily_experiment_funnel, render_daily_experiment_funnel
from research_lab.runner import select_daily_candidates
from research_lab.strategies.baselines import (
    RECOVERY_ALLOWED_BUILDERS,
    RECOVERY_EXECUTABLE_PARAMETER_SCHEMAS,
    StrategySpec,
    _recent_experiment_results,
    recovery_manifest_specs,
    select_daily_experiment_candidates,
    strategy_execution_fingerprint,
)

pytestmark = pytest.mark.usefixtures("hermetic_provider_guard")


def test_fingerprint_is_builder_aware_and_metadata_insensitive():
    base = _spec(
        "swing_trend_filtered_pullback",
        family="SWING",
        parameters={
            "symbol": "qqq",
            "fast_sma": 50,
            "slow_sma": 150,
            "rsi_entry": 38,
            "rsi_exit": 58,
            "atr_stop": 2.0,
            "max_exposure": 0.5,
            "source_hypothesis_id": "H1",
            "title": "first",
        },
    )
    metadata_changed = _spec(
        "swing_trend_filtered_pullback",
        family="SWING",
        asset_class="FUND",
        parameters={
            **base.parameters,
            "source_hypothesis_id": "H2",
            "title": "SECOND",
            "tags": ["B", "A"],
        },
    )
    assert strategy_execution_fingerprint(base) == strategy_execution_fingerprint(metadata_changed)

    for key, value in {
        "symbol": "SPY",
        "fast_sma": 100,
        "slow_sma": 200,
        "rsi_entry": 32,
        "rsi_exit": 65,
        "atr_stop": 3.0,
        "max_exposure": 0.25,
    }.items():
        changed = _spec(
            "swing_trend_filtered_pullback",
            family="SWING",
            parameters={**base.parameters, key: value},
        )
        assert strategy_execution_fingerprint(base) != strategy_execution_fingerprint(changed), key


def test_fingerprint_preserves_ordered_defensive_lists_and_arbitrary_string_case():
    base = _spec(
        "defensive_asset_rotation",
        family="ROTATION",
        parameters={
            "risk_assets": ["SPY", "QQQ", "IWM"],
            "defensive_assets": ["IEF", "TLT", "GLD"],
            "lookback": 126,
            "top_n": 1,
            "risk_symbol": "SPY",
            "risk_sma": 200,
        },
    )
    reversed_risk = _replace_parameters(base, risk_assets=list(reversed(base.parameters["risk_assets"])))
    reversed_defensive = _replace_parameters(base, defensive_assets=list(reversed(base.parameters["defensive_assets"])))
    assert strategy_execution_fingerprint(base) != strategy_execution_fingerprint(reversed_risk)
    assert strategy_execution_fingerprint(base) != strategy_execution_fingerprint(reversed_defensive)

    ticker_case = _replace_parameters(base, risk_symbol="spy")
    assert strategy_execution_fingerprint(base) == strategy_execution_fingerprint(ticker_case)
    generic_case = _spec("active_momentum_rotation", family="ROTATION", parameters={"mode": "RiskOn"})
    generic_lower = _spec("active_momentum_rotation", family="ROTATION", parameters={"mode": "riskon"})
    assert strategy_execution_fingerprint(generic_case) != strategy_execution_fingerprint(generic_lower)


def test_every_recovery_schema_parameter_changes_the_fingerprint():
    specs = [spec for day in range(1, 8) for spec in recovery_manifest_specs(day)]
    base_by_builder = {spec.builder: spec for spec in specs}
    assert set(base_by_builder) == RECOVERY_ALLOWED_BUILDERS
    for builder, keys in RECOVERY_EXECUTABLE_PARAMETER_SCHEMAS.items():
        base = base_by_builder[builder]
        for key in keys:
            value = base.parameters[key]
            if isinstance(value, list):
                changed_value = list(reversed(value))
            elif isinstance(value, str):
                changed_value = "QQQ" if value.upper() != "QQQ" else "SPY"
            elif isinstance(value, int):
                changed_value = value + 1
            else:
                changed_value = value + 0.01
            changed = _replace_parameters(base, **{key: changed_value})
            assert strategy_execution_fingerprint(base) != strategy_execution_fingerprint(changed), f"{builder}.{key}"


@pytest.mark.parametrize("missing_key", ["symbol", "trend_sma", "rsi_entry", "rsi_exit"])
def test_swing_rsi_pullback_rejects_missing_required_fingerprint_fields(missing_key):
    parameters = {"symbol": "SPY", "trend_sma": 100, "rsi_entry": 35, "rsi_exit": 55}
    parameters.pop(missing_key)
    malformed = _spec("swing_rsi_pullback", family="SWING", parameters=parameters)
    with pytest.raises(ValueError, match=missing_key):
        strategy_execution_fingerprint(malformed)


@pytest.mark.parametrize(
    ("builder", "family", "explicit"),
    [
        ("long_term_vol_target", "LONGTERM", {"symbol": "SPY", "sma": 150, "vol_window": 63, "target_vol": 0.12}),
        ("long_term_vol_target_cap", "LONGTERM", {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.10, "max_weight": 0.75}),
        ("swing_trend_filtered_pullback", "SWING", {"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0, "max_exposure": 1.0}),
        ("defensive_asset_rotation", "ROTATION", {"risk_assets": ["SPY", "QQQ"], "defensive_assets": ["TLT", "GLD"], "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 200}),
    ],
)
def test_builder_supported_omissions_match_explicit_execution_defaults(builder, family, explicit):
    omitted = _spec(builder, family=family, parameters={})
    supplied = _spec(builder, family=family, parameters=explicit)
    assert strategy_execution_fingerprint(omitted) == strategy_execution_fingerprint(supplied)


@pytest.mark.parametrize("recovery_day", range(1, 8))
def test_runner_boundary_uses_explicit_recovery_days(tmp_path, recovery_day):
    first = select_daily_candidates(tmp_path, recovery_mode=True, recovery_day=recovery_day)
    second = select_daily_candidates(tmp_path, recovery_mode=True, recovery_day=recovery_day)
    first_fingerprints = [strategy_execution_fingerprint(spec) for spec in first["specs"]]
    assert first_fingerprints == [strategy_execution_fingerprint(spec) for spec in second["specs"]]
    assert len(first_fingerprints) == 4
    assert first["diagnostics"]["selection_mode"] == "bounded_recovery"
    assert first["diagnostics"]["recovery_day"] == recovery_day


def test_runner_boundary_resumes_normal_selection_after_day_seven(tmp_path):
    selection = select_daily_candidates(tmp_path, recovery_mode=True, recovery_day=8)
    assert selection["diagnostics"]["selection_mode"] == "normal_daily"
    assert selection["diagnostics"]["queue_inspected"] is True
    assert selection["diagnostics"]["queue_consumed"] is False
    assert selection["diagnostics"]["candidate_source"] == "normal_baseline_guided_queue"
    assert [spec.short_name for spec in selection["specs"]] == [
        "TREND_FILTER",
        "DUAL_MOMENTUM",
        "RSI_PULLBACK",
        "VWAP_RSI_RECLAIM",
        "TREND_STRICT_CASH",
        "TREND_VOL_CAP",
        "DUAL_MOMENTUM_DD_CB",
        "DEFENSIVE_ROTATION",
    ]


@pytest.mark.parametrize("recovery_day", [None, 0, -1, "2", 1.5, True])
def test_runner_boundary_rejects_missing_or_malformed_recovery_day(tmp_path, recovery_day):
    with pytest.raises(ValueError, match="recovery_day"):
        select_daily_candidates(tmp_path, recovery_mode=True, recovery_day=recovery_day)


def test_normal_mode_ignores_recovery_day_and_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setattr("research_lab.strategies.baselines.date", None)
    selection = select_daily_candidates(tmp_path, recovery_mode=False, recovery_day=3)
    assert selection["diagnostics"]["selection_mode"] == "normal_daily"


def test_recent_history_tail_is_bounded_and_zero_reads_nothing(tmp_path, monkeypatch):
    path = tmp_path / "registry" / "experiments.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join([json.dumps({"row": index}) for index in range(20)] + ["malformed", json.dumps({"row": 21})]) + "\n", encoding="utf-8")

    assert _recent_experiment_results(tmp_path, max_rows=0) == []
    assert _recent_experiment_results(tmp_path, max_rows=-1) == []
    assert _recent_experiment_results(tmp_path, max_rows=3) == [{"row": 19}, {"row": 21}]
    assert _recent_experiment_results(tmp_path, max_rows=5) == [{"row": 17}, {"row": 18}, {"row": 19}, {"row": 21}]

    parsed = []
    real_loads = json.loads

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr("research_lab.strategies.baselines.json.loads", tracking_loads)
    assert _recent_experiment_results(tmp_path, max_rows=2) == [{"row": 21}]
    assert len(parsed) == 2


def test_selector_order_and_budget_are_independent_of_queue_row_order(tmp_path):
    rows = [_self_declared_queue_row(index) for index in range(30)]
    _write_queue(tmp_path, rows)
    first = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=0)
    _write_queue(tmp_path, list(reversed(rows)))
    second = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=0)

    first_fingerprints = [strategy_execution_fingerprint(spec) for spec in first["specs"]]
    second_fingerprints = [strategy_execution_fingerprint(spec) for spec in second["specs"]]
    assert first_fingerprints == second_fingerprints
    assert len(first_fingerprints) == 4
    assert first["diagnostics"]["budget_skipped"] == 0
    assert first["diagnostics"]["selected"] == 4


def test_recovery_selector_does_not_open_queue(tmp_path, monkeypatch):
    from pathlib import Path

    _write_queue(tmp_path, [_self_declared_queue_row(index) for index in range(4)])
    queue_path = tmp_path / "registry" / "hypothesis_queue.jsonl"
    real_open = Path.open
    queue_opens = 0

    def tracking_open(path, *args, **kwargs):
        nonlocal queue_opens
        if path == queue_path:
            queue_opens += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=0)
    assert queue_opens == 0


def test_recovery_selection_ignores_all_queue_provenance_and_content(tmp_path):
    rows = [
        _self_declared_queue_row(1),
        {**_self_declared_queue_row(2), "llm_generated": True},
        {**_self_declared_queue_row(3), "hermes_run_id": "run"},
        {**_self_declared_queue_row(4), "hermes_provider": "provider"},
        {"malformed": ["creative-only"]},
        {**_self_declared_queue_row(6), "source_type": "provider"},
    ]
    _write_queue(tmp_path, rows)
    selection = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=0)
    assert {strategy_execution_fingerprint(spec) for spec in selection["specs"]} == {
        strategy_execution_fingerprint(spec) for spec in recovery_manifest_specs(1)
    }
    assert selection["diagnostics"]["proposed"] == 4
    assert selection["diagnostics"]["source_filtered"] == 0


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_self_declared_queue_schema_version_cannot_enter_recovery(tmp_path, version):
    _write_queue(tmp_path, [{**_self_declared_queue_row(1), "source_schema_version": version}])
    selection = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=0)
    assert {strategy_execution_fingerprint(spec) for spec in selection["specs"]} == {
        strategy_execution_fingerprint(spec) for spec in recovery_manifest_specs(1)
    }


def test_seven_day_manifest_is_unique_bounded_and_queue_independent(tmp_path):
    all_fingerprints = []
    counts = []
    for recovery_day in range(1, 8):
        specs = recovery_manifest_specs(recovery_day)
        fingerprints = [strategy_execution_fingerprint(spec) for spec in specs]
        counts.append(len(fingerprints))
        assert 0 < len(fingerprints) <= 18
        assert len(fingerprints) == len(set(fingerprints))
        assert all(spec.builder in RECOVERY_ALLOWED_BUILDERS for spec in specs)
        all_fingerprints.extend(fingerprints)
        selection = select_daily_experiment_candidates(tmp_path, recovery_day=recovery_day, budget=18, recent_window=0)
        assert sorted(fingerprints) == [strategy_execution_fingerprint(spec) for spec in selection["specs"]]
    assert len(all_fingerprints) == len(set(all_fingerprints))
    assert recovery_manifest_specs(8) == []
    assert counts[0] > 0 and counts[1] > 0


def test_recent_fingerprints_are_excluded_without_padding(tmp_path):
    first_day = recovery_manifest_specs(1)
    _write_experiments(tmp_path, [_result_from_spec(spec, index) for index, spec in enumerate(first_day)])
    selection = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=18, recent_window=50)
    assert selection["specs"] == []
    assert selection["diagnostics"]["recent_duplicate_skipped"] == len(first_day)
    assert selection["diagnostics"]["selected"] == 0


def test_selector_accounting_reconciles_every_proposal(tmp_path):
    rows = [_self_declared_queue_row(1), {"malformed": True}]
    _write_queue(tmp_path, rows)
    selection = select_daily_experiment_candidates(tmp_path, recovery_day=1, budget=2, recent_window=0)
    counts = selection["diagnostics"]
    assert counts["proposed"] == sum(
        counts[key]
        for key in (
            "family_filtered",
            "source_filtered",
            "invalid_filtered",
            "recent_duplicate_skipped",
            "in_batch_duplicate_skipped",
            "budget_skipped",
            "selected",
        )
    )
    assert counts["queue_rows_consumed"] is False
    assert counts["reasons"] == {}
    assert counts["retained_count"] == counts["selected"]
    assert counts["skipped_count"] == 0


def test_compact_funnel_separates_execution_counts_and_independent_diagnostics():
    selection = {
        "proposed": 8,
        "family_filtered": 1,
        "source_filtered": 1,
        "invalid_filtered": 0,
        "recent_duplicate_skipped": 1,
        "in_batch_duplicate_skipped": 1,
        "budget_skipped": 1,
        "selected": 3,
        "attempted": 2,
        "completed": 2,
        "missing_data_skipped": 1,
        "queue_rows_consumed": False,
    }
    results = [_result(tier="B", unseen_cagr=0.05, unseen_max_drawdown=-0.09, survives_double_cost=None)]
    funnel = build_daily_experiment_funnel(results, selection)
    assert funnel["selector_counts"]["selected"] == 3
    assert funnel["execution_counts"] == {
        "attempted": 2,
        "completed": 2,
        "missing_data_skipped": 1,
    }
    assert funnel["execution_failure_contract"] == "fail_fast_no_completed_report"
    assert funnel["result_diagnostics"] == {
        "positive_oos": 1,
        "tier_drawdown_pass_15pct": 1,
        "recovery_drawdown_pass_10pct": 1,
        "walk_forward_pass": 1,
        "cost_pass": 0,
        "tier_ab": 1,
        "deployment_gate_pass": 0,
    }
    rendered = "\n".join(render_daily_experiment_funnel(funnel))
    assert "overlapping, non-exclusive" in rendered
    assert "tier_drawdown_pass_15pct" in rendered
    assert "recovery_drawdown_pass_10pct" in rendered


@pytest.mark.parametrize(
    ("selection_mode", "queue_inspected", "candidate_source"),
    [
        ("bounded_recovery", False, "internal_recovery_manifest"),
        ("normal_daily", True, "normal_baseline_guided_queue"),
    ],
)
def test_compact_funnel_reports_mode_aware_queue_semantics(selection_mode, queue_inspected, candidate_source):
    funnel = build_daily_experiment_funnel([], {
        "selection_mode": selection_mode,
        "queue_inspected": queue_inspected,
        "queue_consumed": False,
        "candidate_source": candidate_source,
    })
    rendered = "\n".join(render_daily_experiment_funnel(funnel))
    assert funnel["selection_mode"] == selection_mode
    assert funnel["queue_inspected"] is queue_inspected
    assert funnel["queue_consumed"] is False
    assert funnel["candidate_source"] == candidate_source
    assert f"queue inspected: {str(queue_inspected).lower()}" in rendered
    assert "queue consumed: false" in rendered
    if not queue_inspected:
        assert "queue rows were inspected" not in rendered


def _spec(builder, *, family, parameters, asset_class="ETF", timeframe="1D"):
    return StrategySpec(
        family=family,
        asset_class=asset_class,
        timeframe=timeframe,
        short_name="TEST",
        hypothesis="metadata",
        parameters=parameters,
        rules="metadata",
        builder=builder,
    )


def _replace_parameters(spec, **updates):
    return _spec(spec.builder, family=spec.family, parameters={**spec.parameters, **updates}, asset_class=spec.asset_class)


def _self_declared_queue_row(index, hypothesis_id=None):
    return {
        "hypothesis_id": hypothesis_id or f"Q{index:02d}",
        "source_type": "deterministic_first_party",
        "source_producer": "research_lab.recovery_queue",
        "source_schema_version": 1,
        "source_id": f"manual:{index:02d}",
        "family": "SWING",
        "ticker": f"ETF{index:02d}",
        "title": f"Swing {index:02d}",
        "parameters": {"rsi_entry": 30 + index},
    }


def _write_queue(root, rows):
    path = root / "registry" / "hypothesis_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_experiments(root, rows):
    path = root / "registry" / "experiments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _result_from_spec(spec, index):
    return {
        "strategy_id": f"RESULT_{index}",
        "family": spec.family,
        "asset_class": spec.asset_class,
        "timeframe": spec.timeframe,
        "short_name": spec.short_name,
        "builder": spec.builder,
        "parameters": spec.parameters,
    }


def _result(*, tier, unseen_cagr, unseen_max_drawdown, survives_double_cost):
    return {
        "strategy_id": "R1",
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "builder": "long_term_vol_target_cap",
        "parameters": {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.6},
        "tier": tier,
        "tier_reason": "test",
        "data_manifest": {"source": "eodhd", "years": 30},
        "split_metrics": {"train": {"cagr": 0.1}, "validation": {"cagr": 0.1}, "unseen": {"cagr": unseen_cagr, "max_drawdown": unseen_max_drawdown, "trade_count": 150}},
        "cost_stress": {} if survives_double_cost is None else {"survives_double_cost": survives_double_cost},
        "walk_forward": {"method": "true_rolling_oos", "status": "ok", "window_count": 3, "pass_rate": 0.67, "median_test_cagr": 0.01, "worst_test_drawdown": -0.10},
    }
