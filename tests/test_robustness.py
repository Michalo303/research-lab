from research_lab.robustness import build_robustness_rows, build_stability_rows


def _result(strategy_id: str, cagr: float, cost: bool = True, tier: str = "C", short_name: str = "DUAL_MOMENTUM") -> dict:
    split = {
        "cagr": cagr,
        "max_drawdown": -0.05,
        "sharpe": 1.0,
        "mar": 1.0,
        "profit_factor": 1.2,
        "trade_count": 120,
    }
    return {
        "strategy_id": strategy_id,
        "family": "ROTATION",
        "short_name": short_name,
        "tier": tier,
        "data_manifest": {"source": "massive", "years": 5.0},
        "split_metrics": {"train": split, "validation": split, "unseen": split},
        "walk_forward": {
            "window_count": 3,
            "positive_windows": 3 if cagr > 0 else 0,
            "passed_windows": 3 if cagr > 0 else 0,
            "method": "true_rolling_oos",
            "status": "ok",
            "pass_rate": 1.0 if cagr > 0 else 0.0,
            "median_test_cagr": cagr,
            "median_test_mar": 1.5,
            "worst_test_drawdown": -0.05,
            "regime_summary": "bull:2/2;bear:1/1",
            "windows": [
                {"test_cagr": cagr, "test_max_drawdown": -0.05},
                {"test_cagr": cagr, "test_max_drawdown": -0.05},
                {"test_cagr": cagr, "test_max_drawdown": -0.05},
            ],
        },
        "cost_stress": {"survives_double_cost": cost},
    }


def test_build_robustness_rows_flags_rolling_walk_forward_pass():
    rows = build_robustness_rows([_result("A", 0.12)])

    assert rows[0]["positive_windows"] == 3
    assert rows[0]["walk_forward_score"] == 1.0
    assert rows[0]["walk_forward_method"] == "true_rolling_oos"
    assert rows[0]["pass_rate"] == 1.0
    assert rows[0]["median_test_mar"] == 1.5
    assert rows[0]["regime_summary"] == "bull:2/2;bear:1/1"
    assert rows[0]["robustness_verdict"] == "pass"


def test_build_robustness_rows_fails_legacy_walk_forward_method():
    item = _result("A", 0.12)
    item["walk_forward"]["method"] = "rolling_train_then_test"

    rows = build_robustness_rows([item])

    assert rows[0]["walk_forward_method"] == "rolling_train_then_test"
    assert rows[0]["robustness_verdict"] == "fail"


def test_build_robustness_rows_fails_when_cost_stress_fails():
    rows = build_robustness_rows([_result("A", 0.12, cost=False)])

    assert rows[0]["robustness_verdict"] == "fail"


def test_build_stability_rows_scores_repeated_positive_group():
    rows = build_stability_rows([_result("A", 0.10), _result("B", 0.08), _result("C", 0.04)])

    assert rows[0]["run_count"] == 3
    assert rows[0]["positive_unseen_share"] == 1.0
    assert rows[0]["stability_verdict"] == "stable"


def test_stability_sorting_prefers_stable_over_weak():
    rows = build_stability_rows(
        [
            _result("A", -0.10, cost=False, tier="Rejected"),
            _result("B", 0.10, short_name="QUEUE_MOM_DD"),
            _result("C", 0.08, short_name="QUEUE_MOM_DD"),
            _result("D", 0.04, short_name="QUEUE_MOM_DD"),
        ]
    )

    assert rows[0]["stability_verdict"] == "stable"
