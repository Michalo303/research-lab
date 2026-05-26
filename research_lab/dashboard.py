from __future__ import annotations

import csv
import html
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


READ_ONLY_LABEL = "READ ONLY MODE"


def build_dashboard_snapshot(root: Path, report_stem: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    report_stem = report_stem or _detect_report_stem(root)
    redactions = _load_redactions(root)

    weekly_dir = root / "reports" / "weekly"
    daily_dir = root / "reports" / "daily"
    alerts_dir = root / "reports" / "alerts"
    paper_dir = root / "reports" / "paper"
    execution_dir = root / "reports" / "execution"
    self_improvement_dir = root / "reports" / "self_improvement"
    registry_dir = root / "registry"
    manifests_dir = root / "data" / "manifests"

    gate_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_deployment_gate.csv"))
    robustness_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_robustness.csv"))
    stability_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_stability.csv"))
    candidate_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_portfolio_candidates.csv"))
    backtest_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_portfolio_backtest.csv"))
    equity_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_portfolio_equity.csv"))
    costs_rows = _read_csv(_resolve_report_path(weekly_dir, report_stem, "_research_costs.csv"))
    sentiment_candidate_path = _resolve_report_path(weekly_dir, report_stem, "_sentiment_candidates.csv") or (registry_dir / "sentiment_candidates.csv")
    sentiment_summary_path = _resolve_report_path(weekly_dir, report_stem, "_narrative_summary.md")
    alerts_rows = _read_csv(_resolve_report_path(alerts_dir, report_stem, "_alerts.csv"))
    alerts_md = _safe_read_text(_resolve_report_path(alerts_dir, report_stem, "_alerts.md"), redactions)
    daily_md_path = _latest_file(daily_dir, "*.md")
    weekly_md_path = _latest_file(weekly_dir, "*.md")
    self_improvement_md_path = _latest_file(self_improvement_dir, "*.md")
    paper_ledger_path = _latest_file(paper_dir, "*_paper_ledger.csv")
    paper_positions_path = _latest_file(paper_dir, "*_paper_positions.csv")
    reconciliation_csv_path = _latest_file(execution_dir, "*reconciliation*.csv")
    data_quality_path = registry_dir / "data_quality_audit.csv"
    edge_audit_path = registry_dir / "edge_audit.csv"
    leaderboard_path = registry_dir / "leaderboard.csv"
    hypothesis_queue_path = registry_dir / "hypothesis_queue.jsonl"
    hypothesis_results_path = registry_dir / "hypothesis_results.jsonl"
    creative_ideas_path = registry_dir / "creative_ideas.jsonl"
    sentiment_snapshot_path = registry_dir / "sentiment_snapshot.csv"
    ibkr_snapshot_path = execution_dir / "ibkr_paper_read_only_snapshot.json"

    manifests = [_sanitize(_read_json(path), redactions) | {"path": _relative_path(root, path)} for path in sorted(manifests_dir.glob("*.json"))]
    daily_manifest = _load_latest_manifest(manifests, "daily_universe")
    intraday_manifest = _load_latest_manifest(manifests, "intraday_BTCUSDT")
    provider_summary = _provider_summary(manifests)
    systemd_summary = _systemd_summary()
    ibkr_snapshot = _sanitize(_read_json(ibkr_snapshot_path), redactions)

    paper_ledger_rows = _read_csv(paper_ledger_path)
    paper_positions_rows = _read_csv(paper_positions_path)
    reconciliation_rows = _read_csv(reconciliation_csv_path)
    leaderboard_rows = _read_csv(leaderboard_path)
    edge_audit_rows = _read_csv(edge_audit_path)
    data_quality_rows = _read_csv(data_quality_path)
    hypothesis_queue_rows = _read_jsonl(hypothesis_queue_path, redactions)
    hypothesis_results_rows = _read_jsonl(hypothesis_results_path, redactions)
    creative_ideas_rows = _read_jsonl(creative_ideas_path, redactions)
    sentiment_candidate_rows = _read_csv(sentiment_candidate_path)
    sentiment_snapshot_rows = _read_csv(sentiment_snapshot_path)
    sentiment_summary_md = _safe_read_text(sentiment_summary_path, redactions)
    self_improvement_md = _safe_read_text(self_improvement_md_path, redactions)

    daily_preview = _artifact_preview(daily_md_path, root)
    weekly_preview = _artifact_preview(weekly_md_path, root)
    self_improvement_preview = _artifact_preview(self_improvement_md_path, root)
    alerts_preview = _artifact_preview(_latest_file(alerts_dir, "*.md"), root)

    return {
        "generated_at": _utc_now(),
        "report_stem": report_stem,
        "read_only_mode": True,
        "manifests": manifests,
        "daily_manifest": daily_manifest,
        "intraday_manifest": intraday_manifest,
        "provider_summary": provider_summary,
        "systemd_summary": systemd_summary,
        "files": {
            "daily_report": _artifact_meta(daily_md_path, root, "daily report"),
            "weekly_report": _artifact_meta(weekly_md_path, root, "weekly report"),
            "alerts_report": _artifact_meta(_latest_file(alerts_dir, "*.md"), root, "alerts report"),
            "paper_ledger": _artifact_meta(paper_ledger_path, root, "paper ledger"),
            "paper_positions": _artifact_meta(paper_positions_path, root, "paper positions"),
            "reconciliation": _artifact_meta(reconciliation_csv_path, root, "reconciliation"),
            "data_quality": _artifact_meta(data_quality_path, root, "data quality audit"),
            "edge_audit": _artifact_meta(edge_audit_path, root, "edge audit"),
            "leaderboard": _artifact_meta(leaderboard_path, root, "leaderboard"),
            "hypothesis_queue": _artifact_meta(hypothesis_queue_path, root, "hypothesis queue"),
            "hypothesis_results": _artifact_meta(hypothesis_results_path, root, "hypothesis results"),
            "creative_ideas": _artifact_meta(creative_ideas_path, root, "creative ideas"),
            "sentiment_candidates": _artifact_meta(sentiment_candidate_path if sentiment_candidate_path.exists() else None, root, "sentiment candidates"),
            "sentiment_snapshot": _artifact_meta(sentiment_snapshot_path, root, "sentiment snapshot"),
        },
        "legacy": {
            "gate_rows": gate_rows,
            "robustness_rows": robustness_rows,
            "stability_rows": stability_rows,
            "candidate_rows": candidate_rows,
            "backtest_rows": backtest_rows,
            "equity_rows": equity_rows,
            "costs_rows": costs_rows,
            "alerts_rows": alerts_rows,
            "alerts_md": alerts_md,
        },
        "portfolio": {
            "paper_ledger_rows": paper_ledger_rows,
            "paper_positions_rows": paper_positions_rows,
            "reconciliation_rows": reconciliation_rows,
            "equity_points": _equity_points(equity_rows),
            "equity_summary": _equity_summary(equity_rows),
            "ledger_summary": _paper_ledger_summary(paper_ledger_rows, paper_positions_rows),
            "reconciliation_summary": _reconciliation_summary(reconciliation_rows),
            "simulator_summary": _simulator_summary(paper_ledger_rows, ibkr_snapshot),
            "ibkr_snapshot": ibkr_snapshot,
        },
        "research": {
            "paper_gate": _paper_gate_summary(gate_rows),
            "wf_pass_rate": _wf_pass_rate(robustness_rows),
            "top_rejected_reasons": _top_rejected_reasons(gate_rows),
            "leaderboard_rows": leaderboard_rows[:8],
            "top_candidates": candidate_rows[:8] if candidate_rows else leaderboard_rows[:8],
            "backtest_rows": backtest_rows[:8],
        },
        "data": {
            "data_quality_rows": data_quality_rows,
            "edge_audit_rows": edge_audit_rows,
            "data_quality_summary": _data_quality_summary(data_quality_rows),
            "edge_audit_summary": _edge_audit_summary(edge_audit_rows),
            "missing_data_summary": _missing_data_summary(data_quality_rows),
        },
        "sentiment": _sentiment_dashboard_summary(sentiment_candidate_rows, sentiment_snapshot_rows, sentiment_summary_md),
        "improvement": {
            "self_improvement_md": self_improvement_md,
            "self_improvement_preview": self_improvement_preview,
            "hypothesis_queue_rows": hypothesis_queue_rows[:12],
            "hypothesis_results_rows": hypothesis_results_rows[:12],
            "creative_ideas_rows": creative_ideas_rows[:12],
            "summary": _improvement_summary(self_improvement_md, hypothesis_queue_rows, hypothesis_results_rows, creative_ideas_rows),
        },
        "alerts": {
            "alerts_rows": alerts_rows,
            "alerts_preview": alerts_preview,
            "summary": _alerts_summary(alerts_rows, alerts_md, provider_summary, ibkr_snapshot),
            "latest_alerts": _latest_alert_messages(alerts_rows, alerts_md),
        },
        "artifacts": [
            _artifact_meta(daily_md_path, root, "latest daily report"),
            _artifact_meta(weekly_md_path, root, "latest weekly report"),
            _artifact_meta(_latest_file(alerts_dir, "*.md"), root, "latest alerts report"),
            _artifact_meta(self_improvement_md_path, root, "latest self-improvement"),
            _artifact_meta(sentiment_candidate_path if sentiment_candidate_path.exists() else None, root, "sentiment candidates"),
            _artifact_meta(paper_ledger_path, root, "paper ledger"),
            _artifact_meta(ibkr_snapshot_path, root, "IBKR read-only snapshot"),
        ],
    }


def render_dashboard_html(root: Path, report_stem: str | None = None) -> str:
    return _render_html(build_dashboard_snapshot(root, report_stem))


def render_dashboard_json(root: Path, report_stem: str | None = None) -> dict[str, Any]:
    return build_dashboard_snapshot(root, report_stem)


def render_artifact_preview(root: Path, relative_path: str) -> tuple[str, str, int]:
    resolved = _resolve_preview_path(root, relative_path)
    if resolved is None:
        body = _render_preview_page("Artifact preview", relative_path, "not available")
        return body, "text/html; charset=utf-8", 404
    redactions = _load_redactions(root)
    content = _safe_read_text(resolved, redactions)
    body = _render_preview_page("Artifact preview", _relative_path(root, resolved), content or "empty")
    return body, "text/html; charset=utf-8", 200


def write_static_dashboard(root: Path, report_stem: str) -> dict[str, Any]:
    dashboard_dir = root / "reports" / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    path = dashboard_dir / "index.html"
    path.write_text(render_dashboard_html(root, report_stem), encoding="utf-8")
    return {"path": path}


def validate_static_dashboard(path: Path) -> list[str]:
    if not path.exists():
        return ["dashboard html"]
    html_text = path.read_text(encoding="utf-8").lower()
    checks = {
        "read only mode": "read only mode",
        "live status": "live status",
        "research results": "research results",
        "portfolio / paper readiness": "portfolio / paper readiness",
        "sentiment / attention": "sentiment / attention",
        "data / edge audit": "data / edge audit",
        "improvement ideas": "improvement ideas",
        "alerts / errors": "alerts / errors",
        "equity chart": 'id="equity-chart"',
    }
    return [name for name, marker in checks.items() if marker not in html_text]


def _render_html(snapshot: dict[str, Any]) -> str:
    files = snapshot["files"]
    legacy = snapshot["legacy"]
    research = snapshot["research"]
    portfolio = snapshot["portfolio"]
    sentiment = snapshot["sentiment"]
    data = snapshot["data"]
    improvement = snapshot["improvement"]
    alerts = snapshot["alerts"]
    artifacts = snapshot["artifacts"]

    gate_rows = legacy["gate_rows"]
    robustness_rows = legacy["robustness_rows"]
    equity_points = portfolio["equity_points"]
    equity_state = _equity_state(equity_points)
    paper_gate = research["paper_gate"]
    wf_pass_rate = research["wf_pass_rate"]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="X-Content-Type-Options" content="nosniff">
  <meta http-equiv="Cache-Control" content="no-store">
  <title>Research Lab Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17201a;
      --muted: #5d665f;
      --line: #d9ded8;
      --surface: #ffffff;
      --surface-alt: #f7f8f5;
      --accent: #0f766e;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #146c43;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: #fbfbf8;
      letter-spacing: 0;
    }}
    header {{
      padding: 24px 28px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }}
    h1 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.15;
    }}
    .subtitle {{
      margin-top: 8px;
      color: var(--muted);
      line-height: 1.45;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      padding: 6px 10px;
      border-radius: 4px;
      background: var(--bad);
      color: #fff;
      font-weight: 700;
      font-size: 12px;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px 20px 48px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1px;
      border: 1px solid var(--line);
      background: var(--line);
      margin-bottom: 20px;
    }}
    .metric {{
      background: var(--surface);
      padding: 14px 16px;
      min-height: 88px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .metric strong {{
      display: block;
      margin-top: 8px;
      font-size: 20px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}
    section {{
      margin-top: 24px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 18px;
      line-height: 1.25;
    }}
    p {{
      margin: 0;
      line-height: 1.5;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .panel {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 14px;
    }}
    .panel h3 {{
      margin: 0 0 8px;
      font-size: 14px;
      line-height: 1.25;
    }}
    .muted {{ color: var(--muted); }}
    .status-ok {{ color: var(--good); font-weight: 700; }}
    .status-warn {{ color: var(--warn); font-weight: 700; }}
    .status-bad {{ color: var(--bad); font-weight: 700; }}
    .empty {{
      padding: 14px;
      border: 1px dashed var(--line);
      background: var(--surface);
      color: var(--muted);
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      background: var(--surface);
    }}
    table {{
      width: 100%;
      min-width: 760px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      line-height: 1.35;
    }}
    th {{
      background: var(--surface-alt);
      color: var(--muted);
      font-weight: 700;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .artifact-list {{
      display: grid;
      gap: 10px;
    }}
    .artifact-item {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 12px 14px;
    }}
    .artifact-item .top {{
      display: flex;
      gap: 10px;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
    }}
    .artifact-item .label {{ font-weight: 700; }}
    .artifact-item .meta {{ color: var(--muted); font-size: 12px; }}
    .tag {{
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface-alt);
      font-size: 12px;
      color: var(--muted);
    }}
    .svg-wrap {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 10px;
      overflow-x: auto;
    }}
    svg {{
      display: block;
      width: 100%;
      height: auto;
      min-width: 680px;
    }}
    @media (max-width: 720px) {{
      header {{ padding: 20px 16px 16px; }}
      h1 {{ font-size: 24px; }}
      main {{ padding: 18px 14px 36px; }}
      .metric strong {{ font-size: 18px; }}
      table {{ min-width: 640px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Research Lab Dashboard</h1>
    <p class="subtitle">Read-only observability snapshot for {_e(snapshot["report_stem"])} generated {_e(snapshot["generated_at"])}.</p>
    <div class="badge">{READ_ONLY_LABEL}</div>
  </header>
  <main>
    <div class="metrics">
      {_metric("Latest daily run", _file_state(files["daily_report"]))}
      {_metric("Latest weekly run", _file_state(files["weekly_report"]))}
      {_metric("Provider status", provider_label(snapshot))}
      {_metric("Paper gate", f'{paper_gate["eligible"]} eligible / {paper_gate["blocked"]} blocked')}
      {_metric("Rolling WF pass", wf_pass_rate)}
      {_metric("Alerts", len(alerts["alerts_rows"]))}
    </div>

    <section>
      <h2>Live Status</h2>
      <div class="grid">
        <div class="panel">
          <h3>Artifacts</h3>
          <div class="artifact-list">
            {_artifact_item(files["daily_report"], root=None)}
            {_artifact_item(files["weekly_report"], root=None)}
            {_artifact_item(files["alerts_report"], root=None)}
            {_artifact_item(files["paper_ledger"], root=None)}
            {_artifact_item(files["paper_positions"], root=None)}
            {_artifact_item(files["reconciliation"], root=None)}
          </div>
        </div>
        <div class="panel">
          <h3>Provider / System</h3>
          <p>{_e(snapshot["provider_summary"]["text"])}</p>
          <p class="muted" style="margin-top: 8px;">{_e(snapshot["systemd_summary"]["text"])}</p>
          <p class="muted" style="margin-top: 8px;">{_e(snapshot["daily_manifest"]["text"])}</p>
          <p class="muted" style="margin-top: 8px;">{_e(snapshot["intraday_manifest"]["text"])}</p>
        </div>
        <div class="panel">
          <h3>Last alerts / errors</h3>
          {_latest_messages_html(alerts["latest_alerts"])}
        </div>
      </div>
    </section>

    <section>
      <h2>Research Results</h2>
      <div class="grid">
        <div class="panel">
          <h3>Gate summary</h3>
          <p>{_e(paper_gate["text"])}</p>
          <p class="muted" style="margin-top: 8px;">Top rejected reasons: {_e(", ".join(research["top_rejected_reasons"]) or "not available")}</p>
          <p class="muted" style="margin-top: 8px;">WF pass rate: {_e(wf_pass_rate)}</p>
        </div>
        <div class="panel">
          <h3>Reports</h3>
          <div class="artifact-list">
            {_artifact_link_row(snapshot, files["weekly_report"], "weekly report preview")}
            {_artifact_link_row(snapshot, files["daily_report"], "daily report preview")}
            {_artifact_link_row(snapshot, files["leaderboard"], "leaderboard preview")}
            {_artifact_link_row(snapshot, files["edge_audit"], "edge audit preview")}
          </div>
        </div>
      </div>
      {_table_section("Deployment Gate", gate_rows, ["strategy_id", "tier", "paper_eligible", "gate_verdict", "walk_forward_verdict", "parameter_verdict", "cost_verdict", "reasons"], "No deployment gate rows available.")}
      {_table_section("Robustness", robustness_rows, ["strategy_id", "walk_forward_status", "window_count", "walk_forward_score", "median_test_cagr", "robustness_verdict"], "No robustness rows available.")}
      {_table_section("Top Candidates", research["top_candidates"], ["strategy_id", "family", "short_name", "tier", "portfolio_score", "suggested_weight_pct", "reason"], "No candidates available.")}
    </section>

    <section>
      <h2>Portfolio / Paper Readiness</h2>
      <div class="grid">
        <div class="panel">
          <h3>Equity and drawdown</h3>
          {_equity_chart_svg(equity_points)}
        </div>
        <div class="panel">
          <h3>Paper status</h3>
          <p>{_e(portfolio["ledger_summary"])}</p>
          <p class="muted" style="margin-top: 8px;">IBKR snapshot: {_e(_ibkr_snapshot_summary(snapshot["portfolio"]["ibkr_snapshot"]))}</p>
          <p class="muted" style="margin-top: 8px;">Reconciliation: {_e(portfolio["reconciliation_summary"])}</p>
          <p class="muted" style="margin-top: 8px;">Simulator: {_e(portfolio["simulator_summary"])}</p>
        </div>
      </div>
      {_table_section("Portfolio Backtest", legacy["backtest_rows"], ["strategy_id", "status", "max_drawdown"], "No portfolio backtest rows available.")}
      {_table_section("Paper Ledger", portfolio["paper_ledger_rows"], ["date", "cash", "equity", "daily_pnl", "gross_exposure_pct", "strategy_count", "source"], "No paper ledger rows available.")}
    </section>

    <section>
      <h2>Sentiment / Attention</h2>
      <div class="grid">
        <div class="panel">
          <h3>Coverage status</h3>
          <p>{_e(sentiment["coverage_summary"])}</p>
          <p class="muted" style="margin-top: 8px;">Warnings: {_e(sentiment["warnings"])}</p>
          <p class="muted" style="margin-top: 8px;">Research-only confirmation layer. Not a trading signal.</p>
        </div>
        <div class="panel">
          <h3>Top narrative tags</h3>
          {_tag_list_html(sentiment["top_narrative_tags"])}
        </div>
        <div class="panel">
          <h3>Provider coverage</h3>
          <p>{_e(sentiment["provider_coverage"])}</p>
        </div>
      </div>
      {_table_section("Top sentiment + momentum candidates", sentiment["top_candidates"], ["ticker", "research_rank", "combined_sentiment_score", "attention_delta_7d", "price_return_5d", "volume_zscore", "price_confirmed_sentiment", "narrative_tags", "coverage_status", "research_only", "not_trading_signal"], "No sentiment candidates available.")}
      {_table_section("Top attention acceleration tickers", sentiment["attention_candidates"], ["ticker", "attention_delta_7d", "mentions_zscore", "combined_sentiment_score", "price_return_5d", "price_confirmed_sentiment", "coverage_status"], "No attention acceleration rows available.")}
      {_table_section("IREN-like candidates", sentiment["iren_like_candidates"], ["ticker", "price_return_5d", "attention_delta_7d", "volume_zscore", "narrative_tags", "price_confirmed_sentiment"], "No IREN-like candidates available.")}
      {_table_section("Failed hype candidates", sentiment["failed_hype_candidates"], ["ticker", "combined_sentiment_score", "price_return_5d", "attention_delta_7d", "price_confirmed_sentiment"], "No failed hype candidates available.")}
      {_table_section("Stealth momentum candidates", sentiment["stealth_momentum_candidates"], ["ticker", "combined_sentiment_score", "price_return_5d", "attention_delta_7d", "price_confirmed_sentiment"], "No stealth momentum candidates available.")}
    </section>

    <section>
      <h2>Data / Edge Audit</h2>
      <div class="grid">
        <div class="panel">
          <h3>Data quality</h3>
          <p>{_e(data["data_quality_summary"])}</p>
          <p class="muted" style="margin-top: 8px;">{_e(data["missing_data_summary"])}</p>
        </div>
        <div class="panel">
          <h3>Edge audit</h3>
          <p>{_e(data["edge_audit_summary"])}</p>
        </div>
        <div class="panel">
          <h3>Coverage notes</h3>
          <p class="muted">Fundamentals coverage: not available.</p>
          <p class="muted" style="margin-top: 8px;">Event windows: not available.</p>
          <p class="muted" style="margin-top: 8px;">Congress pilot quality: not available.</p>
        </div>
      </div>
      {_table_section("Data Quality Audit", data["data_quality_rows"], ["dataset", "symbol", "check", "status", "value", "threshold", "details"], "No data quality audit CSV available.")}
      {_table_section("Edge Audit", data["edge_audit_rows"], ["item_id", "source", "family", "title", "edge_bucket", "edge_strength", "failure_mode", "validation_requirement"], "No edge audit rows available.")}
    </section>

    <section>
      <h2>Improvement Ideas</h2>
      <div class="grid">
        <div class="panel">
          <h3>Self-improvement</h3>
          <div class="artifact-list">
            {_artifact_link_row(snapshot, files["creative_ideas"], "creative ideas preview")}
            {_artifact_link_row(snapshot, files["hypothesis_queue"], "hypothesis queue preview")}
            {_artifact_link_row(snapshot, files["hypothesis_results"], "hypothesis results preview")}
            {_artifact_link_row(snapshot, files["data_quality"], "data quality audit preview")}
          </div>
        </div>
        <div class="panel">
          <h3>Summary</h3>
          <p>{_e(improvement["summary"])}</p>
          <p class="muted" style="margin-top: 8px;">{_e(improvement["self_improvement_md"].splitlines()[0] if improvement["self_improvement_md"].splitlines() else "No self-improvement report available.")}</p>
        </div>
      </div>
      {_table_section("Hypothesis Queue", improvement["hypothesis_queue_rows"], ["hypothesis_id", "family", "title", "status", "logged_at"], "No queued hypotheses available.")}
      {_table_section("Hypothesis Results", improvement["hypothesis_results_rows"], ["hypothesis_id", "family", "title", "status", "logged_at"], "No hypothesis results available.")}
      {_table_section("Creative Ideas", improvement["creative_ideas_rows"], ["creative_idea_id", "family", "hypothesis_id", "title", "status", "logged_at"], "No creative ideas available.")}
    </section>

    <section>
      <h2>Alerts / Errors</h2>
      <div class="grid">
        <div class="panel">
          <h3>Alerts summary</h3>
          <p>{_e(alerts["summary"])}</p>
          <p class="muted" style="margin-top: 8px;">Cost threshold warning: not available.</p>
          <p class="muted" style="margin-top: 8px;">Provider failure: {_e(snapshot["provider_summary"]["warning"])}</p>
          <p class="muted" style="margin-top: 8px;">IBKR connection/read-only error: {_e(_ibkr_error_summary(snapshot["portfolio"]["ibkr_snapshot"]))}</p>
          <p class="muted" style="margin-top: 8px;">Missing artifact warnings: {_e(_missing_artifact_warning(snapshot))}</p>
        </div>
        <div class="panel">
          <h3>Alerts report</h3>
          {_artifact_link_row(snapshot, files["alerts_report"], "alerts markdown preview")}
          {_latest_messages_html(alerts["latest_alerts"])}
        </div>
      </div>
      {_table_section("Alerts", alerts["alerts_rows"], ["severity", "event_type", "title", "details", "source", "delivery_status"], "No alert rows available.")}
    </section>

    <section>
      <h2>Legacy Snapshot</h2>
      <p class="muted">Retained for backwards compatibility with the existing static export and its tests.</p>
      {_table_section("Research Costs", legacy["costs_rows"], ["category", "unit", "quantity", "estimated_cost_usd", "notes"], "No research cost rows available.")}
      {_table_section("Leaderboard", snapshot["research"]["leaderboard_rows"], ["strategy_id", "family", "asset_class", "timeframe", "tier", "data_source", "unseen_cagr", "unseen_mar", "unseen_max_drawdown"], "No leaderboard rows available.")}
    </section>
  </main>
</body>
</html>
"""


def _render_preview_page(title: str, label: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      font-family: Arial, Helvetica, sans-serif;
      color: #17201a;
      background: #fbfbf8;
      letter-spacing: 0;
    }}
    h1 {{ margin: 0 0 12px; font-size: 22px; }}
    .meta {{ color: #5d665f; margin-bottom: 16px; }}
    pre {{
      margin: 0;
      padding: 16px;
      border: 1px solid #d9ded8;
      background: #fff;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.4;
    }}
  </style>
</head>
<body>
  <h1>{_e(title)}</h1>
  <div class="meta">{_e(label)}</div>
  <pre>{_e(content)}</pre>
</body>
</html>
"""


def _equity_chart_svg(points: list[tuple[str, float]]) -> str:
    if not points:
        return '<div id="equity-chart" class="empty">No portfolio equity data available. No portfolio equity curve available.</div>'
    width = 760
    height = 240
    pad = 28
    equities = [point[1] for point in points]
    peaks = []
    peak = equities[0]
    for value in equities:
        peak = max(peak, value)
        peaks.append(peak)
    drawdowns = [(value / peak_value - 1.0) if peak_value else 0.0 for value, peak_value in zip(equities, peaks)]
    equity_polyline = _polyline(equities, width, height, pad)
    drawdown_polyline = _polyline(drawdowns, width, height, pad)
    return f"""
    <div class="svg-wrap">
      <svg id="equity-chart" viewBox="0 0 {width} {height}" role="img" aria-label="Portfolio equity and drawdown chart">
        <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" stroke="#d9ded8" />
        <polyline points="{_e(equity_polyline)}" fill="none" stroke="#0f766e" stroke-width="2" />
        <polyline points="{_e(drawdown_polyline)}" fill="none" stroke="#b91c1c" stroke-width="2" />
        <text x="{pad}" y="20" font-size="12" fill="#17201a">Equity</text>
        <text x="{pad + 66}" y="20" font-size="12" fill="#b91c1c">Drawdown</text>
      </svg>
    </div>
    """


def _table_section(title: str, rows: list[dict[str, Any]], columns: list[str], empty_message: str) -> str:
    if not rows:
        return f'<div class="panel" style="margin-top: 12px;"><h3>{_e(title)}</h3><div class="empty">{_e(empty_message)}</div></div>'
    header = "".join(f"<th>{_e(column)}</th>" for column in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_e(row.get(column, ''))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    return f'<div class="panel" style="margin-top: 12px;"><h3>{_e(title)}</h3><div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table></div></div>'


def _artifact_meta(path: Path | None, root: Path, label: str) -> dict[str, Any]:
    if path is None:
        return {"label": label, "status": "missing", "path": "", "updated": "not available", "size": 0, "preview_path": "", "link": ""}
    try:
        size = path.stat().st_size
        updated = _format_mtime(path.stat().st_mtime)
    except OSError:
        size = 0
        updated = "not available"
    status = "empty" if size == 0 else "available"
    rel = _relative_path(root, path)
    return {
        "label": label,
        "status": status,
        "path": rel,
        "updated": updated,
        "size": size,
        "preview_path": rel,
        "link": f"/preview?path={_url_escape(rel)}",
    }


def _artifact_item(item: dict[str, Any], root: Path | None = None) -> str:
    status_class = "status-bad" if item["status"] == "missing" else "status-warn" if item["status"] == "empty" else "status-ok"
    path = item.get("path") or "not available"
    link = item.get("link") or ""
    label = item["label"]
    if link:
        path_html = f'<a href="{_e(link)}">{_e(path)}</a>'
    else:
        path_html = _e(path)
    return f'<div class="artifact-item"><div class="top"><div class="label">{_e(label)}</div><span class="tag {status_class}">{_e(item["status"])}</span></div><div class="meta">{path_html} · {_e(item["updated"])}</div></div>'


def _artifact_link_row(snapshot: dict[str, Any], item: dict[str, Any], label: str) -> str:
    if not item.get("link"):
        return f'<div class="artifact-item"><div class="top"><div class="label">{_e(label)}</div><span class="tag status-bad">missing</span></div><div class="meta">not available</div></div>'
    return f'<div class="artifact-item"><div class="top"><div class="label">{_e(label)}</div><span class="tag status-ok">{_e(item["status"])}</span></div><div class="meta"><a href="{_e(item["link"])}">{_e(item["path"])}</a> · {_e(item["updated"])}</div></div>'


def _latest_messages_html(messages: list[str]) -> str:
    if not messages:
        return '<div class="empty">No messages available.</div>'
    return "<div class=\"artifact-list\">" + "".join(f'<div class="artifact-item"><div class="meta">{_e(message)}</div></div>' for message in messages[:5]) + "</div>"


def _equity_points(rows: list[dict[str, str]]) -> list[tuple[str, float]]:
    points = []
    for row in rows:
        value = row.get("equity") or row.get("portfolio_equity") or row.get("equity_value")
        if value in {"", None}:
            continue
        try:
            points.append((str(row.get("date", "")), float(value)))
        except (TypeError, ValueError):
            continue
    return points


def _equity_summary(rows: list[dict[str, str]]) -> str:
    points = _equity_points(rows)
    if not points:
        return "No portfolio equity data available."
    values = [value for _, value in points]
    return f"Equity points: {len(points)}; latest equity: {values[-1]:.2f}; min equity: {min(values):.2f}; max equity: {max(values):.2f}."


def _paper_ledger_summary(ledger_rows: list[dict[str, str]], position_rows: list[dict[str, str]]) -> str:
    if not ledger_rows:
        return "Paper ledger: not available."
    latest = ledger_rows[-1]
    return (
        "Paper ledger latest row: "
        f"date={latest.get('date', 'n/a')}, equity={latest.get('equity', 'n/a')}, "
        f"cash={latest.get('cash', 'n/a')}, positions={len(position_rows)}."
    )


def _reconciliation_summary(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "IBKR reconciliation: not available."
    verdicts = Counter(str(row.get("verdict", "")) for row in rows)
    return f"IBKR reconciliation rows: {len(rows)}; verdicts: {', '.join(f'{key}={value}' for key, value in sorted(verdicts.items()))}."


def _simulator_summary(ledger_rows: list[dict[str, str]], ibkr_snapshot: dict[str, Any]) -> str:
    if not ledger_rows:
        return "Paper simulator: not available."
    if ibkr_snapshot.get("status"):
        return f"Paper simulator: read-only snapshot status={ibkr_snapshot.get('status')}."
    return "Paper simulator: not available."


def _ibkr_snapshot_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "not available"
    status = snapshot.get("status", "unknown")
    read_only = snapshot.get("read_only", snapshot.get("read_only_mode", "unknown"))
    mode = snapshot.get("mode", "unknown")
    managed = snapshot.get("managed_accounts") or []
    return f"status={status}, mode={mode}, read_only={read_only}, managed_accounts={len(managed)}"


def _ibkr_error_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "not available"
    error = snapshot.get("error")
    if error:
        return str(error)
    status = snapshot.get("status", "unknown")
    if status and status not in {"ok", "connected_read_only"}:
        return f"read-only snapshot status={status}"
    return "not available"


def _paper_gate_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    eligible = sum(1 for row in rows if str(row.get("paper_eligible", "")).lower() == "true")
    blocked = len(rows) - eligible
    reasons = _top_rejected_reasons(rows)
    return {
        "eligible": eligible,
        "blocked": blocked,
        "text": f"{eligible} eligible / {blocked} blocked; top rejected reasons: {', '.join(reasons) or 'not available'}.",
    }


def _wf_pass_rate(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "not available"
    passed = sum(1 for row in rows if str(row.get("robustness_verdict", "")).lower() == "pass")
    return f"{passed}/{len(rows)}"


def _top_rejected_reasons(rows: list[dict[str, str]]) -> list[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        if str(row.get("paper_eligible", "")).lower() == "true":
            continue
        reasons = str(row.get("reasons", ""))
        for reason in reasons.split(";"):
            reason = reason.strip()
            if reason:
                counter[reason] += 1
    return [reason for reason, _ in counter.most_common(5)]


def _data_quality_summary(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "No data quality audit CSV available."
    failures = sum(1 for row in rows if str(row.get("status", "")).lower() == "fail")
    return f"Data quality checks: {len(rows)}; failures: {failures}."


def _edge_audit_summary(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "No edge audit rows available."
    buckets = Counter(str(row.get("edge_bucket", "")) for row in rows)
    rendered = ", ".join(f"{name}={count}" for name, count in sorted(buckets.items()))
    return f"Edge audit rows: {len(rows)}; buckets: {rendered}."


def _missing_data_summary(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "Missing bars / duplicates / extreme returns summary: not available."
    failures = [row for row in rows if str(row.get("status", "")).lower() == "fail"]
    if not failures:
        return "Missing bars / duplicates / extreme returns summary: no failures."
    return f"Missing bars / duplicates / extreme returns summary: {len(failures)} failed checks."


def _sentiment_dashboard_summary(candidate_rows: list[dict[str, str]], snapshot_rows: list[dict[str, str]], summary_md: str) -> dict[str, Any]:
    coverage_counts = Counter(str(row.get("coverage_status", "missing") or "missing") for row in candidate_rows)
    provider_counts = Counter(str(row.get("provider", "unknown") or "unknown") for row in candidate_rows)
    warnings = []
    for row in snapshot_rows:
        status = str(row.get("coverage_status", "")).lower()
        if status in {"missing", "stale", "error"}:
            ticker = row.get("ticker", "unknown")
            reason = row.get("stale_reason") or status
            warnings.append(f"{ticker}: {reason}")
    rows = list(candidate_rows)
    top_candidates = sorted(rows, key=lambda row: _float_or_zero(row.get("research_score")), reverse=True)[:8]
    attention_candidates = sorted(rows, key=lambda row: _float_or_zero(row.get("attention_delta_7d")), reverse=True)[:8]
    return {
        "top_candidates": top_candidates,
        "attention_candidates": attention_candidates,
        "iren_like_candidates": [row for row in rows if _is_iren_like(row)][:8],
        "failed_hype_candidates": [row for row in rows if row.get("price_confirmed_sentiment") == "failed_hype_or_distribution"][:8],
        "stealth_momentum_candidates": [row for row in rows if row.get("price_confirmed_sentiment") in {"stealth_momentum", "price_only"}][:8],
        "top_narrative_tags": _top_narrative_tags(rows),
        "coverage_summary": _counter_summary(coverage_counts) if candidate_rows else "sentiment layer not available",
        "provider_coverage": _counter_summary(provider_counts) if candidate_rows else "not available",
        "warnings": "; ".join(warnings[:6]) if warnings else ("none" if candidate_rows else "sentiment layer not available"),
        "summary_md": summary_md,
    }


def _is_iren_like(row: dict[str, str]) -> bool:
    tags = set(str(row.get("narrative_tags", "")).split("|"))
    narrative_match = bool(tags & {"AI infrastructure", "GPU cloud", "power capacity", "bitcoin mining"})
    return _float_or_zero(row.get("price_return_5d")) > 0 and _float_or_zero(row.get("attention_delta_7d")) > 0 and narrative_match


def _top_narrative_tags(rows: list[dict[str, str]]) -> list[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for tag in str(row.get("narrative_tags", "")).split("|"):
            tag = tag.strip()
            if tag:
                counter[tag] += 1
    return [tag for tag, _ in counter.most_common(8)]


def _counter_summary(counter: Counter[str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items())) or "not available"


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _tag_list_html(tags: list[str]) -> str:
    if not tags:
        return '<div class="empty">No narrative tags available.</div>'
    return '<div class="artifact-list">' + "".join(f'<span class="tag">{_e(tag)}</span>' for tag in tags) + "</div>"


def _improvement_summary(self_md: str, hypotheses: list[dict[str, Any]], results: list[dict[str, Any]], ideas: list[dict[str, Any]]) -> str:
    if not self_md.strip():
        return "No self-improvement report available."
    return (
        f"Self-improvement report loaded; queue items={len(hypotheses)}, "
        f"hypothesis results={len(results)}, creative ideas={len(ideas)}."
    )


def _alerts_summary(rows: list[dict[str, str]], alerts_md: str, provider_summary: dict[str, str], ibkr_snapshot: dict[str, Any]) -> str:
    parts = []
    if rows:
        parts.append(f"{len(rows)} alert rows")
    else:
        parts.append("no alert rows")
    if alerts_md.strip():
        parts.append("alerts markdown available")
    else:
        parts.append("alerts markdown empty")
    if provider_summary.get("warning"):
        parts.append(provider_summary["warning"])
    if ibkr_snapshot.get("error"):
        parts.append(str(ibkr_snapshot["error"]))
    return "; ".join(parts)


def _latest_alert_messages(rows: list[dict[str, str]], alerts_md: str) -> list[str]:
    messages = []
    for row in rows[:5]:
        title = row.get("title", "")
        details = row.get("details", "")
        status = row.get("delivery_status", "")
        payload = " | ".join(part for part in [title, details, status] if part)
        if payload:
            messages.append(payload)
    if not messages and alerts_md.strip():
        lines = [line.strip("- ").strip() for line in alerts_md.splitlines() if line.strip().startswith("-")]
        messages.extend(lines[:5])
    return messages


def _provider_summary(manifests: list[dict[str, Any]]) -> dict[str, str]:
    sources = [str(manifest.get("source", "")) for manifest in manifests if manifest.get("source")]
    if not sources:
        return {"text": "Provider status: not available.", "warning": "provider status not available"}
    unique = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    if "synthetic" in unique:
        return {
            "text": f"Provider status: synthetic ({', '.join(unique)}).",
            "warning": "synthetic data in use; no capital relevance until real providers are validated.",
        }
    if "massive" in unique:
        return {
            "text": f"Provider status: Massive ({', '.join(unique)}).",
            "warning": "Massive data available; verify history length before promotion.",
        }
    if "yfinance" in unique:
        return {
            "text": f"Provider status: yfinance ({', '.join(unique)}).",
            "warning": "yfinance data available; verify data integrity before promotion.",
        }
    return {"text": f"Provider status: {', '.join(unique)}.", "warning": "provider status requires manual review."}


def _systemd_summary() -> dict[str, str]:
    if shutil.which("systemctl") is None:
        return {"text": "Systemd status: not available on this host.", "warning": "systemd status not available"}
    units = [
        "research-lab-dashboard.service",
        "trading-research-daily.service",
        "trading-research-weekly.service",
        "trading-research-hourly.service",
        "trading-research-self-improvement.service",
    ]
    statuses = []
    for unit in units:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        parsed = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key.strip()] = value.strip()
        if parsed:
            statuses.append(f"{unit}: {parsed.get('LoadState', 'unknown')}/{parsed.get('ActiveState', 'unknown')}/{parsed.get('SubState', 'unknown')}")
    if not statuses:
        return {"text": "Systemd status: not available.", "warning": "systemd status not available"}
    return {"text": "Systemd status: " + "; ".join(statuses), "warning": "systemd status available"}


def _load_latest_manifest(manifests: list[dict[str, Any]], name: str) -> dict[str, str]:
    match = next((manifest for manifest in manifests if manifest.get("name") == name), {})
    if not match:
        return {"text": f"{name}: not available."}
    source = match.get("source", "unknown")
    rows = match.get("rows", "n/a")
    years = match.get("years", "n/a")
    created = match.get("created_at", "n/a")
    return {"text": f"{name}: source={source}, rows={rows}, years={years}, created_at={created}."}


def _load_redactions(root: Path) -> list[str]:
    candidates: list[str] = []
    env_path = root / ".env"
    if not env_path.exists():
        return candidates
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().upper()
        value = value.strip().strip("'").strip('"')
        if not value:
            continue
        if any(token in key for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
            candidates.append(value)
        elif len(value) >= 12 and not value.isdigit():
            candidates.append(value)
    return sorted(set(candidates), key=len, reverse=True)


def _sanitize(value: Any, redactions: list[str]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, redactions)
    if isinstance(value, list):
        return [_sanitize(item, redactions) for item in value]
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"account", "account_id", "accountid", "client_id", "clientid"}:
                sanitized[key] = "redacted"
                continue
            if lower in {"managed_accounts", "accounts"} and isinstance(item, list):
                sanitized[key] = ["redacted" for _ in item]
                continue
            sanitized[key] = _sanitize(item, redactions)
        return sanitized
    return value


def _redact_text(text: str, redactions: list[str]) -> str:
    result = text
    for token in redactions:
        if token:
            result = result.replace(token, "[redacted]")
    result = re.sub(r"\b(API_KEY|TOKEN|SECRET|PASSWORD)\b", "[redacted]", result, flags=re.IGNORECASE)
    result = re.sub(r"(?i)\baccount\s*[:=]\s*[A-Z0-9_\-./]{4,}\b", "account=[redacted]", result)
    return result


def _safe_read_text(path: Path | None, redactions: list[str]) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return _redact_text(path.read_text(encoding="utf-8"), redactions)
    except UnicodeDecodeError:
        return ""


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path | None, redactions: list[str]) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(_sanitize(json.loads(line), redactions))
        except json.JSONDecodeError:
            continue
    return rows


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except UnicodeDecodeError:
        return []
    return rows


def _artifact_preview(path: Path | None, root: Path) -> dict[str, Any]:
    if path is None:
        return {"path": "", "text": "not available", "status": "missing"}
    rel = _relative_path(root, path)
    if not path.exists():
        return {"path": rel, "text": "not available", "status": "missing"}
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = text.strip()
    if not text:
        return {"path": rel, "text": "empty", "status": "empty"}
    return {"path": rel, "text": text, "status": "available"}


def _resolve_report_path(directory: Path, report_stem: str, suffix: str) -> Path | None:
    candidate = directory / f"{report_stem}{suffix}"
    return candidate if candidate.exists() else None


def _detect_report_stem(root: Path) -> str:
    candidates = [
        _latest_file(root / "reports" / "weekly", "*.md"),
        _latest_file(root / "reports" / "weekly", "*_portfolio_equity.csv"),
        _latest_file(root / "reports" / "weekly", "*_deployment_gate.csv"),
        _latest_file(root / "reports" / "alerts", "*_alerts.csv"),
    ]
    for path in candidates:
        if path is None:
            continue
        name = path.name
        for suffix in (
            "_portfolio_equity.csv",
            "_deployment_gate.csv",
            "_robustness.csv",
            "_stability.csv",
            "_portfolio_candidates.csv",
            "_portfolio_backtest.csv",
            "_research_costs.csv",
            "_alerts.csv",
            "_alerts.md",
            ".md",
            ".csv",
            ".json",
        ):
            if name.endswith(suffix):
                return name[: -len(suffix)]
    return datetime.now(timezone.utc).strftime("%G-W%V")


def _latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    candidates = [path for path in directory.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def _resolve_preview_path(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    if not candidate.exists():
        return None
    allowed_roots = [
        (root / "reports").resolve(),
        (root / "registry").resolve(),
        (root / "data" / "manifests").resolve(),
    ]
    for allowed_root in allowed_roots:
        try:
            if candidate.is_relative_to(allowed_root):
                return candidate
        except AttributeError:
            if str(candidate).startswith(str(allowed_root)):
                return candidate
    return None


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _file_state(meta: dict[str, Any]) -> str:
    return f"{meta['status']} ({meta['updated']})"


def _equity_state(points: list[tuple[str, float]]) -> str:
    if not points:
        return "missing"
    return f"{len(points)} points"


def _format_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _polyline(values: list[float], width: int, height: int, pad: int) -> str:
    if not values:
        return ""
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    usable_w = width - pad * 2
    usable_h = height - pad * 2
    last = max(len(values) - 1, 1)
    pairs = []
    for index, value in enumerate(values):
        x = pad + usable_w * (index / last)
        y = pad + usable_h * (1.0 - ((value - low) / span))
        pairs.append(f"{x:.2f},{y:.2f}")
    return " ".join(pairs)


def _load_latest_manifest(manifests: list[dict[str, Any]], name: str) -> dict[str, str]:
    match = next((manifest for manifest in manifests if manifest.get("name") == name), {})
    if not match:
        return {"text": f"{name}: not available."}
    source = match.get("source", "unknown")
    rows = match.get("rows", "n/a")
    years = match.get("years", "n/a")
    created = match.get("created_at", "n/a")
    return {"text": f"{name}: source={source}, rows={rows}, years={years}, created_at={created}."}


def _url_escape(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _missing_artifact_warning(snapshot: dict[str, Any]) -> str:
    missing = [item["label"] for item in snapshot["artifacts"] if item.get("status") in {"missing", "empty"}]
    if not missing:
        return "none"
    return ", ".join(missing[:6])


def _ibkr_snapshot_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "not available"
    status = snapshot.get("status", "unknown")
    read_only = snapshot.get("read_only", snapshot.get("read_only_mode", "unknown"))
    mode = snapshot.get("mode", "unknown")
    managed = snapshot.get("managed_accounts") or []
    return f"status={status}, mode={mode}, read_only={read_only}, managed_accounts={len(managed)}"


def _ibkr_error_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "not available"
    error = snapshot.get("error")
    if error:
        return str(error)
    status = snapshot.get("status", "unknown")
    if status and status not in {"ok", "connected_read_only"}:
        return f"read-only snapshot status={status}"
    return "not available"


def provider_label(snapshot: dict[str, Any]) -> str:
    return snapshot["provider_summary"]["text"]


def _latest_alert_messages(rows: list[dict[str, str]], alerts_md: str) -> list[str]:
    messages = []
    for row in rows[:5]:
        title = row.get("title", "")
        details = row.get("details", "")
        status = row.get("delivery_status", "")
        payload = " | ".join(part for part in [title, details, status] if part)
        if payload:
            messages.append(payload)
    if not messages and alerts_md.strip():
        lines = [line.strip("- ").strip() for line in alerts_md.splitlines() if line.strip().startswith("-")]
        messages.extend(lines[:5])
    return messages


def _latest_messages_html(messages: list[str]) -> str:
    if not messages:
        return '<div class="empty">No messages available.</div>'
    return "<div class=\"artifact-list\">" + "".join(f'<div class="artifact-item"><div class="meta">{_e(message)}</div></div>' for message in messages[:5]) + "</div>"


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><span>{_e(label)}</span><strong>{_e(value)}</strong></div>'


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
