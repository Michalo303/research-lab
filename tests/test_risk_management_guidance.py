from research_lab.llm.hypothesis_adapter import build_hermes_prompt, ingest_llm_hypotheses
from research_lab.risk_management import apply_risk_guidance, has_strong_rotation_risk_overlay
from research_lab.strategies.baselines import queued_hypothesis_strategies
from research_lab.strategy_templates import generate_strategy_candidates

from tests.test_candidate_generation_guidance import _result, _write_experiments, _write_queue


RISK_CONTROL_KEYS = {
    "volatility_targeting",
    "drawdown_circuit_breakers",
    "cash_defensive_regimes",
    "exposure_caps",
    "correlation_aware_portfolio_risk",
    "crisis_period_diagnostics",
    "cost_slippage_stress",
    "parameter_neighborhood_stability",
}


def test_generated_strategy_catalog_candidates_include_survival_first_risk_guidance():
    candidate = generate_strategy_candidates(
        [
            {
                "template_id": "risk_first_rotation",
                "family": "ROTATION",
                "asset_class": "ETF",
                "timeframe": "1D",
                "title": "Risk-first rotation",
                "hypothesis": "Cross-asset rotation should only be expanded with explicit risk controls.",
                "rules": "Rank liquid ETFs by momentum.",
                "builder": "active_momentum_rotation",
                "parameter_grid": {"symbols": [["SPY", "QQQ", "TLT", "GLD"]], "lookback": [126], "top_n": [2]},
            }
        ],
        limit=1,
    )[0]

    assert candidate["risk_management_priority"] == "survival_first"
    assert set(candidate["risk_controls"]) == RISK_CONTROL_KEYS
    assert candidate["optimization_objectives"] == [
        "survival",
        "drawdown_containment",
        "walk_forward_robustness",
        "portfolio_level_risk",
    ]
    assert candidate["deprioritize_when"]["high_cagr_unstable_drawdown"] is True
    assert candidate["promotion_blocks"]["synthetic_or_fallback_data"] is True


def test_hermes_prompt_requires_risk_controls_without_relaxing_gates(tmp_path):
    prompt = build_hermes_prompt(tmp_path)

    assert "Risk management is a first-class research objective" in prompt
    assert "Do not weaken existing gates" in prompt
    assert "Do not relax max drawdown thresholds" in prompt
    for key in RISK_CONTROL_KEYS:
        assert key in prompt
    assert "Synthetic/fallback-data candidates remain blocked from promotion" in prompt
    assert '"risk_controls":' in prompt


def test_ingested_llm_hypotheses_receive_machine_readable_risk_guidance(tmp_path):
    payload = (
        '{"title": "Vol capped trend", "family": "LONGTERM", "rationale": "risk first", '
        '"tags": ["trend"], "source_url": "https://example.test"}'
    )

    [item] = ingest_llm_hypotheses(tmp_path, payload)

    assert item["risk_management_priority"] == "survival_first"
    assert set(item["risk_controls"]) == RISK_CONTROL_KEYS
    assert item["promotion_blocks"]["synthetic_or_fallback_data"] is True
    assert item["deprioritize_when"]["high_cagr_unstable_drawdown"] is True


def test_default_advisory_risk_guidance_does_not_count_as_strong_rotation_overlay():
    item = apply_risk_guidance({"family": "ROTATION", "title": "Generic rotation"})

    assert item["risk_controls"]
    assert item["explicit_risk_controls"] == {}
    assert has_strong_rotation_risk_overlay(item) is False


def test_extreme_drawdown_rotation_hypotheses_wait_for_stronger_risk_overlay(tmp_path):
    _write_experiments(
        tmp_path,
        [
            _result(
                strategy_id="ROTATION_ETF_1D_DUAL_MOMENTUM_20260607_001",
                family="ROTATION",
                short_name="DUAL_MOMENTUM",
                builder="active_momentum_rotation",
                unseen_cagr=0.31,
                unseen_max_drawdown=-0.58,
            )
        ],
    )
    _write_queue(
        tmp_path,
        [
            {
                "hypothesis_id": "ROTATION_GENERIC",
                "family": "ROTATION",
                "title": "Return-chasing rotation",
                "source_title": "source",
                "risk_controls": {"exposure_caps": "none"},
            },
            {
                "hypothesis_id": "ROTATION_RISK_OVERLAY",
                "family": "ROTATION",
                "title": "Risk-overlay rotation",
                "source_title": "source",
                "risk_controls": {
                    "volatility_targeting": "portfolio sleeve target",
                    "drawdown_circuit_breakers": "de-risk after benchmark drawdown",
                    "cash_defensive_regimes": "cash or defensive assets in risk-off periods",
                    "exposure_caps": "cap gross and single-asset exposure",
                    "correlation_aware_portfolio_risk": "do not stack correlated risk assets",
                },
            },
        ],
    )

    specs = queued_hypothesis_strategies(tmp_path, limit=4)

    assert [spec.parameters["source_hypothesis_id"] for spec in specs] == ["ROTATION_RISK_OVERLAY"]
