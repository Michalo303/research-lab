from __future__ import annotations

from pathlib import Path

from research_lab.reports import build_rejection_diagnostics, write_daily_report


def test_rejection_diagnostics_include_weak_validation_and_unseen_returns():
    result = _result(
        strategy_id="WEAK_RETURNS",
        tier="Rejected",
        tier_reason="Negative unseen result.",
        split_metrics={
            "validation": {"cagr": -0.021},
            "unseen": {"cagr": -0.001292},
        },
    )

    assert build_rejection_diagnostics(result) == [
        "validation return below threshold",
        "unseen return below threshold",
    ]


def test_rejection_diagnostics_include_excessive_drawdown():
    result = _result(
        strategy_id="DEEP_DRAWDOWN",
        tier="Rejected",
        tier_reason="Unseen max drawdown exceeds 15%.",
        split_metrics={"unseen": {"max_drawdown": -0.2142}},
    )

    assert build_rejection_diagnostics(result) == ["max drawdown too deep"]


def test_rejection_diagnostics_include_walk_forward_fallback_and_history_limits():
    result = _result(
        strategy_id="TIER_C_SYNTHETIC",
        tier="C",
        tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
        family="LONGTERM",
        data_manifest={
            "source": "synthetic",
            "years": 2.5,
            "fallback_used": True,
            "fallback_reason": "EODHD failed: missing SPY history",
        },
        walk_forward={
            "method": "true_rolling_oos",
            "status": "ok",
            "window_count": 2,
            "pass_rate": 0.5,
            "median_test_cagr": 0.0,
            "worst_test_drawdown": -0.21,
        },
    )

    assert build_rejection_diagnostics(result) == [
        "insufficient walk-forward robustness",
        "missing required provider data",
        "synthetic/fallback data used",
        "insufficient real data history",
        "failed promotion gate",
        "no accepted tier reached",
    ]


def test_accepted_strategy_has_no_rejection_diagnostics():
    result = _result(strategy_id="ACCEPTED", tier="A", tier_reason="Passes Tier A return, drawdown, cost, and trade-quality gates.")

    assert build_rejection_diagnostics(result) == []


def test_rejection_diagnostics_handle_partial_walk_forward_metrics():
    result = _result(
        strategy_id="PARTIAL_WF",
        tier="C",
        tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
        walk_forward={
            "method": "true_rolling_oos",
            "status": "ok",
            "window_count": None,
            "pass_rate": None,
            "median_test_cagr": None,
            "worst_test_drawdown": None,
        },
    )

    assert build_rejection_diagnostics(result) == [
        "insufficient walk-forward robustness",
        "failed promotion gate",
        "no accepted tier reached",
    ]


def test_daily_report_renders_stable_rejection_diagnostics_for_non_accepted_strategies(tmp_path):
    results = [
        _result(
            strategy_id="ACCEPTED",
            tier="A",
            tier_reason="Passes Tier A return, drawdown, cost, and trade-quality gates.",
        ),
        _result(
            strategy_id="WEAK_RETURNS",
            tier="Rejected",
            tier_reason="Negative unseen result.",
            split_metrics={
                "validation": {"cagr": -0.021},
                "unseen": {"cagr": -0.001292},
            },
        ),
        _result(
            strategy_id="TIER_C_WF",
            tier="C",
            tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
            walk_forward={
                "method": "true_rolling_oos",
                "status": "ok",
                "window_count": 2,
                "pass_rate": 0.5,
                "median_test_cagr": 0.0,
                "worst_test_drawdown": -0.21,
            },
        ),
    ]
    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.md"

    write_daily_report(first_path, results)
    write_daily_report(second_path, results)

    first_section = _section(first_path, "## Rejection Diagnostics", "### Rejection Drawdown Attribution")
    second_section = _section(second_path, "## Rejection Diagnostics", "### Rejection Drawdown Attribution")
    assert first_section == second_section
    assert "| strategy_id | tier | tier_reason | rejection_reasons | failed_metric | actual_value | required_threshold |" in first_section
    assert (
        "| WEAK_RETURNS | Rejected | Negative unseen result. | "
        "validation return below threshold; unseen return below threshold | validation_cagr | -2.10% | > 0.00% |"
    ) in first_section
    assert (
        "| TIER_C_WF | C | Positive unseen result, but rolling walk-forward is not strong enough for promotion. | "
        "insufficient walk-forward robustness; failed promotion gate; no accepted tier reached | "
        "walk_forward_pass_rate | 50.00% | >= 67.00% |"
    ) in first_section
    assert "ACCEPTED" not in first_section


def _section(path: Path, start: str, end: str) -> str:
    text = path.read_text(encoding="utf-8")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def _result(
    *,
    strategy_id: str,
    tier: str,
    tier_reason: str,
    family: str = "ROTATION",
    split_metrics: dict | None = None,
    data_manifest: dict | None = None,
    cost_stress: dict | None = None,
    walk_forward: dict | None = None,
) -> dict:
    metrics = {
        "train": {
            "cagr": 0.12,
            "sharpe": 1.2,
            "mar": 1.0,
            "max_drawdown": -0.05,
            "profit_factor": 1.5,
            "trade_count": 150,
        },
        "validation": {
            "cagr": 0.08,
            "sharpe": 1.0,
            "mar": 0.8,
            "max_drawdown": -0.06,
            "profit_factor": 1.4,
            "trade_count": 150,
        },
        "unseen": {
            "cagr": 0.07,
            "sharpe": 0.9,
            "mar": 0.7,
            "max_drawdown": -0.08,
            "profit_factor": 1.3,
            "trade_count": 150,
        },
    }
    for split_name, overrides in (split_metrics or {}).items():
        metrics[split_name].update(overrides)

    manifest = {
        "source": "eodhd",
        "start": "1993-01-29",
        "end": "2026-05-29",
        "rows": 8390,
        "years": 33.3,
    }
    manifest.update(data_manifest or {})

    return {
        "strategy_id": strategy_id,
        "family": family,
        "asset_class": "ETF",
        "timeframe": "1D",
        "data_manifest": manifest,
        "split_metrics": metrics,
        "cost_stress": {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": True,
            "double_unseen_cagr": 0.05,
            **(cost_stress or {}),
        },
        "walk_forward": walk_forward,
        "tier": tier,
        "tier_reason": tier_reason,
    }
