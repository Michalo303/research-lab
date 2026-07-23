from pathlib import Path
from datetime import date, datetime, timezone
import csv
import os
import sys
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.robustness import summarize_weekly_robustness
from research_lab.weekly_validation_gate import (
    build_weekly_validation_metrics,
    evaluate_weekly_validation_gate,
    render_weekly_validation_gate_markdown,
)


def _weekly_robustness_findings(robustness_rows, stability_rows):
    lines = summarize_weekly_robustness(robustness_rows, stability_rows)
    true_rows = [row for row in robustness_rows if row.get("walk_forward_method") == "true_rolling_oos"]
    if not true_rows:
        return lines

    true_passes = [row for row in true_rows if row.get("robustness_verdict") == "pass"]
    pass_rates = [float(row.get("pass_rate", 0.0) or 0.0) for row in true_rows]
    median_mars = [float(row.get("median_test_mar", 0.0) or 0.0) for row in true_rows]
    true_summary = (
        f"- true walk-forward pass: {len(true_passes)}/{len(true_rows)} "
        f"(median pass_rate={median(pass_rates):.2f}, median MAR={median(median_mars):.2f})"
    )

    findings = [line for line in lines if "proxy" not in line.lower()]
    insert_at = 1 if findings else 0
    findings.insert(insert_at, true_summary)

    regime_breakdown = _true_walk_forward_regime_breakdown(true_rows)
    if regime_breakdown:
        findings.insert(insert_at + 1, f"- true walk-forward regime breakdown: {regime_breakdown}")
    return findings


def _true_walk_forward_regime_breakdown(rows):
    totals = {}
    for row in rows:
        for item in str(row.get("regime_summary", "")).split(";"):
            if ":" not in item or "/" not in item:
                continue
            regime, counts = item.split(":", 1)
            passed, total = counts.split("/", 1)
            regime = regime.strip()
            if not regime:
                continue
            try:
                passed_count = int(passed.strip())
                total_count = int(total.strip())
            except ValueError:
                continue
            current_passed, current_total = totals.get(regime, (0, 0))
            totals[regime] = (current_passed + passed_count, current_total + total_count)
    return "; ".join(f"{regime} {passed}/{total}" for regime, (passed, total) in sorted(totals.items()))


def build_weekly_validation_gate_section(robustness_rows, deployment_rows, evaluated_at=None) -> list[str]:
    weekly_gate = evaluate_weekly_validation_gate(
        build_weekly_validation_metrics(robustness_rows, deployment_rows),
        evaluated_at=evaluated_at,
    )
    return render_weekly_validation_gate_markdown(weekly_gate).splitlines()


def build_weekly_data_provider_section(diagnostics: dict | None) -> list[str]:
    diagnostics = diagnostics or {}
    symbols = diagnostics.get("symbols") or []
    if isinstance(symbols, str):
        symbols_text = symbols
    else:
        symbols_text = ", ".join(str(symbol) for symbol in symbols)
    data_years = float(diagnostics.get("data_years", 0.0) or 0.0)
    return [
        "## Data Provider Diagnostics",
        "",
        f"- requested provider: {diagnostics.get('requested_provider', '')}",
        f"- selected provider: {diagnostics.get('selected_provider', '')}",
        f"- actual provider used: {diagnostics.get('actual_provider', '')}",
        f"- universe: {symbols_text}",
        f"- data range: {diagnostics.get('start_date', '')} to {diagnostics.get('end_date', '')}",
        f"- data years: {data_years:.2f}",
        f"- fallback occurred: {bool(diagnostics.get('fallback_used', False))}",
        f"- fallback reason: {diagnostics.get('fallback_reason', '')}",
    ]


def print_weekly_data_provider_diagnostics(diagnostics: dict | None) -> None:
    diagnostics = diagnostics or {}
    print(
        "weekly_data_selection"
        f" | requested_provider={diagnostics.get('requested_provider', '')}"
        f" | selected_provider={diagnostics.get('selected_provider', '')}"
        f" | actual_provider={diagnostics.get('actual_provider', '')}"
        f" | universe={','.join(str(symbol) for symbol in diagnostics.get('symbols', []) or [])}"
        f" | start_date={diagnostics.get('start_date', '')}"
        f" | end_date={diagnostics.get('end_date', '')}"
        f" | data_years={float(diagnostics.get('data_years', 0.0) or 0.0):.2f}"
        f" | fallback_used={bool(diagnostics.get('fallback_used', False))}"
        f" | fallback_reason={diagnostics.get('fallback_reason', '')}",
        flush=True,
    )


def _run_weekly() -> None:
    from research_lab.alerting import build_weekly_alerts, summarize_alerts, write_and_send_alerts
    from research_lab.apify_dataroma import DEFAULT_SUPERINVESTORS, run_dataroma_actor
    from research_lab.cost_monitor import run_research_cost_monitor, summarize_research_costs
    from research_lab.dashboard import validate_static_dashboard, write_static_dashboard
    from research_lab.data_quality import run_data_quality_audit
    from research_lab.deployment_gate import run_deployment_gate, summarize_deployment_gate
    from research_lab.event_study import run_event_window_study
    from research_lab.fundamentals import enrich_smartmoney_fundamentals
    from research_lab.hypothesis_dedupe import audit_hypothesis_queue
    from research_lab.parameter_sweep import run_parameter_sweep, summarize_parameter_sweep
    from research_lab.paper_ledger import run_paper_portfolio_ledger, summarize_paper_ledger
    from research_lab.portfolio import (
        run_portfolio_combination_backtest,
        run_portfolio_scoring,
        summarize_portfolio_backtest,
        summarize_portfolio_scoring,
    )
    from research_lab.robustness import write_weekly_robustness_outputs
    from research_lab.sentiment import summarize_sentiment_for_weekly
    from research_lab.signals import run_signal_generation, summarize_signals

    root = Path.cwd()
    apify_status = "skipped: APIFY_TOKEN is not set"
    if os.getenv("APIFY_TOKEN", "").strip():
        try:
            max_results = int(os.getenv("APIFY_DATAROMA_MAX_RESULTS", "200"))
            apify_items = run_dataroma_actor(root, superinvestors=DEFAULT_SUPERINVESTORS, max_results=max_results)
            apify_status = f"imported {len(apify_items)} holdings via Apify Dataroma"
        except Exception as exc:
            apify_status = f"failed: {exc}"

    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    leaderboard = root / "registry" / "leaderboard.csv"
    rows = []
    if leaderboard.exists():
        with leaderboard.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    iso_year, iso_week, _ = date.today().isocalendar()
    report_stem = f"{iso_year}-W{iso_week:02d}"
    robustness = write_weekly_robustness_outputs(root, report_stem)
    parameter_sweep = run_parameter_sweep(root, report_stem)
    print_weekly_data_provider_diagnostics(parameter_sweep.get("data_diagnostics"))
    portfolio = run_portfolio_scoring(root, report_stem)
    sentiment_status = "sentiment layer not available"
    sentiment_rows = []
    sentiment_path = root / "registry" / "sentiment_candidates.csv"
    if sentiment_path.exists():
        with sentiment_path.open(newline="", encoding="utf-8") as handle:
            sentiment_rows = list(csv.DictReader(handle))
        sentiment_status = f"available: {len(sentiment_rows)} candidates from {sentiment_path}"
    portfolio_backtest = run_portfolio_combination_backtest(root, report_stem, portfolio["rows"])
    deployment_gate = run_deployment_gate(root, report_stem, robustness["robustness_rows"], parameter_sweep["rows"], portfolio["rows"])
    weekly_gate = evaluate_weekly_validation_gate(
        build_weekly_validation_metrics(robustness["robustness_rows"], deployment_gate["rows"])
    )
    signals = run_signal_generation(root)
    paper_ledger = run_paper_portfolio_ledger(root, report_stem, portfolio["rows"], portfolio_backtest["equity"])
    costs = run_research_cost_monitor(root, report_stem)
    data_quality = run_data_quality_audit(root, report_stem)
    fundamentals = enrich_smartmoney_fundamentals(root, report_stem)
    event_windows = run_event_window_study(root, report_stem)
    congress = run_congress_pilot_if_available(root, report_stem)
    try:
        sentiment_summary = summarize_sentiment_for_weekly(root, report_stem)
    except Exception as exc:
        sentiment_summary = [f"- sentiment layer not available: {exc}"]
    alert_events = build_weekly_alerts(deployment_gate["rows"], portfolio_backtest["summary"], apify_status, costs["total_estimated_cost_usd"])
    alerts = write_and_send_alerts(root, report_stem, alert_events)
    dedupe = audit_hypothesis_queue(root, apply=True)
    dashboard = write_static_dashboard(root, report_stem)
    dashboard_missing = validate_static_dashboard(dashboard["path"])
    dashboard_smoke = "pass" if not dashboard_missing else f"fail missing={','.join(dashboard_missing)}"
    report = report_dir / f"{iso_year}-W{iso_week:02d}.md"
    lines = [
        f"# Weekly Deep Research Report - {iso_year}-W{iso_week:02d}",
        "",
        "## Summary",
        "",
        f"- leaderboard rows reviewed: {len(rows)}",
        f"- Tier A candidates: {sum(1 for row in rows if row.get('tier') == 'A')}",
        f"- Tier B candidates: {sum(1 for row in rows if row.get('tier') == 'B')}",
        f"- rejected: {sum(1 for row in rows if row.get('tier') == 'Rejected')}",
        f"- Apify Dataroma holdings: {apify_status}",
        f"- robustness CSV: {robustness['robustness_path']}",
        f"- stability CSV: {robustness['stability_path']}",
        f"- parameter sweep CSV: {parameter_sweep['path']}",
        f"- portfolio candidates CSV: {portfolio['path']}",
        f"- portfolio backtest CSV: {portfolio_backtest['path']}",
        f"- portfolio equity CSV: {portfolio_backtest['equity_path']}",
        f"- deployment gate CSV: {deployment_gate['path']}",
        f"- weekly validation gate: {weekly_gate.status} / {weekly_gate.tier}",
        f"- signals CSV: {signals['path']}",
        f"- paper ledger CSV: {paper_ledger['path']}",
        f"- paper positions CSV: {paper_ledger['positions_path']}",
        f"- research cost CSV: {costs['path']}",
        f"- data quality CSV: {data_quality['csv_path']}",
        f"- fundamentals CSV: {fundamentals['csv_path']}",
        f"- event windows CSV: {event_windows['csv_path']}",
        f"- congress pilot: {congress['status']}",
        f"- sentiment layer: {sentiment_summary[0].lstrip('- ')}",
        f"- alerts CSV: {alerts['path']}",
        f"- dashboard HTML: {dashboard['path']}",
        f"- dashboard smoke: {dashboard_smoke}",
        (
            "- hypothesis dedupe: "
            f"total={dedupe['total']} kept={dedupe['kept']} duplicates={dedupe['duplicates']} "
            f"applied={dedupe['applied']} archive_path={dedupe['archive_path']}"
        ),
        "",
        "## Robustness Findings",
        "",
        *_weekly_robustness_findings(robustness["robustness_rows"], robustness["stability_rows"]),
        "",
        *build_weekly_data_provider_section(parameter_sweep.get("data_diagnostics")),
        "",
        "## Parameter Findings",
        "",
        *summarize_parameter_sweep(parameter_sweep["rows"]),
        "",
        "## Portfolio Findings",
        "",
        *summarize_portfolio_scoring(portfolio["rows"]),
        *summarize_portfolio_backtest(portfolio_backtest["summary"]),
        "",
        *build_weekly_validation_gate_section(
            robustness["robustness_rows"],
            deployment_gate["rows"],
            evaluated_at=weekly_gate.evaluated_at,
        ),
        "",
        "## Deployment Gate",
        "",
        *summarize_deployment_gate(deployment_gate["rows"]),
        "",
        "## Paper Readiness",
        "",
        *summarize_signals(signals["rows"]),
        *summarize_paper_ledger(paper_ledger["rows"], paper_ledger["positions"]),
        "",
        "## Research Costs",
        "",
        *summarize_research_costs(costs["rows"]),
        "",
        "## Data And Edge Audits",
        "",
        f"- data quality checks: {len(data_quality['rows'])} rows; report={data_quality['report_path']}",
        f"- fundamentals coverage rows: {len(fundamentals['rows'])}; report={fundamentals['report_path']}",
        f"- event windows measured: {len(event_windows['rows'])}; report={event_windows['report_path']}",
        f"- congress pilot status: {congress['status']}; events={congress.get('events_path', '')}; quality={congress.get('quality_path', '')}",
        "",
        "## Sentiment / Attention",
        "",
        *sentiment_summary,
        "",
        "## Alerts",
        "",
        *summarize_alerts(alerts["rows"]),
        "",
        "## Sentiment / Attention (Read-Only)",
        "",
        f"- status: {sentiment_status}",
        "- mode: READ ONLY",
        "- no write endpoints, no order signals, no deployment permission.",
        "",
        "## Research Findings",
        "",
        "- Walk-forward scoring uses true rolling out-of-sample windows as a conservative weekly gate.",
        "- Parameter stability is tested with bounded neighborhood sweeps around eligible real-data EOD groups.",
        "- Portfolio scoring is model-only and penalizes clustered families/strategy groups; it does not authorize allocation.",
        "- No deployment recommendation is allowed from this report.",
        "",
        "## Next Actions",
        "",
        "- Continue with parameter refit and portfolio layer work after the true rolling WF baseline is stable.",
        "- Expand parameter sweeps only after adding stronger data history; do not optimize on 5-year Massive data alone.",
        "- Run real-data daily research so portfolio combination, signals, and ledger have backtestable return series.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"weekly report written: {report}")


def run_congress_pilot_if_available(root: Path, report_stem: str) -> dict:
    from research_lab.congress import import_congress_disclosures

    for rel in ("data/raw/congress_sample.json", "data/raw/congress_sample.csv"):
        path = root / rel
        if path.exists():
            result = import_congress_disclosures(root, path, report_stem)
            return {"status": "imported", **result}
    return {"status": "skipped", "events_path": "", "quality_path": ""}


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _run_weekly()
    except Exception as exc:
        from research_lab.operational_runtime import write_failure_artifact

        artifact = write_failure_artifact(
            Path.cwd(),
            job="weekly",
            exc=exc,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        print(f"weekly research failed: reason_code={type(exc).__name__} failure_artifact={artifact}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
