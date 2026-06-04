from datetime import datetime, timezone

from research_lab.weekly_validation_gate import (
    evaluate_weekly_validation_gate,
    render_weekly_validation_gate_markdown,
)


FIXED_TIME = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def _strong_metrics(**overrides):
    metrics = {
        "experiments_run": 12,
        "accepted_count": 3,
        "rejected_count": 2,
        "best_validation_return": 0.18,
        "best_unseen_return": 0.12,
        "best_max_drawdown": -0.12,
        "walk_forward_pass_rate": 0.75,
        "robustness_pass_rate": 0.70,
        "data_years": 12.0,
        "data_source": "massive",
        "synthetic_used": False,
        "missing_required_metrics": [],
    }
    metrics.update(overrides)
    return metrics


def test_weekly_validation_gate_fails_on_no_experiments():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(experiments_run=0),
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "FAIL"
    assert result.tier == "REJECTED"
    assert "no_experiments_run" in result.reasons
    assert result.evaluated_at == "2026-06-04T12:00:00+00:00"


def test_weekly_validation_gate_fails_on_synthetic_only_data():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(data_source="synthetic", synthetic_used=True),
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "FAIL"
    assert result.tier == "REJECTED"
    assert "synthetic_data_only" in result.reasons


def test_weekly_validation_gate_warns_on_five_year_real_data_history():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(data_years=5.0),
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "WARNING"
    assert result.tier == "WATCHLIST"
    assert "limited_real_data_history" in result.reasons


def test_weekly_validation_gate_warns_when_no_accepted_but_positive_unseen():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(accepted_count=0, best_unseen_return=0.04),
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "WARNING"
    assert result.tier == "WATCHLIST"
    assert "positive_unseen_without_accepted_strategy" in result.reasons


def test_weekly_validation_gate_passes_on_strong_real_data_metrics():
    result = evaluate_weekly_validation_gate(_strong_metrics(), evaluated_at=FIXED_TIME)

    assert result.status == "PASS"
    assert result.tier == "DEPLOYABLE"
    assert result.reasons == ["all_deployable_thresholds_met"]


def test_weekly_validation_gate_missing_required_metrics_fail_deterministically():
    result = evaluate_weekly_validation_gate(
        {
            "experiments_run": 4,
            "accepted_count": 1,
            "best_unseen_return": 0.03,
            "data_source": "massive",
            "synthetic_used": False,
            "missing_required_metrics": ["data_years", "best_max_drawdown"],
        },
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "FAIL"
    assert result.tier == "REJECTED"
    assert "missing_required_metrics:data_years,best_max_drawdown" in result.reasons
    assert result.metrics["missing_required_metrics"] == ["data_years", "best_max_drawdown"]


def test_weekly_validation_gate_incomplete_wf_and_robustness_warn_deterministically():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(
            walk_forward_pass_rate=None,
            robustness_pass_rate=None,
        ),
        evaluated_at=FIXED_TIME,
    )

    assert result.status == "WARNING"
    assert result.tier == "WATCHLIST"
    assert "walk_forward_metrics_incomplete" in result.reasons
    assert "robustness_metrics_incomplete" in result.reasons


def test_weekly_validation_gate_markdown_includes_status_tier_reasons_and_metrics():
    result = evaluate_weekly_validation_gate(
        _strong_metrics(data_years=7.0),
        evaluated_at=FIXED_TIME,
    )

    markdown = render_weekly_validation_gate_markdown(result)

    assert "## Weekly Validation Gate" in markdown
    assert "- status: WARNING" in markdown
    assert "- tier: WATCHLIST" in markdown
    assert "limited_real_data_history" in markdown
    assert "data_years: 7.0" in markdown
    assert "best_unseen_return: 0.12" in markdown
