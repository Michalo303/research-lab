from pathlib import Path
from datetime import date
import csv


if __name__ == "__main__":
    root = Path.cwd()
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
