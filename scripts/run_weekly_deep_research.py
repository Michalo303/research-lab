from pathlib import Path
from datetime import date
import csv
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.apify_dataroma import DEFAULT_SUPERINVESTORS, run_dataroma_actor


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
        "",
        "## Findings",
        "",
        "- Walk-forward and parameter-neighborhood stability are not implemented yet.",
        "- No deployment recommendation is allowed from this weekly placeholder.",
        "",
        "## Next Actions",
        "",
        "- Add rolling walk-forward windows for top non-rejected strategies.",
        "- Add parameter stability grids and cost-sensitivity summaries.",
        "- Add portfolio combination tests once real data-backed candidates exist.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"weekly report written: {report}")
