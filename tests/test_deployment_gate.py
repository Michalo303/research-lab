import pytest

from research_lab.deployment_gate import PaperGateConfig, _gate_row


def _item(walk_forward=None):
    payload = {
        "strategy_id": "S1",
        "family": "ROTATION",
        "short_name": "DUAL_MOMENTUM",
        "tier": "B",
        "data_manifest": {"source": "massive", "years": 22.0},
        "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.05, "trade_count": 150}
        },
    }
    if walk_forward is not None:
        payload["walk_forward"] = walk_forward
    return payload


def _passing_walk_forward(**overrides):
    wf = {
        "method": "true_rolling_oos",
        "status": "ok",
        "window_count": 3,
        "pass_rate": 0.67,
        "median_test_cagr": 0.01,
        "worst_test_drawdown": -0.20,
    }
    wf.update(overrides)
    return wf


def _row(walk_forward=None, robustness=None, parameter_by_group=None, config=None):
    return _gate_row(
        _item(walk_forward),
        robustness if robustness is not None else {"robustness_verdict": "pass"},
        (
            parameter_by_group
            if parameter_by_group is not None
            else {("ROTATION", "DUAL_MOMENTUM"): "pass"}
        ),
        {"portfolio_score": 1.0, "suggested_weight_pct": 5.0},
        config or PaperGateConfig(),
    )


def test_paper_gate_config_reads_min_walk_forward_windows(monkeypatch):
    monkeypatch.setenv("PAPER_GATE_MIN_WALK_FORWARD_WINDOWS", "4")

    assert PaperGateConfig.from_env().min_walk_forward_windows == 4


def test_deployment_gate_rejects_legacy_walk_forward_method_even_with_good_metrics():
    row = _row(_passing_walk_forward(method="legacy_rebalance"))

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_rejects_missing_walk_forward_data():
    row = _row()

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_rejects_insufficient_pass_rate():
    row = _row(_passing_walk_forward(pass_rate=0.66))

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_rejects_missing_pass_rate():
    walk_forward = _passing_walk_forward()
    walk_forward.pop("pass_rate")

    row = _row(walk_forward)

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_rejects_insufficient_window_count():
    row = _row(_passing_walk_forward(window_count=2))

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_rejects_failed_walk_forward_window():
    row = _row(_passing_walk_forward(windows=[{"passed": False}]))

    assert row["paper_eligible"] is False
    assert row["walk_forward_verdict"] == "fail"
    assert "rolling_walk_forward_not_passed" in row["reasons"]


def test_deployment_gate_accepts_valid_true_walk_forward():
    row = _row(_passing_walk_forward())

    assert row["paper_eligible"] is True
    assert row["gate_verdict"] == "pass"
    assert row["walk_forward_verdict"] == "pass"
    assert row["drawdown_verdict"] == "pass"
    assert row["minimum_walk_forward_windows"] == 3
    assert row["reasons"] == []


def test_deployment_gate_rejects_missing_parameter_verdict():
    row = _row(_passing_walk_forward(), parameter_by_group={})

    assert row["paper_eligible"] is False
    assert row["gate_verdict"] == "fail"
    assert "parameter_verdict_not_passed" in row["reasons"]


@pytest.mark.parametrize(
    ("metric_group", "metric_name"),
    [
        ("walk_forward", "window_count"),
        ("walk_forward", "pass_rate"),
        ("walk_forward", "median_test_cagr"),
        ("walk_forward", "worst_test_drawdown"),
        ("gate", "max_drawdown"),
    ],
)
@pytest.mark.parametrize("metric_value", [float("inf"), float("-inf"), float("nan")])
def test_deployment_gate_rejects_non_finite_numeric_metrics(
    metric_group, metric_name, metric_value
):
    walk_forward = _passing_walk_forward()
    item = _item(walk_forward)
    if metric_group == "walk_forward":
        walk_forward[metric_name] = metric_value
    else:
        item["split_metrics"]["unseen"][metric_name] = metric_value

    row = _gate_row(
        item,
        {"robustness_verdict": "pass"},
        {("ROTATION", "DUAL_MOMENTUM"): "pass"},
        {"portfolio_score": 1.0, "suggested_weight_pct": 5.0},
        PaperGateConfig(),
    )

    assert row["paper_eligible"] is False
    if metric_group == "walk_forward":
        assert row["walk_forward_verdict"] == "fail"
        assert "rolling_walk_forward_not_passed" in row["reasons"]
    else:
        assert row["drawdown_verdict"] == "fail"
        assert "drawdown_below_threshold" in row["reasons"]
