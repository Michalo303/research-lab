from pathlib import Path
from datetime import date
import csv
import os
import sys
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.apify_dataroma import DEFAULT_SUPERINVESTORS, run_dataroma_actor
from research_lab.parameter_sweep import run_parameter_sweep, summarize_parameter_sweep
from research_lab.portfolio import run_portfolio_scoring, summarize_portfolio_scoring
from research_lab.robustness import summarize_weekly_robustness, write_weekly_robustness_outputs


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


if __name__ == "__main__":
    root = Path.cwd()
    apify_status = "skipped: APIFY_TOKEN is not set"
    apify_items = []
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
    portfolio = run_portfolio_scoring(root, report_stem)
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
        "",
        "## Robustness Findings",
        "",
        *_weekly_robustness_findings(robustness["robustness_rows"], robustness["stability_rows"]),
        "",
        "## Parameter Findings",
        "",
        *summarize_parameter_sweep(parameter_sweep["rows"]),
        "",
        "## Portfolio Findings",
        "",
        *summarize_portfolio_scoring(portfolio["rows"]),
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
        "- Add true rolling walk-forward windows using regenerated weights per window.",
        "- Expand parameter sweeps only after adding stronger data history; do not optimize on 5-year Massive data alone.",
        "- Add portfolio combination tests once real data-backed candidates exist.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"weekly report written: {report}")
