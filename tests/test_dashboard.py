from research_lab.dashboard import validate_static_dashboard, write_static_dashboard


def test_static_dashboard_writes_html_from_weekly_csvs(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    registry = tmp_path / "registry"
    registry.mkdir(parents=True)
    (registry / "sentiment_candidates.csv").write_text(
        "ticker,research_rank,combined_sentiment_score,attention_delta_7d,price_return_5d,volume_zscore,price_confirmed_sentiment,narrative_tags,coverage_status,research_only,not_trading_signal\n"
        "IREN,1,0.6,2.0,0.12,1.8,confirmed_momentum,AI infrastructure|GPU cloud|power capacity,partial,true,true\n",
        encoding="utf-8",
    )
    (weekly / "2026-W21_deployment_gate.csv").write_text(
        "strategy_id,paper_eligible,gate_verdict,walk_forward_verdict,parameter_verdict,cost_verdict,reasons\n"
        "S1,False,blocked,fail,missing,pass,rolling_walk_forward_not_passed\n",
        encoding="utf-8",
    )
    (weekly / "2026-W21_robustness.csv").write_text(
        "strategy_id,walk_forward_status,window_count,walk_forward_score,median_test_cagr,robustness_verdict\n"
        "S1,ok,3,0.67,0.1,pass\n",
        encoding="utf-8",
    )
    (weekly / "2026-W21_portfolio_backtest.csv").write_text("status,max_drawdown\nok,-0.05\n", encoding="utf-8")
    (weekly / "2026-W21_portfolio_equity.csv").write_text(
        "date,equity,return\n2026-01-01,1.0,0.0\n2026-01-02,1.1,0.1\n2026-01-03,1.05,-0.045\n",
        encoding="utf-8",
    )
    (weekly / "2026-W21_research_costs.csv").write_text(
        "category,unit,quantity,estimated_cost_usd,notes\n"
        "total,usd,,0.0,configured\n",
        encoding="utf-8",
    )

    result = write_static_dashboard(tmp_path, "2026-W21")

    html = result["path"].read_text(encoding="utf-8")
    assert "Research Lab Dashboard" in html
    assert "Rolling WF pass" in html
    assert "S1" in html
    assert "Sentiment / Attention" in html
    assert "IREN-like candidates" in html
    assert "IREN" in html
    assert 'id="equity-chart"' in html
    assert "Drawdown" in html
    assert validate_static_dashboard(result["path"]) == []


def test_static_dashboard_shows_empty_equity_chart_state(tmp_path):
    result = write_static_dashboard(tmp_path, "2026-W21")

    html = result["path"].read_text(encoding="utf-8")
    assert 'id="equity-chart"' in html
    assert "No portfolio equity curve available." in html
    assert "equity chart" not in validate_static_dashboard(result["path"])
