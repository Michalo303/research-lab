from research_lab.parameter_sweep import PARAMETER_SWEEP_COLUMNS, _parameter_variants, _row, _select_representatives, _variant_verdict, summarize_parameter_sweep


def test_parameter_variants_keep_base_first_and_bound_count():
    params = {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2}

    variants = _parameter_variants("DUAL_MOMENTUM", params, max_variants=5)

    assert variants[0] == params
    assert len(variants) == 5
    assert all("lookback" in item for item in variants)


def test_variant_verdict_requires_cost_survival():
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, False) == "fail"
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, True, 0.67) == "pass"
    assert _variant_verdict(-0.1, 0.1, 0.1, -0.05, True, 0.67) == "borderline"
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, True, 0.33) == "fail"


def test_summarize_parameter_sweep_reports_best_group():
    rows = [
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "pass", "unseen_cagr": 0.10},
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "fail", "unseen_cagr": -0.02},
    ]

    lines = summarize_parameter_sweep(rows)

    assert any("parameter variants tested: 2" in line for line in lines)
    assert any("ROTATION/DUAL_MOMENTUM" in line for line in lines)


def test_parameter_sweep_columns_include_walk_forward_metrics():
    for column in [
        "wf_window_count",
        "wf_pass_rate",
        "wf_median_test_cagr",
        "wf_worst_test_drawdown",
        "wf_status",
        "final_verdict",
    ]:
        assert column in PARAMETER_SWEEP_COLUMNS


def test_parameter_sweep_selects_eodhd_representatives():
    results = [
        {
            "strategy_id": "EODHD1",
            "family": "ROTATION",
            "short_name": "DUAL_MOMENTUM",
            "data_manifest": {"source": "eodhd"},
            "tier": "C",
            "cost_stress": {"survives_double_cost": True},
            "split_metrics": {"unseen": {"cagr": 0.12, "max_drawdown": -0.08}},
        }
    ]

    selected = _select_representatives(results, max_groups=4)

    assert [item["strategy_id"] for item in selected] == ["EODHD1"]


def test_parameter_row_exposes_walk_forward_metrics():
    class Spec:
        family = "ROTATION"
        short_name = "DUAL_MOMENTUM"

    split_metrics = {
        "train": {"cagr": 0.1},
        "validation": {"cagr": 0.08},
        "unseen": {"cagr": 0.07, "max_drawdown": -0.05},
    }
    walk_forward = {
        "status": "ok",
        "window_count": 4,
        "pass_rate": 0.75,
        "median_test_cagr": 0.04,
        "worst_test_drawdown": -0.08,
    }

    row = _row(Spec(), 1, {"lookback": 126}, split_metrics, {"survives_double_cost": True}, walk_forward, "B", "ok")

    assert row["wf_window_count"] == 4
    assert row["wf_pass_rate"] == 0.75
    assert row["wf_status"] == "ok"
    assert row["final_verdict"] == row["verdict"]
