import json

from research_lab.strategies.baselines import (
    StrategySpec,
    dedupe_strategy_specs,
    next_run_guided_strategies,
    queued_hypothesis_strategies,
    select_queued_hypothesis_candidates,
)


def test_near_miss_tier_c_strategy_generates_conservative_next_run_variants(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="LONGTERM_ETF_1D_TREND_VOL_CAP_20260606_006",
                short_name="TREND_VOL_CAP",
                builder="long_term_vol_target_cap",
                tier="C",
                parameters={"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.10, "max_weight": 0.75},
                unseen_cagr=0.0399,
                unseen_max_drawdown=-0.1339,
                wf_pass_rate=0.5714,
            )
        ],
    )

    specs = next_run_guided_strategies(tmp_path, limit=2)

    assert [spec.short_name for spec in specs] == ["TREND_VOL_CAP_CONSERVATIVE", "TREND_VOL_CAP_STABLE"]
    assert all(spec.builder == "long_term_vol_target_cap" for spec in specs)
    assert all(spec.parameters["target_vol"] < 0.10 for spec in specs)
    assert all(spec.parameters["max_weight"] < 0.75 for spec in specs)


def test_high_drawdown_queue_families_are_deprioritized_but_not_banned(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.62,
            ),
            _result(
                strategy_id="LONGTERM_ETF_1D_QUEUE_VOL_TARGET_20260606_011",
                family="LONGTERM",
                short_name="QUEUE_VOL_TARGET",
                builder="long_term_vol_target",
                unseen_max_drawdown=-0.14,
            ),
        ],
    )
    _write_queue(
        tmp_path,
        [
            {"hypothesis_id": "SWING_1", "family": "SWING", "ticker": "QQQ", "title": "Pullback candidate", "source_title": "source"},
            {"hypothesis_id": "LONG_1", "family": "LONGTERM", "title": "Long-term vol candidate", "source_title": "source"},
        ],
    )

    specs = queued_hypothesis_strategies(tmp_path, limit=2)

    assert [spec.short_name for spec in specs] == ["QUEUE_VOL_TARGET", "QUEUE_PULLBACK"]


def test_failure_memory_prefers_materially_changed_risk_repair_candidate(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.34,
                unseen_trades=24,
            )
        ],
    )
    _write_queue(
        tmp_path,
        [
            {"hypothesis_id": "SWING_1", "family": "SWING", "ticker": "QQQ", "title": "Plain pullback", "source_title": "source"},
            {
                "hypothesis_id": "SWING_2",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Pullback with explicit risk repair",
                "source_title": "source",
                "risk_overlay_changed": True,
                "min_unseen_trades_target": 120,
            },
        ],
    )

    specs = queued_hypothesis_strategies(tmp_path, limit=2)

    assert [spec.parameters["source_hypothesis_id"] for spec in specs] == ["SWING_2", "SWING_1"]


def test_exact_duplicate_queue_rows_collapse_to_one_candidate_with_semantic_reason(tmp_path):
    _write_queue(
        tmp_path,
        [
            {"hypothesis_id": "SWING_1", "family": "SWING", "ticker": "QQQ", "title": "Plain pullback", "source_title": "source"},
            {"hypothesis_id": "SWING_1_DUP", "family": "SWING", "ticker": "QQQ", "title": "Plain pullback", "source_title": "source"},
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert [spec.parameters["source_hypothesis_id"] for spec in selection["specs"]] == ["SWING_1"]
    assert selection["diagnostics"]["retained_count"] == 1
    assert selection["diagnostics"]["skipped_count"] == 1
    assert selection["diagnostics"]["reasons"] == {"semantic_queue_duplicate": 1}
    assert selection["diagnostics"]["skipped"][0]["reason_code"] == "semantic_queue_duplicate"


def test_equivalent_executable_queue_rows_collapse_to_one_candidate_with_effective_parameter_reason(tmp_path):
    _write_queue(
        tmp_path,
        [
            {"hypothesis_id": "SWING_1", "family": "SWING", "ticker": "QQQ", "title": "Plain pullback", "source_title": "source"},
            {
                "hypothesis_id": "SWING_2",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Annotated pullback",
                "source_title": "source",
                "walk_forward_repair": True,
                "trade_count_repair": True,
                "min_unseen_trades_target": 100,
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert [spec.parameters["source_hypothesis_id"] for spec in selection["specs"]] == ["SWING_1"]
    assert selection["diagnostics"]["retained_count"] == 1
    assert selection["diagnostics"]["skipped_count"] == 1
    assert selection["diagnostics"]["reasons"] == {"effective_parameter_duplicate": 1}
    assert selection["diagnostics"]["skipped"][0]["reason_code"] == "effective_parameter_duplicate"


def test_materially_different_risk_overlay_mutation_is_retained_when_executable_params_change(tmp_path):
    _write_queue(
        tmp_path,
        [
            {"hypothesis_id": "SWING_1", "family": "SWING", "ticker": "QQQ", "title": "Plain pullback", "source_title": "source"},
            {
                "hypothesis_id": "SWING_2",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Plain pullback",
                "source_title": "source",
                "risk_overlay_changed": True,
                "max_exposure": 0.35,
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert {spec.parameters["source_hypothesis_id"] for spec in selection["specs"]} == {"SWING_1", "SWING_2"}
    assert selection["diagnostics"]["retained_count"] == 2
    assert selection["diagnostics"]["skipped_count"] == 0
    assert selection["diagnostics"]["reasons"] == {}


def test_non_executable_row_does_not_poison_semantic_or_source_key_dedupe_state(tmp_path):
    _write_queue(
        tmp_path,
        [
            {
                "hypothesis_id": "INVALID_1",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Plain pullback",
                "source_title": "source",
                "llm_generated": True,
            },
            {
                "hypothesis_id": "SWING_1",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Plain pullback",
                "source_title": "source",
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert [spec.parameters["source_hypothesis_id"] for spec in selection["specs"]] == ["SWING_1"]
    assert selection["diagnostics"]["retained_count"] == 1
    assert selection["diagnostics"]["skipped_count"] == 0


def test_shared_source_key_across_strategy_families_does_not_suppress_candidates(tmp_path):
    _write_queue(
        tmp_path,
        [
            {
                "hypothesis_id": "SWING_1",
                "source_key": "shared:research-note",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Pullback",
                "source_title": "source",
            },
            {
                "hypothesis_id": "LONG_1",
                "source_key": "shared:research-note",
                "family": "LONGTERM",
                "title": "Volatility target",
                "source_title": "source",
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert {spec.parameters["source_hypothesis_id"] for spec in selection["specs"]} == {"SWING_1", "LONG_1"}
    assert selection["diagnostics"]["retained_count"] == 2
    assert selection["diagnostics"]["skipped_count"] == 0


def test_shared_source_key_does_not_suppress_materially_different_execution_parameters(tmp_path):
    _write_queue(
        tmp_path,
        [
            {
                "hypothesis_id": "SWING_1",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Plain pullback",
                "source_title": "source",
            },
            {
                "hypothesis_id": "SWING_2",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Capped pullback",
                "source_title": "source",
                "risk_overlay_changed": True,
                "max_exposure": 0.35,
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert {spec.parameters["source_hypothesis_id"] for spec in selection["specs"]} == {"SWING_1", "SWING_2"}
    assert selection["diagnostics"]["retained_count"] == 2
    assert selection["diagnostics"]["skipped_count"] == 0


def test_shared_source_key_suppresses_only_when_execution_identity_is_equal(tmp_path):
    _write_queue(
        tmp_path,
        [
            {
                "hypothesis_id": "SWING_1",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Plain pullback",
                "source_title": "source",
            },
            {
                "hypothesis_id": "SWING_2",
                "source_key": "shared:pullback",
                "family": "SWING",
                "ticker": "QQQ",
                "title": "Annotated pullback",
                "source_title": "source",
                "walk_forward_repair": True,
            },
        ],
    )

    selection = select_queued_hypothesis_candidates(tmp_path, limit=4)

    assert [spec.parameters["source_hypothesis_id"] for spec in selection["specs"]] == ["SWING_1"]
    assert selection["diagnostics"]["reasons"] == {"source_key_duplicate": 1}


def test_executable_dedupe_collapses_specs_that_only_reorder_unordered_symbols():
    first = _spec("FIRST", {"symbols": ["SPY", "TLT"], "lookback": 126})
    second = _spec("SECOND", {"symbols": ["TLT", "SPY"], "lookback": 126})

    retained = dedupe_strategy_specs([first, second])

    assert [spec.short_name for spec in retained] == ["FIRST"]


def test_executable_dedupe_preserves_order_sensitive_weight_lists():
    first = _spec("FIRST", {"symbols": ["SPY", "TLT"], "weights": [0.6, 0.4]})
    second = _spec("SECOND", {"symbols": ["SPY", "TLT"], "weights": [0.4, 0.6]})

    retained = dedupe_strategy_specs([first, second])

    assert [spec.short_name for spec in retained] == ["FIRST", "SECOND"]


def test_executable_dedupe_does_not_reorder_weights_when_symbols_are_reordered():
    first = _spec("FIRST", {"symbols": ["SPY", "TLT"], "weights": [0.6, 0.4]})
    second = _spec("SECOND", {"symbols": ["TLT", "SPY"], "weights": [0.4, 0.6]})

    retained = dedupe_strategy_specs([first, second])

    assert [spec.short_name for spec in retained] == ["FIRST", "SECOND"]


def _write_experiments(root, rows):
    path = root / "registry" / "experiments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_queue(root, rows):
    path = root / "registry" / "hypothesis_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _spec(short_name, parameters):
    return StrategySpec(
        family="ROTATION",
        asset_class="ETF",
        timeframe="1D",
        short_name=short_name,
        hypothesis="test",
        parameters=parameters,
        rules="test",
        builder="active_momentum_rotation",
    )


def _result(
    *,
    strategy_id: str,
    family: str = "LONGTERM",
    short_name: str,
    builder: str,
    tier: str = "Rejected",
    parameters: dict | None = None,
    unseen_cagr: float = 0.02,
    unseen_max_drawdown: float = -0.10,
    unseen_trades: int = 150,
    wf_pass_rate: float = 0.8,
):
    return {
        "strategy_id": strategy_id,
        "family": family,
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": short_name,
        "tier": tier,
        "parameters": parameters or {"symbol": "SPY"},
        "rules": "test",
        "hypothesis": "test",
        "builder": builder,
        "split_metrics": {
            "train": {"cagr": 0.034},
            "validation": {"cagr": 0.0545},
            "unseen": {"cagr": unseen_cagr, "max_drawdown": unseen_max_drawdown, "trade_count": unseen_trades},
        },
        "cost_stress": {"survives_double_cost": True},
        "walk_forward": {
            "method": "true_rolling_oos",
            "status": "ok",
            "window_count": 7,
            "pass_rate": wf_pass_rate,
            "median_test_cagr": 0.01,
            "worst_test_drawdown": -0.18,
        },
    }
