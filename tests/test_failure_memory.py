import json

from research_lab.failure_memory import build_failure_memory, execution_parameter_signature
from research_lab.strategies.baselines import (
    StrategySpec,
    dedupe_strategy_specs,
    queued_hypothesis_strategies,
    strategy_execution_fingerprint,
)


def test_failure_memory_penalizes_same_unrepaired_failure_pattern(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.32,
                unseen_trades=24,
            )
        ],
    )
    memory = build_failure_memory(tmp_path)

    repeated = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
    )
    repaired = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK_RISK_REPAIR",
        builder="swing_trend_filtered_pullback",
        parameters={
            "symbol": "QQQ",
            "fast_sma": 50,
            "slow_sma": 150,
            "rsi_entry": 35,
            "rsi_exit": 58,
            "atr_stop": 2.0,
            "risk_overlay_changed": True,
            "min_unseen_trades_target": 100,
        },
    )

    repeated_penalty = memory.penalty_for_spec(repeated)
    repaired_penalty = memory.penalty_for_spec(repaired)

    assert repeated_penalty.score > repaired_penalty.score
    assert "max drawdown too deep" in repeated_penalty.reasons
    assert "too few unseen trades" in repeated_penalty.reasons


def test_queued_hypotheses_use_failure_memory_to_prefer_executable_parameter_changes(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.32,
                unseen_trades=24,
                parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
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
                "title": "Pullback with executable threshold change",
                "source_title": "source",
                "parameters": {"rsi_entry": 35},
            },
        ],
    )

    specs = queued_hypothesis_strategies(tmp_path, limit=2)

    assert [spec.parameters["source_hypothesis_id"] for spec in specs] == ["SWING_2", "SWING_1"]


def test_metadata_only_repair_flags_do_not_reduce_failure_penalty(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.32,
                unseen_trades=24,
                parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
            )
        ],
    )
    memory = build_failure_memory(tmp_path)

    plain = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
    )
    annotated = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={
            "symbol": "QQQ",
            "fast_sma": 50,
            "slow_sma": 150,
            "rsi_entry": 40,
            "rsi_exit": 58,
            "atr_stop": 2.0,
            "risk_overlay_changed": True,
            "walk_forward_repair": True,
            "min_unseen_trades_target": 100,
            "cost_stress_repair": True,
        },
    )

    assert memory.penalty_for_spec(annotated).score == memory.penalty_for_spec(plain).score


def test_metadata_only_repair_flags_do_not_change_execution_fingerprint_or_dedupe():
    plain = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "rsi_entry": 40},
    )
    annotated = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "rsi_entry": 40, "risk_overlay_changed": True, "min_unseen_trades_target": 100},
    )

    assert strategy_execution_fingerprint(annotated) == strategy_execution_fingerprint(plain)
    assert dedupe_strategy_specs([plain, annotated]) == [plain]


def test_ordered_symbols_have_distinct_failure_memory_signature_and_execution_fingerprint():
    first_parameters = {"symbols": ["SPY", "TLT"], "lookback": 126}
    second_parameters = {"symbols": ["TLT", "SPY"], "lookback": 126}
    first = _spec(
        family="ROTATION",
        short_name="QUEUE_MOM_DD",
        builder="active_momentum_rotation",
        parameters=first_parameters,
    )
    second = _spec(
        family="ROTATION",
        short_name="QUEUE_MOM_DD",
        builder="active_momentum_rotation",
        parameters=second_parameters,
    )

    assert execution_parameter_signature(first_parameters) != execution_parameter_signature(second_parameters)
    assert strategy_execution_fingerprint(first) != strategy_execution_fingerprint(second)


def test_symbol_list_reordering_is_treated_as_an_executable_parameter_change(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="ROTATION_ETF_1D_QUEUE_MOM_DD_20260606_009",
                family="ROTATION",
                short_name="QUEUE_MOM_DD",
                builder="active_momentum_rotation",
                unseen_max_drawdown=-0.32,
                unseen_trades=150,
                parameters={"symbols": ["SPY", "TLT"], "lookback": 126},
            )
        ],
    )
    memory = build_failure_memory(tmp_path)
    original = _spec(
        family="ROTATION",
        short_name="QUEUE_MOM_DD",
        builder="active_momentum_rotation",
        parameters={"symbols": ["SPY", "TLT"], "lookback": 126},
    )
    reordered = _spec(
        family="ROTATION",
        short_name="QUEUE_MOM_DD",
        builder="active_momentum_rotation",
        parameters={"symbols": ["TLT", "SPY"], "lookback": 126},
    )

    assert memory.penalty_for_spec(reordered).score < memory.penalty_for_spec(original).score


def test_metadata_only_repair_flags_do_not_bypass_queue_dedupe(tmp_path):
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

    specs = queued_hypothesis_strategies(tmp_path, limit=2)

    assert [spec.parameters["source_hypothesis_id"] for spec in specs] == ["SWING_1"]


def test_actual_executable_parameter_change_reduces_failure_penalty(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="SWING_ETF_1D_QUEUE_PULLBACK_20260606_009",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.32,
                unseen_trades=24,
                parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
            )
        ],
    )
    memory = build_failure_memory(tmp_path)
    repeated = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0},
    )
    changed_threshold = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 35, "rsi_exit": 58, "atr_stop": 2.0},
    )

    assert memory.penalty_for_spec(changed_threshold).score < memory.penalty_for_spec(repeated).score


def test_partial_experiment_rows_do_not_poison_failure_memory(tmp_path):
    _write_experiments(
        tmp_path,
        [
            {
                "strategy_id": "PARTIAL",
                "family": "SWING",
                "short_name": "QUEUE_PULLBACK",
                "builder": "swing_trend_filtered_pullback",
            }
        ],
    )
    memory = build_failure_memory(tmp_path)
    spec = _spec(
        family="SWING",
        short_name="QUEUE_PULLBACK",
        builder="swing_trend_filtered_pullback",
        parameters={"symbol": "QQQ", "rsi_entry": 40},
    )

    assert memory.penalty_for_spec(spec).score == 0


def _write_experiments(root, rows):
    path = root / "registry" / "experiments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_queue(root, rows):
    path = root / "registry" / "hypothesis_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _spec(family, short_name, builder, parameters):
    return StrategySpec(
        family=family,
        asset_class="ETF",
        timeframe="1D",
        short_name=short_name,
        hypothesis="test",
        parameters=parameters,
        rules="test",
        builder=builder,
    )


def _result(
    *,
    strategy_id,
    family,
    short_name,
    builder,
    unseen_max_drawdown,
    unseen_trades,
    parameters=None,
):
    return {
        "strategy_id": strategy_id,
        "family": family,
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": short_name,
        "tier": "Rejected",
        "parameters": parameters or {"symbol": "QQQ"},
        "rules": "test",
        "hypothesis": "test",
        "builder": builder,
        "data_manifest": {"source": "eodhd", "years": 33.3},
        "split_metrics": {
            "train": {"cagr": 0.03},
            "validation": {"cagr": 0.02},
            "unseen": {"cagr": 0.01, "max_drawdown": unseen_max_drawdown, "trade_count": unseen_trades},
        },
        "cost_stress": {"survives_double_cost": True},
        "walk_forward": {
            "method": "true_rolling_oos",
            "status": "ok",
            "window_count": 7,
            "pass_rate": 0.5,
            "median_test_cagr": 0.01,
            "worst_test_drawdown": -0.18,
        },
    }
