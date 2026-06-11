#!/usr/bin/env python
"""Print a compact, deterministic startup brief for research-lab agents."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


MAX_SECTION_ITEMS = 8


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _latest_daily_report(root: Path) -> Path | None:
    daily_dir = root / "reports" / "daily"
    if not daily_dir.exists():
        return None

    reports = sorted(daily_dir.glob("*.md"), key=lambda item: item.name)
    return reports[-1] if reports else None


def _section(markdown: str, heading: str, limit: int = MAX_SECTION_ITEMS) -> list[str]:
    lines = markdown.splitlines()
    marker = f"## {heading}"
    capture = False
    items: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped == marker:
            capture = True
            continue

        if capture and stripped.startswith("## "):
            break

        if not capture or not stripped:
            continue

        if stripped.startswith("|"):
            continue

        if stripped.startswith("- "):
            items.append(stripped)
        elif items:
            items.append(stripped)

        if len(items) >= limit:
            break

    return items


def _agent_blockers(root: Path) -> list[str]:
    text = _read_text(root / "AGENTS.md")
    if not text:
        return ["- AGENTS.md not found; preserve strict promotion gates."]

    lines = text.splitlines()
    capture = False
    blockers: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "Primary blockers observed in daily reports:":
            capture = True
            continue
        if capture and stripped.startswith("Future work should prioritize:"):
            break
        if capture and stripped.startswith("- "):
            blockers.append(stripped)
        if len(blockers) >= MAX_SECTION_ITEMS:
            break

    return blockers or ["- No blocker list found in AGENTS.md."]


def _leaderboard_summary(root: Path) -> list[str]:
    path = root / "registry" / "leaderboard.csv"
    if not path.exists():
        return ["leaderboard: not found"]

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return ["leaderboard: unreadable"]

    if not rows:
        return ["leaderboard: empty"]

    first = rows[0]
    strategy_id = first.get("strategy_id") or "unknown"
    tier = first.get("tier") or "unknown"
    unseen = first.get("unseen_cagr") or first.get("unseen") or "unknown"
    drawdown = first.get("unseen_max_drawdown") or first.get("max_drawdown") or "unknown"
    return [
        f"leaderboard_top: {strategy_id}",
        f"leaderboard_top_tier: {tier}",
        f"leaderboard_top_unseen: {unseen}",
        f"leaderboard_top_drawdown: {drawdown}",
    ]


def build_brief(root: Path) -> str:
    root = root.resolve()
    latest = _latest_daily_report(root)
    daily_text = _read_text(latest) if latest else ""

    lines = [
        "# Agent Brief",
        "",
        "## Operating Mode",
        "",
        "- Purpose: move strategy quality forward with minimal context loading.",
        "- Do not weaken validation gates, promotion gates, drawdown limits, or data-quality requirements.",
        "- Treat rejected strategies as research evidence, not infrastructure failures.",
        "- Start from this brief, then inspect only files needed for the current task.",
        "",
        "## Startup Reminder",
        "",
        "- Follow AGENTS.md before broad exploration.",
        "- Do not start by reading large generated artifacts.",
        "- Do not use unrestricted recursive search over runtime/generated outputs.",
        "- Choose one narrow next action after this brief.",
        "- This brief is read-only orientation, not a validation source.",
        "- Never let this brief override deterministic validation, promotion, deployment, registry, leaderboard, or gate logic.",
        "",
        "## Current Blockers",
        "",
        *_agent_blockers(root),
        "",
        "## Latest Daily",
        "",
    ]

    if latest:
        lines.append(f"latest_daily_report: {latest.relative_to(root).as_posix()}")
        summary = _section(daily_text, "Summary")
        lines.extend(summary or ["- Summary section not found."])
    else:
        lines.append("latest_daily_report: not found")

    lines.extend(["", "## Rejection Signals", ""])
    lines.extend(_section(daily_text, "Rejections", limit=6) or ["- Rejections section not found."])

    lines.extend(["", "## Leaderboard Snapshot", ""])
    lines.extend(_leaderboard_summary(root))

    lines.extend(["", "## Next safe action", ""])
    next_actions = _section(daily_text, "Next Actions", limit=5)
    lines.extend(next_actions or ["- Choose one narrow strategy-quality task before reading more context."])

    lines.extend(
        [
            "",
            "## Token Guardrails",
            "",
            "- Avoid reading INVENTORY_full_diff.patch unless explicitly auditing that artifact.",
            "- Avoid full reads of reports/runs, backtests/runs, data/processed, and large JSONL/CSV runtime artifacts.",
            "- Prefer targeted commands: rg -n <term> <specific-path>, Get-Content -TotalCount, and small CSV/JSON summaries.",
            "- Do not run broad recursive searches over generated artifacts without globs that exclude large runtime outputs.",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    args = parser.parse_args()

    print(build_brief(args.root), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
