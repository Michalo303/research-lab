from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from research_lab.registry import append_jsonl


CREATIVE_TEMPLATES = [
    {
        "title": "Cross-asset momentum with defensive cash switch",
        "family": "ROTATION",
        "tags": ["momentum", "risk"],
        "rationale": "Rank assets by medium-term momentum, but use a broad risk-off switch to avoid forcing exposure in hostile regimes.",
    },
    {
        "title": "Volatility-targeted trend participation",
        "family": "LONGTERM",
        "tags": ["trend", "volatility"],
        "rationale": "Stay in the main trend while sizing down during volatility expansion instead of exiting completely.",
    },
    {
        "title": "Pullback after volatility contraction",
        "family": "SWING",
        "tags": ["pullback", "volatility"],
        "rationale": "Look for trend pullbacks after a quieter volatility regime, then require recovery confirmation before entry.",
    },
    {
        "title": "Momentum crash filter",
        "family": "ROTATION",
        "tags": ["momentum", "drawdown"],
        "rationale": "Keep the rotation engine, but block new risk exposure when the benchmark is in a deep drawdown state.",
    },
    {
        "title": "Mean reversion only above long-term trend",
        "family": "SWING",
        "tags": ["mean_reversion", "trend"],
        "rationale": "Treat oversold signals as valid only when the broader trend is still positive.",
    },
    {
        "title": "Skew-aware defensive allocation",
        "family": "LONGTERM",
        "tags": ["skew", "risk"],
        "rationale": "Use downside-risk proxies to reduce exposure when negative-tail behavior rises.",
    },
    {
        "title": "Rebalance-day robustness rotation",
        "family": "ROTATION",
        "tags": ["robustness", "momentum"],
        "rationale": "Test whether a rotation edge survives different rebalance days instead of one lucky month-end convention.",
    },
    {
        "title": "Time-stop pullback strategy",
        "family": "SWING",
        "tags": ["pullback", "time_stop"],
        "rationale": "Exit if a pullback trade does not recover quickly, reducing capital trapped in slow failures.",
    },
]


def run_creative_research(root: Path, max_new: int = 12) -> list[dict]:
    source_items = _latest_source_items(root, limit=25)
    generated = []
    existing = _existing_idea_keys(root)
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    seeds = source_items or [{"title": "Internal seed: broaden strategy search", "url": "", "tags": ["general"]}]
    for source_idx, source in enumerate(seeds, start=1):
        for template_idx, template in enumerate(_templates_for_source(source), start=1):
            idea_key = _idea_key(source, template)
            if idea_key in existing:
                continue
            payload = {
                "idea_id": f"IDEA_{now}_{source_idx:02d}_{template_idx:02d}",
                "idea_key": idea_key,
                "title": template["title"],
                "family": template["family"],
                "rationale": f"{template['rationale']} Inspiration source: {source.get('title', 'internal seed')}.",
                "source_title": source.get("title", "internal seed"),
                "source_url": source.get("url", ""),
                "tags": sorted(set(template.get("tags", []) + source.get("tags", []))),
                "status": "creative_pool",
                "research_only": True,
            }
            append_jsonl(root / "registry" / "creative_ideas.jsonl", payload)
            generated.append(payload)
            existing.add(idea_key)
            if len(generated) >= max_new:
                _write_creative_report(root, generated)
                return generated

    _write_creative_report(root, generated)
    return generated


def promote_creative_ideas_to_hypotheses(root: Path, max_promotions: int = 6) -> list[dict]:
    ideas = _read_jsonl(root / "registry" / "creative_ideas.jsonl")
    existing_source_keys = _existing_hypothesis_source_keys(root)
    promoted = []
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for idea in reversed(ideas):
        source_key = f"creative:{idea['idea_key']}"
        if source_key in existing_source_keys:
            continue
        payload = {
            "hypothesis_id": f"HYP_{now}_CREATIVE_{len(promoted) + 1:03d}",
            "title": idea["title"],
            "family": idea["family"],
            "rationale": idea["rationale"],
            "source_title": idea["source_title"],
            "source_url": idea["source_url"],
            "source_key": source_key,
            "tags": idea["tags"],
            "status": "queued",
            "research_only": True,
            "creative_idea_id": idea["idea_id"],
        }
        append_jsonl(root / "registry" / "hypothesis_queue.jsonl", payload)
        promoted.append(payload)
        existing_source_keys.add(source_key)
        if len(promoted) >= max_promotions:
            break
    return list(reversed(promoted))


def _templates_for_source(source: dict) -> list[dict]:
    source_tags = set(source.get("tags", []))
    scored = []
    for template in CREATIVE_TEMPLATES:
        overlap = len(source_tags.intersection(template.get("tags", [])))
        scored.append((overlap, template["title"], template))
    scored.sort(reverse=True)
    return [item[2] for item in scored]


def _latest_source_items(root: Path, limit: int) -> list[dict]:
    return _read_jsonl(root / "registry" / "source_items.jsonl")[-limit:]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _existing_idea_keys(root: Path) -> set[str]:
    return {item.get("idea_key", "") for item in _read_jsonl(root / "registry" / "creative_ideas.jsonl")}


def _existing_hypothesis_source_keys(root: Path) -> set[str]:
    return {item.get("source_key", "") for item in _read_jsonl(root / "registry" / "hypothesis_queue.jsonl")}


def _idea_key(source: dict, template: dict) -> str:
    return f"{source.get('url') or source.get('title')}::{template['title']}".lower().replace(" ", "-")


def _write_creative_report(root: Path, ideas: list[dict]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = root / "reports" / "source_scans" / f"{stamp}-creative-ideas.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Creative Research Ideas - {stamp} UTC", "", f"- ideas generated: {len(ideas)}", ""]
    for idea in ideas:
        lines.extend(
            [
                f"## {idea['idea_id']}",
                "",
                f"- title: {idea['title']}",
                f"- family: {idea['family']}",
                f"- source: {idea['source_title']}",
                "",
                idea["rationale"],
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

