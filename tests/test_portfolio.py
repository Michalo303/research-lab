from research_lab.portfolio import _portfolio_score, run_portfolio_scoring, summarize_portfolio_scoring


def test_portfolio_score_requires_cost_survival():
    assert _portfolio_score(0.2, -0.1, False, "plausible", 0.0) == 0.0
    assert _portfolio_score(0.2, -0.1, True, "plausible", 0.0) > 0.0


def test_portfolio_summary_handles_empty_rows():
    assert summarize_portfolio_scoring([]) == ["- portfolio scoring: no eligible candidates"]


def test_portfolio_scoring_writes_candidates(tmp_path):
    run_dir = tmp_path / "backtests" / "runs" / "S1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        """
        {
          "strategy_id": "S1",
          "family": "ROTATION",
          "asset_class": "ETF",
          "short_name": "DUAL_MOMENTUM",
          "tier": "C",
          "hypothesis": "Momentum rotation edge",
          "rules": "Rank by momentum",
          "data_manifest": {"source": "massive"},
          "cost_stress": {"survives_double_cost": true},
          "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.1}
          }
        }
        """,
        encoding="utf-8",
    )

    result = run_portfolio_scoring(tmp_path, "2026-W21")

    assert result["path"].exists()
    assert result["rows"][0]["strategy_id"] == "S1"
    assert result["rows"][0]["suggested_weight_pct"] > 0
