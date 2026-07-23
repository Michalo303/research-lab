from scripts.run_weekly_deep_research import _weekly_robustness_findings


def test_weekly_runtime_failure_returns_nonzero_and_writes_failure_artifact(tmp_path, monkeypatch):
    import scripts.run_weekly_deep_research as weekly

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(weekly, "_run_weekly", lambda: (_ for _ in ()).throw(RuntimeError("token=top-secret")))

    assert weekly.main() == 1
    payload = (tmp_path / "reports" / "operational" / "weekly-latest-failure.json").read_text(encoding="utf-8")
    assert "top-secret" not in payload
    assert "failure details redacted" in payload


def test_weekly_robustness_findings_include_true_walk_forward_summary_text():
    lines = _weekly_robustness_findings(
        [
            {
                "strategy_id": "alpha",
                "walk_forward_method": "true_rolling_oos",
                "pass_rate": 1.0,
                "median_test_mar": 1.25,
                "regime_summary": "",
                "robustness_verdict": "pass",
                "walk_forward_score": 2.0,
                "unseen_cagr": 0.12,
            },
            {
                "strategy_id": "beta",
                "walk_forward_method": "true_rolling_oos",
                "pass_rate": 0.5,
                "median_test_mar": 0.75,
                "regime_summary": "",
                "robustness_verdict": "borderline",
                "walk_forward_score": 1.0,
                "unseen_cagr": 0.07,
            },
        ],
        [],
    )

    assert "- true walk-forward pass: 1/2 (median pass_rate=0.75, median MAR=1.00)" in lines


def test_weekly_robustness_findings_include_regime_breakdown_from_regime_summary():
    lines = _weekly_robustness_findings(
        [
            {
                "strategy_id": "alpha",
                "walk_forward_method": "true_rolling_oos",
                "pass_rate": 1.0,
                "median_test_mar": 1.25,
                "regime_summary": "bull:2/2;bear:1/1",
                "robustness_verdict": "pass",
                "walk_forward_score": 2.0,
                "unseen_cagr": 0.12,
            },
            {
                "strategy_id": "beta",
                "walk_forward_method": "true_rolling_oos",
                "pass_rate": 1.0,
                "median_test_mar": 0.75,
                "regime_summary": "bull:1/2;bear:1/1",
                "robustness_verdict": "pass",
                "walk_forward_score": 1.0,
                "unseen_cagr": 0.07,
            },
        ],
        [],
    )

    assert "- true walk-forward regime breakdown: bear 2/2; bull 3/4" in lines


def test_weekly_robustness_findings_omit_proxy_wording_for_true_walk_forward_rows():
    lines = _weekly_robustness_findings(
        [
            {
                "strategy_id": "alpha",
                "walk_forward_method": "true_rolling_oos",
                "pass_rate": 1.0,
                "median_test_mar": 1.25,
                "regime_summary": "bull:2/2",
                "robustness_verdict": "pass",
                "walk_forward_score": 2.0,
                "unseen_cagr": 0.12,
            }
        ],
        [],
    )

    assert "proxy" not in "\n".join(lines).lower()
