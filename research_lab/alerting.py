from __future__ import annotations

import csv
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any


ALERT_COLUMNS = ["severity", "event_type", "title", "details", "source", "delivery_status"]


def build_weekly_alerts(
    deployment_rows: list[dict[str, Any]],
    portfolio_summary: dict[str, Any],
    apify_status: str,
    cost_total_usd: float,
) -> list[dict[str, Any]]:
    alerts = []
    eligible = [row for row in deployment_rows if row.get("paper_eligible") is True or str(row.get("paper_eligible")).lower() == "true"]
    if eligible:
        alerts.append(
            _alert(
                "high",
                "deployment_gate_passed",
                "Strategy passed paper gate",
                f"{len(eligible)} strategy candidate(s) passed the paper deployment gate.",
                "deployment_gate",
            )
        )
    if apify_status.startswith("failed"):
        alerts.append(_alert("medium", "apify_failed", "Apify import failed", apify_status, "apify"))
    drawdown_threshold = float(os.getenv("RESEARCH_ALERT_DRAWDOWN_THRESHOLD", "-0.15"))
    max_drawdown = float(portfolio_summary.get("max_drawdown", 0.0) or 0.0)
    if portfolio_summary.get("status") == "ok" and max_drawdown <= drawdown_threshold:
        alerts.append(
            _alert(
                "medium",
                "portfolio_drawdown_warning",
                "Portfolio drawdown warning",
                f"Combined portfolio max drawdown is {max_drawdown:.2%}.",
                "portfolio_backtest",
            )
        )
    cost_threshold = float(os.getenv("RESEARCH_ALERT_COST_THRESHOLD_USD", "0"))
    if cost_threshold > 0 and cost_total_usd >= cost_threshold:
        alerts.append(
            _alert(
                "low",
                "research_cost_warning",
                "Research cost threshold reached",
                f"Estimated research cost is ${cost_total_usd:.4f}; threshold is ${cost_threshold:.4f}.",
                "cost_monitor",
            )
        )
    return alerts


def write_and_send_alerts(root: Path, report_stem: str, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    report_dir = root / "reports" / "alerts"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f"{report_stem}_alerts.csv"
    md_path = report_dir / f"{report_stem}_alerts.md"
    delivered = []
    for alert in alerts:
        status = _deliver_alert(alert)
        delivered.append({**alert, "delivery_status": status})
    _write_csv(csv_path, delivered)
    _write_markdown(md_path, delivered)
    return {"rows": delivered, "path": csv_path, "report_path": md_path}


def send_test_alert(dry_run: bool = True) -> dict[str, Any]:
    alert = _alert(
        "low",
        "alert_delivery_test",
        "Research alert delivery test",
        "This is a research-lab alert delivery path test.",
        "alerting",
    )
    providers = _configured_providers()
    if dry_run:
        return {"dry_run": True, "providers": providers, "alert": alert, "delivery_status": "dry_run"}
    return {"dry_run": False, "providers": providers, "alert": alert, "delivery_status": _deliver_alert(alert)}


def summarize_alerts(alerts: list[dict[str, Any]]) -> list[str]:
    if not alerts:
        return ["- alerting: no important events"]
    high = sum(1 for alert in alerts if alert["severity"] == "high")
    medium = sum(1 for alert in alerts if alert["severity"] == "medium")
    return [f"- alerting: {len(alerts)} events (high={high}, medium={medium})"]


def _deliver_alert(alert: dict[str, Any]) -> str:
    statuses = []
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        statuses.append(_send_telegram(alert))
    if os.getenv("ALERT_SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        statuses.append(_send_email(alert))
    return ",".join(statuses) if statuses else "not_configured"


def _configured_providers() -> list[str]:
    providers = []
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        providers.append("telegram")
    if os.getenv("ALERT_SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        providers.append("email")
    return providers


def _send_telegram(alert: dict[str, Any]) -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    text = _format_alert(alert)
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as response:
            json.loads(response.read().decode("utf-8"))
        return "telegram_sent"
    except Exception as exc:
        return f"telegram_failed:{exc}"


def _send_email(alert: dict[str, Any]) -> str:
    host = os.getenv("ALERT_SMTP_HOST", "")
    port = int(os.getenv("ALERT_SMTP_PORT", "587"))
    user = os.getenv("ALERT_SMTP_USER", "")
    password = os.getenv("ALERT_SMTP_PASSWORD", "")
    sender = os.getenv("ALERT_EMAIL_FROM", user)
    recipient = os.getenv("ALERT_EMAIL_TO", "")
    message = EmailMessage()
    message["Subject"] = f"[research-lab] {alert['title']}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(_format_alert(alert))
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
        return "email_sent"
    except Exception as exc:
        return f"email_failed:{exc}"


def _alert(severity: str, event_type: str, title: str, details: str, source: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "event_type": event_type,
        "title": title,
        "details": details,
        "source": source,
        "delivery_status": "",
    }


def _format_alert(alert: dict[str, Any]) -> str:
    return f"{alert['severity'].upper()}: {alert['title']}\n{alert['details']}\nsource={alert['source']}"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALERT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in ALERT_COLUMNS})


def _write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["# Research Alerts", ""]
    if not rows:
        lines.append("- no important events")
    for row in rows:
        lines.extend(
            [
                f"## {row['severity'].upper()} - {row['title']}",
                "",
                f"- type: {row['event_type']}",
                f"- source: {row['source']}",
                f"- delivery: {row['delivery_status']}",
                "",
                row["details"],
                "",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
