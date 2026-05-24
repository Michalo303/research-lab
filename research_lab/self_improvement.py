from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path


def run_self_improvement(root: Path) -> Path:
    leaderboard = _read_csv(root / "registry" / "leaderboard.csv")
    hypotheses = _read_jsonl(root / "registry" / "hypothesis_queue.jsonl")
    hypothesis_results = _read_jsonl(root / "registry" / "hypothesis_results.jsonl")
    rejected = [row for row in leaderboard if row.get("tier") == "Rejected"]
    weak_points = _weak_points(leaderboard, hypotheses, hypothesis_results)
    report = root / "reports" / "self_improvement" / f"{date.today().isoformat()}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Self-Improvement Cycle - {date.today().isoformat()}",
        "",
        "## Current State",
        "",
        f"- leaderboard rows: {len(leaderboard)}",
        f"- rejected strategies: {len(rejected)}",
        f"- queued hypotheses: {len(hypotheses)}",
        f"- hypothesis-derived tests: {len(hypothesis_results)}",
        "- live trading permission: not present",
        "",
        "## Weak Points",
        "",
        *[f"- {item}" for item in weak_points],
        "",
        "## Next Engineering Actions",
        "",
        "- Implement real data adapters before treating any performance metric as investment evidence.",
        "- Implement walk-forward windows and parameter-neighborhood stability grids.",
        "- Monitor source deduplication and add stronger semantic duplicate detection if RSS titles repeat with different URLs.",
        "- Add portfolio correlation scoring once at least two real-data candidates survive rejection.",
        "",
        "## Research Behavior Rules",
        "",
        "- Prefer simple hypotheses first.",
        "- Penalize every extra parameter.",
        "- Promote nothing from source/forum popularity alone.",
        "- Preserve failures because they are training data for the lab.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _weak_points(leaderboard: list[dict], hypotheses: list[dict], hypothesis_results: list[dict]) -> list[str]:
    points = []
    if not leaderboard:
        points.append("No leaderboard exists yet; daily research must run first.")
    if leaderboard and all(row.get("data_source") != "yfinance" for row in leaderboard):
        points.append("Current leaderboard is synthetic-only; capital relevance is zero until real data ingestion runs.")
    if leaderboard and all(row.get("tier") == "Rejected" for row in leaderboard):
        points.append("All tested strategies are rejected; prioritize data quality and broader baseline coverage.")
    if len(hypotheses) < 5:
        points.append("Hypothesis queue is thin; enable controlled network scanning or add curated paper/forum sources.")
    if hypotheses and not hypothesis_results:
        points.append("Hypotheses exist but have not yet been converted into deterministic strategy tests.")
    return points or ["No critical weakness detected, but promotion still requires walk-forward and cost robustness."]
