from research_lab.alerting import build_weekly_alerts, send_test_alert, summarize_alerts, write_and_send_alerts


def test_build_weekly_alerts_detects_paper_gate_and_drawdown(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_ALERT_DRAWDOWN_THRESHOLD", "-0.10")
    alerts = build_weekly_alerts(
        deployment_rows=[{"paper_eligible": True}],
        portfolio_summary={"status": "ok", "max_drawdown": -0.12},
        apify_status="failed: timeout",
        cost_total_usd=0.0,
    )

    assert {alert["event_type"] for alert in alerts} == {
        "deployment_gate_passed",
        "portfolio_drawdown_warning",
        "apify_failed",
    }
    assert any("3 events" in line for line in summarize_alerts(alerts))


def test_write_and_send_alerts_defaults_to_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = write_and_send_alerts(
        tmp_path,
        "2026-W21",
        [{"severity": "high", "event_type": "x", "title": "X", "details": "detail", "source": "test"}],
    )

    assert result["path"].exists()
    assert result["report_path"].exists()
    assert result["rows"][0]["delivery_status"] == "not_configured"


def test_build_weekly_alerts_detects_cost_threshold(monkeypatch):
    monkeypatch.setenv("RESEARCH_ALERT_COST_THRESHOLD_USD", "1")

    alerts = build_weekly_alerts([], {}, "skipped", cost_total_usd=1.25)

    assert any(alert["event_type"] == "research_cost_warning" for alert in alerts)


def test_send_test_alert_dry_run_does_not_deliver(monkeypatch):
    called = {"telegram": False, "email": False}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr("research_lab.alerting._send_telegram", lambda alert: called.__setitem__("telegram", True))
    monkeypatch.setattr("research_lab.alerting._send_email", lambda alert: called.__setitem__("email", True))

    result = send_test_alert(dry_run=True)

    assert result["delivery_status"] == "dry_run"
    assert result["providers"] == ["telegram"]
    assert called == {"telegram": False, "email": False}


def test_send_test_alert_uses_delivery_path_when_requested(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.delenv("ALERT_SMTP_HOST", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    monkeypatch.setattr("research_lab.alerting._send_telegram", lambda alert: "telegram_sent")

    result = send_test_alert(dry_run=False)

    assert result["delivery_status"] == "telegram_sent"
    assert result["alert"]["event_type"] == "alert_delivery_test"
