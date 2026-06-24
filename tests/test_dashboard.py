from datetime import datetime, timedelta, timezone
import os

from research_lab.dashboard import _missing_artifact_warning, _provider_summary, build_dashboard_snapshot, validate_static_dashboard, write_static_dashboard


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


def test_provider_summary_reports_eodhd_as_real_eod():
    summary = _provider_summary([{"source": "eodhd"}])

    assert "EODHD" in summary["text"]
    assert "real EOD" in summary["text"]
    assert "no capital relevance" not in summary["warning"]


def test_provider_summary_reports_eodhd_with_synthetic_auxiliary_data():
    summary = _provider_summary([{"source": "eodhd"}, {"source": "synthetic"}])

    assert "EODHD" in summary["text"]
    assert "real EOD" in summary["text"]
    assert "synthetic" in summary["warning"]
    assert "no capital relevance" not in summary["warning"]


def test_provider_summary_reports_synthetic_only_as_non_capital_relevant():
    summary = _provider_summary([{"source": "synthetic"}])

    assert "synthetic" in summary["text"]
    assert "no capital relevance" in summary["warning"]


def test_provider_summary_reports_massive_with_synthetic_auxiliary_data():
    summary = _provider_summary([{"source": "massive"}, {"source": "synthetic"}])

    assert "Massive" in summary["text"]
    assert "real EOD" in summary["text"]
    assert "synthetic" in summary["warning"]
    assert "no capital relevance" not in summary["warning"]


def test_provider_summary_reports_real_eod_with_unknown_sources():
    summary = _provider_summary([{"source": "unknown"}, {"source": "eodhd"}])

    assert "EODHD" in summary["text"]
    assert "real EOD" in summary["text"]
    assert "unknown" in summary["warning"]
    assert "no capital relevance" not in summary["warning"]


def test_dashboard_snapshot_marks_missing_daily_and_weekly_reports(tmp_path):
    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["files"]["daily_report"]["status"] == "missing"
    assert snapshot["files"]["weekly_report"]["status"] == "missing"


def test_dashboard_snapshot_marks_recent_daily_and_weekly_reports_available(tmp_path):
    daily = tmp_path / "reports" / "daily"
    weekly = tmp_path / "reports" / "weekly"
    daily.mkdir(parents=True)
    weekly.mkdir(parents=True)
    daily_path = daily / "2026-06-24.md"
    weekly_path = weekly / "2026-W26.md"
    daily_path.write_text("# daily\n", encoding="utf-8")
    weekly_path.write_text("# weekly\n", encoding="utf-8")
    now = datetime.now(timezone.utc).timestamp()
    os.utime(daily_path, (now, now))
    os.utime(weekly_path, (now, now))

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["files"]["daily_report"]["status"] == "available"
    assert snapshot["files"]["weekly_report"]["status"] == "available"
    assert snapshot["files"]["daily_report"]["stale_reason"] == ""
    assert snapshot["files"]["weekly_report"]["stale_reason"] == ""


def test_dashboard_snapshot_marks_old_daily_report_stale_with_reason_and_age(tmp_path):
    daily = tmp_path / "reports" / "daily"
    daily.mkdir(parents=True)
    daily_path = daily / "2026-06-05.md"
    daily_path.write_text("# daily\n", encoding="utf-8")
    stale_time = (datetime.now(timezone.utc) - timedelta(days=19)).timestamp()
    os.utime(daily_path, (stale_time, stale_time))

    snapshot = build_dashboard_snapshot(tmp_path)
    daily_meta = snapshot["files"]["daily_report"]

    assert daily_meta["status"] == "stale"
    assert "latest daily report is stale" in daily_meta["stale_reason"]
    assert "19 days old" in daily_meta["age"]


def test_dashboard_snapshot_marks_old_weekly_report_stale_with_reason_and_age(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    weekly_path = weekly / "2026-W21.md"
    weekly_path.write_text("# weekly\n", encoding="utf-8")
    stale_time = (datetime.now(timezone.utc) - timedelta(days=29)).timestamp()
    os.utime(weekly_path, (stale_time, stale_time))

    snapshot = build_dashboard_snapshot(tmp_path)
    weekly_meta = snapshot["files"]["weekly_report"]

    assert weekly_meta["status"] == "stale"
    assert "latest weekly report is stale" in weekly_meta["stale_reason"]
    assert "29 days old" in weekly_meta["age"]


def test_missing_artifact_warning_does_not_treat_stale_as_missing():
    snapshot = {
        "artifacts": [
            {"label": "latest daily report", "status": "stale"},
            {"label": "latest weekly report", "status": "stale"},
            {"label": "paper ledger", "status": "missing"},
        ]
    }

    assert _missing_artifact_warning(snapshot) == "paper ledger"
