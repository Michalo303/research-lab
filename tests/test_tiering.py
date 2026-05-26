import pytest

from research_lab.tiering import classify_strategy


def _metrics():
    split = {
        "cagr": 0.12,
        "max_drawdown": -0.05,
        "sharpe": 1.2,
        "mar": 2.0,
        "profit_factor": 1.5,
        "trade_count": 150,
    }
    return {"train": split, "validation": split, "unseen": split}


def test_tiering_does_not_promote_legacy_walk_forward():
    tier, reason = classify_strategy(
        "ROTATION",
        _metrics(),
        {"survives_double_cost": True},
        "massive",
        22.0,
        {
            "method": "rolling_train_then_test",
            "status": "ok",
            "window_count": 12,
            "pass_rate": 1.0,
            "median_test_cagr": 0.12,
            "worst_test_drawdown": -0.05,
        },
    )

    assert tier == "C"
    assert "walk-forward" in reason.lower()


def _passing_walk_forward(**overrides):
    walk_forward = {
        "method": "true_rolling_oos",
        "status": "ok",
        "window_count": 3,
        "pass_rate": 0.67,
        "median_test_cagr": 0.01,
        "worst_test_drawdown": -0.20,
    }
    walk_forward.update(overrides)
    return walk_forward


def test_tiering_preserves_promotion_when_walk_forward_is_omitted():
    tier, reason = classify_strategy(
        "ROTATION",
        _metrics(),
        {"survives_double_cost": True},
        "massive",
        22.0,
    )

    assert tier == "A"
    assert "Tier A" in reason


def test_tiering_allows_promotion_with_true_walk_forward_pass():
    tier, reason = classify_strategy(
        "ROTATION",
        _metrics(),
        {"survives_double_cost": True},
        "massive",
        22.0,
        _passing_walk_forward(),
    )

    assert tier == "A"
    assert "Tier A" in reason


@pytest.mark.parametrize(
    "override",
    [
        {"status": "not_enough_oos_windows"},
        {"window_count": 2},
        {"pass_rate": 0.66},
        {"median_test_cagr": 0.0},
        {"worst_test_drawdown": -0.21},
    ],
)
def test_tiering_rejects_failed_true_walk_forward_thresholds(override):
    tier, reason = classify_strategy(
        "ROTATION",
        _metrics(),
        {"survives_double_cost": True},
        "massive",
        22.0,
        _passing_walk_forward(**override),
    )

    assert tier == "C"
    assert "walk-forward" in reason.lower()
