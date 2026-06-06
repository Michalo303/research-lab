import json

from research_lab.research_orchestrator import (
    build_research_guidance,
    classify_rejection_reason,
    detect_duplicate_candidate_patterns,
    score_candidate_direction,
    summarize_recent_failures,
)
from research_lab.strategies.baselines import StrategySpec


def test_classify_rejection_reason_maps_supported_failure_categories():
    assert classify_rejection_reason("max drawdown too deep") == "risk/drawdown"
    assert classify_rejection_reason("failed cost stress") == "cost stress failure"
    assert classify_rejection_reason("insufficient walk-forward robustness") == "walk-forward robustness"
    assert classify_rejection_reason("too few unseen trades") == "too few trades"
    assert classify_rejection_reason("synthetic/fallback data used") == "synthetic/fallback data"
    assert classify_rejection_reason("semantic duplicate hypothesis") == "duplicate or near-duplicate hypothesis"
    assert classify_rejection_reason("failed promotion gate") == "promotion gate failure"


def test_build_research_guidance_prioritizes_risk_overlay_when_drawdown_dominates():
    results = [
        _result(
            "SWING_ETF_1D_QUEUE_PULLBACK_20260606_001",
            family="SWING",
            short_name="QUEUE_PULLBACK",
            builder="swing_trend_filtered_pullback",
            unseen_max_drawdown=-0.34,
            unseen_trades=31,
        ),
        _result(
            "SWING_ETF_1D_QUEUE_PULLBACK_20260606_002",
            family="SWING",
            short_name="QUEUE_PULLBACK",
            builder="swing_trend_filtered_pullback",
            unseen_max_drawdown=-0.29,
            unseen_trades=44,
        ),
        _result(
            "ROTATION_ETF_1D_DUP_20260606_003",
            family="ROTATION",
            short_name="DUP",
            builder="active_momentum_rotation",
            unseen_max_drawdown=-0.12,
            extra={"duplicate_reasons": ["semantic duplicate hypothesis"]},
        ),
    ]

    memory = summarize_recent_failures(results)
    guidance = build_research_guidance(memory)

    assert guidance.dominant_blocker_category == "risk/drawdown"
    assert guidance.blocker_mix["risk/drawdown"] == 2
    assert guidance.blocker_mix["too few trades"] == 2
    assert guidance.prioritized_next_directions[0].name == "risk_overlay_repair"
    assert "family_short:SWING:QUEUE_PULLBACK" in [penalty.pattern_key for penalty in guidance.deprioritized_candidate_types]
    assert guidance.to_dict()["dominant_blocker_category"] == "risk/drawdown"


def test_guidance_marks_synthetic_fallback_as_data_quality_limited():
    result = _result(
        "LONGTERM_ETF_1D_SYNTH_20260606_001",
        family="LONGTERM",
        short_name="SYNTH",
        builder="long_term_trend_filter",
        data_manifest={"source": "synthetic", "years": 2.0, "fallback_used": True, "fallback_reason": "EODHD failed"},
    )

    guidance = build_research_guidance(summarize_recent_failures([result]))

    assert guidance.data_quality_limited is True
    assert guidance.promotion_blocked is True
    assert guidance.data_quality_limitations == ("synthetic/fallback data present; do not promote affected candidates",)


def test_score_candidate_direction_penalizes_unrepaired_repeated_patterns_more_than_repairs():
    memory = summarize_recent_failures(
        [
            _result(
                "SWING_ETF_1D_QUEUE_PULLBACK_20260606_001",
                family="SWING",
                short_name="QUEUE_PULLBACK",
                builder="swing_trend_filtered_pullback",
                unseen_max_drawdown=-0.31,
                unseen_trades=28,
            )
        ]
    )
    guidance = build_research_guidance(memory)

    repeated = _spec("QUEUE_PULLBACK", {"symbol": "QQQ"}, "swing_trend_filtered_pullback")
    repaired = _spec(
        "QUEUE_PULLBACK_RISK_REPAIR",
        {"symbol": "QQQ", "risk_overlay_changed": True, "min_unseen_trades_target": 120},
        "swing_trend_filtered_pullback",
    )

    assert score_candidate_direction(repeated, guidance) > score_candidate_direction(repaired, guidance)


def test_detect_duplicate_candidate_patterns_counts_recent_semantic_repeats():
    first = {
        "family": "SWING",
        "title": "QQQ pullback",
        "ticker": "QQQ",
        "parameters": {"rsi_entry": 40},
    }
    second = {
        "family": "SWING",
        "title": "QQQ pullback",
        "ticker": "QQQ",
        "parameters": {"rsi_entry": 40},
    }

    patterns = detect_duplicate_candidate_patterns([first, second])

    assert patterns[0]["count"] == 2
    assert patterns[0]["duplicate_count"] == 1


def test_summarize_recent_failures_loads_registry_artifacts(tmp_path):
    path = tmp_path / "registry" / "experiments.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_result("RISKY", unseen_max_drawdown=-0.22)) + "\n", encoding="utf-8")

    memory = summarize_recent_failures(tmp_path)

    assert memory.blocker_counts["risk/drawdown"] == 1


def _spec(short_name: str, parameters: dict, builder: str) -> StrategySpec:
    return StrategySpec(
        family="SWING",
        asset_class="ETF",
        timeframe="1D",
        short_name=short_name,
        hypothesis="test",
        parameters=parameters,
        rules="test",
        builder=builder,
    )


def _result(
    strategy_id: str,
    *,
    family: str = "ROTATION",
    short_name: str = "MOM",
    builder: str = "active_momentum_rotation",
    unseen_cagr: float = 0.02,
    validation_cagr: float = 0.03,
    unseen_max_drawdown: float = -0.10,
    unseen_trades: int = 150,
    cost_survives: bool = True,
    wf_pass_rate: float = 0.8,
    data_manifest: dict | None = None,
    extra: dict | None = None,
) -> dict:
    manifest = {"source": "eodhd", "years": 33.3}
    manifest.update(data_manifest or {})
    result = {
        "strategy_id": strategy_id,
        "family": family,
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": short_name,
        "builder": builder,
        "tier": "Rejected",
        "tier_reason": "Rejected by gates.",
        "data_manifest": manifest,
        "split_metrics": {
            "train": {"cagr": 0.03},
            "validation": {"cagr": validation_cagr},
            "unseen": {"cagr": unseen_cagr, "max_drawdown": unseen_max_drawdown, "trade_count": unseen_trades},
        },
        "cost_stress": {"survives_double_cost": cost_survives, "double_unseen_cagr": -0.01},
        "walk_forward": {
            "method": "true_rolling_oos",
            "status": "ok",
            "window_count": 7,
            "pass_rate": wf_pass_rate,
            "median_test_cagr": 0.01,
            "worst_test_drawdown": -0.18,
        },
    }
    result.update(extra or {})
    return result
