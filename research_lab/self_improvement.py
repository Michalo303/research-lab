from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from research_lab.edge import run_edge_audit, summarize_edge_audit


def run_self_improvement(root: Path) -> Path:
    leaderboard = _read_csv(root / "registry" / "leaderboard.csv")
    hypotheses = _read_jsonl(root / "registry" / "hypothesis_queue.jsonl")
    ideas = _read_jsonl(root / "registry" / "creative_ideas.jsonl")
    hypothesis_results = _read_jsonl(root / "registry" / "hypothesis_results.jsonl")
    edge_audit = run_edge_audit(root)
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
        f"- creative ideas: {len(ideas)}",
        f"- hypothesis-derived tests: {len(hypothesis_results)}",
        f"- edge audit csv: {edge_audit['csv_path']}",
        "- live trading permission: not present",
        "",
        "## Edge Audit",
        "",
        *summarize_edge_audit(edge_audit["rows"]),
        "",
        "## Weak Points",
        "",
        *[f"- {item}" for item in weak_points],
        "",
        "## Next Engineering Actions",
        "",
        "- Extend real data coverage and add longer-history EOD sources before treating long-term metrics as investment evidence.",
        "- Implement walk-forward windows and parameter-neighborhood stability grids.",
        "- Monitor source deduplication and add stronger semantic duplicate detection if RSS titles repeat with different URLs.",
        "- Add portfolio correlation scoring once at least two real-data candidates survive rejection.",
        "",
        "## Research Behavior Rules",
        "",
        "- Prefer simple hypotheses first.",
        "- Penalize every extra parameter.",
        "- Promote nothing from source/forum popularity alone.",
        "- A hypothesis without a named edge remains research-only even if a backtest looks good.",
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
    real_data_sources = {"yfinance", "massive"}
    if leaderboard and all(row.get("data_source") not in real_data_sources for row in leaderboard):
        points.append("Current leaderboard is synthetic-only; capital relevance is zero until real data ingestion runs.")
    if leaderboard and any(row.get("data_source") == "massive" for row in leaderboard):
        points.append("Massive real EOD data is running, but current history is still too short for long-term promotion.")
    if leaderboard and all(row.get("tier") == "Rejected" for row in leaderboard):
        points.append("All tested strategies are rejected; prioritize data quality and broader baseline coverage.")
    if len(hypotheses) < 5:
        points.append("Hypothesis queue is thin; enable controlled network scanning or add curated paper/forum sources.")
    if hypotheses and not hypothesis_results:
        points.append("Hypotheses exist but have not yet been converted into deterministic strategy tests.")
    return points or ["No critical weakness detected, but promotion still requires walk-forward and cost robustness."]
