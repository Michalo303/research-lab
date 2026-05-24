from research_lab.parameter_sweep import _parameter_variants, _variant_verdict, summarize_parameter_sweep


def test_parameter_variants_keep_base_first_and_bound_count():
    params = {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2}

    variants = _parameter_variants("DUAL_MOMENTUM", params, max_variants=5)

    assert variants[0] == params
    assert len(variants) == 5
    assert all("lookback" in item for item in variants)


def test_variant_verdict_requires_cost_survival():
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, False) == "fail"
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, True) == "pass"
    assert _variant_verdict(-0.1, 0.1, 0.1, -0.05, True) == "borderline"


def test_summarize_parameter_sweep_reports_best_group():
    rows = [
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "pass", "unseen_cagr": 0.10},
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "fail", "unseen_cagr": -0.02},
    ]

    lines = summarize_parameter_sweep(rows)

    assert any("parameter variants tested: 2" in line for line in lines)
    assert any("ROTATION/DUAL_MOMENTUM" in line for line in lines)
