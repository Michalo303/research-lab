from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from research_lab.registry import append_jsonl


KEYWORDS = {
    "momentum": ["momentum", "trend", "relative strength", "cross-sectional"],
    "mean_reversion": ["mean reversion", "reversal", "overreaction", "pullback"],
    "volatility": ["volatility", "risk parity", "vol targeting", "drawdown"],
    "intraday": ["intraday", "vwap", "market microstructure", "high frequency"],
    "macro": ["macro", "rates", "inflation", "carry", "currency"],
    "ml": ["machine learning", "neural", "transformer", "random forest", "boosting"],
}


def run_source_scan(root: Path) -> dict:
    config_path = root / "config" / "research_sources.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    network_enabled = os.getenv("RESEARCH_LAB_NETWORK", "0") == "1"
    items: list[dict] = []
    skipped: list[dict] = []

    seen_keys = _existing_keys(root / "registry" / "source_items.jsonl", "dedupe_key")
    for source in _enabled_sources(config):
        if source["kind"] == "manual_watchlist":
            skipped.append({"source": source["name"], "reason": "manual watchlist; no scraping"})
            continue
        if not network_enabled:
            skipped.append({"source": source["name"], "reason": "network disabled"})
            continue
        try:
            if source["kind"] == "arxiv":
                items.extend(_fetch_arxiv(source))
            elif source["kind"] == "rss":
                items.extend(_fetch_rss(source))
        except Exception as exc:
            skipped.append({"source": source["name"], "reason": f"fetch failed: {exc}"})

    now = datetime.now(timezone.utc).isoformat()
    new_items = []
    for item in items:
        if item["dedupe_key"] in seen_keys:
            continue
        append_jsonl(root / "registry" / "source_items.jsonl", {**item, "scanned_at": now})
        seen_keys.add(item["dedupe_key"])
        new_items.append(item)

    report = _write_scan_report(root, new_items, skipped, network_enabled)
    return {"items": new_items, "skipped": skipped, "report": str(report)}


def generate_hypotheses_from_sources(root: Path, max_items: int = 10) -> list[dict]:
    source_path = root / "registry" / "source_items.jsonl"
    if source_path.exists():
        source_items = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        source_items = source_items[-max_items:]
    else:
        source_items = []
    if not source_items:
        source_items = _seed_items()

    hypotheses = []
    existing_hypotheses = _existing_keys(root / "registry" / "hypothesis_queue.jsonl", "source_key")
    for idx, item in enumerate(source_items, start=1):
        source_key = item.get("dedupe_key") or _dedupe_key(item.get("title", ""), item.get("url", ""))
        if source_key in existing_hypotheses:
            continue
        tags = _tags_for_text(f"{item.get('title', '')} {item.get('summary', '')}")
        hypothesis = _hypothesis_for_tags(tags, item)
        payload = {
            "hypothesis_id": f"HYP_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{idx:03d}",
            "title": hypothesis["title"],
            "family": hypothesis["family"],
            "rationale": hypothesis["rationale"],
            "source_title": item.get("title", "seed"),
            "source_url": item.get("url", ""),
            "source_key": source_key,
            "tags": tags,
            "status": "queued",
            "research_only": True,
        }
        append_jsonl(root / "registry" / "hypothesis_queue.jsonl", payload)
        hypotheses.append(payload)
    _write_hypothesis_report(root, hypotheses)
    return hypotheses


def _enabled_sources(config: dict) -> Iterable[dict]:
    for group in ("paper_sources", "watchlist_sources", "forum_sources"):
        for source in config.get(group, []):
            if source.get("enabled", False):
                yield source


def _fetch_arxiv(source: dict) -> list[dict]:
    text = _download(source["url"])
    root = ET.fromstring(text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("atom:entry", ns):
        title = _clean(entry.findtext("atom:title", default="", namespaces=ns))
        summary = _clean(entry.findtext("atom:summary", default="", namespaces=ns))
        url = entry.findtext("atom:id", default="", namespaces=ns)
        published = entry.findtext("atom:published", default="", namespaces=ns)
        items.append(_item(source, title, summary, url, published))
    return items


def _fetch_rss(source: dict) -> list[dict]:
    text = _download(source["url"])
    root = ET.fromstring(text)
    items = []
    for entry in root.findall(".//item")[:25]:
        title = _clean(entry.findtext("title", default=""))
        summary = _clean(entry.findtext("description", default=""))
        url = entry.findtext("link", default="")
        published = entry.findtext("pubDate", default="")
        items.append(_item(source, title, summary, url, published))
    return items


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    with urllib.request.urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _item(source: dict, title: str, summary: str, url: str, published: str) -> dict:
    title = _clean(title)
    summary = _clean(summary)
    return {
        "source": source["name"],
        "kind": source["kind"],
        "title": title,
        "summary": summary[:1000],
        "url": url,
        "published": published,
        "tags": _tags_for_text(f"{title} {summary}"),
        "dedupe_key": _dedupe_key(title, url),
    }


def _tags_for_text(text: str) -> list[str]:
    lowered = text.lower()
    tags = [tag for tag, words in KEYWORDS.items() if any(word in lowered for word in words)]
    return tags or ["general"]


def _hypothesis_for_tags(tags: list[str], item: dict) -> dict:
    title = item.get("title", "seed idea")
    if "momentum" in tags:
        return {
            "title": "Regime-filtered momentum rotation",
            "family": "ROTATION",
            "rationale": f"Test whether the idea suggested by '{title}' improves top-N rotation after drawdown and volatility filters.",
        }
    if "mean_reversion" in tags:
        return {
            "title": "Trend-filtered pullback mean reversion",
            "family": "SWING",
            "rationale": f"Convert '{title}' into a pullback strategy with explicit trend, stop, and time-exit rules.",
        }
    if "intraday" in tags:
        return {
            "title": "Intraday VWAP reclaim with cost stress",
            "family": "INTRADAY",
            "rationale": f"Use '{title}' only as inspiration, then require fills to survive one- and two-tick adverse stress.",
        }
    if "volatility" in tags:
        return {
            "title": "Volatility-targeted defensive allocation",
            "family": "LONGTERM",
            "rationale": f"Test whether '{title}' can reduce max drawdown without relying on CAGR-only optimization.",
        }
    return {
        "title": "Simple baseline variant with strict out-of-sample gate",
        "family": "LONGTERM",
        "rationale": f"Translate '{title}' into the simplest measurable rule before trying complex variants.",
    }


def _seed_items() -> list[dict]:
    return [
        {
            "title": "Seed: momentum with volatility targeting",
            "summary": "Internal seed used when network source scanning is disabled.",
            "url": "",
            "tags": ["momentum", "volatility"],
        },
        {
            "title": "Seed: pullback inside positive trend",
            "summary": "Internal seed used when network source scanning is disabled.",
            "url": "",
            "tags": ["mean_reversion"],
        },
        {
            "title": "Seed: VWAP reclaim after washout",
            "summary": "Internal seed used when network source scanning is disabled.",
            "url": "",
            "tags": ["intraday"],
        },
    ]


def _write_scan_report(root: Path, items: list[dict], skipped: list[dict], network_enabled: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = root / "reports" / "source_scans" / f"{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Source Scan - {stamp} UTC",
        "",
        f"- network enabled: {network_enabled}",
        f"- items collected: {len(items)}",
        f"- sources skipped: {len(skipped)}",
        "",
        "## Collected Items",
        "",
    ]
    for item in items[:25]:
        lines.append(f"- {item['source']}: {item['title']} ({item['url']})")
    lines.extend(["", "## Skipped Sources", ""])
    for item in skipped:
        lines.append(f"- {item['source']}: {item['reason']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_hypothesis_report(root: Path, hypotheses: list[dict]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = root / "reports" / "source_scans" / f"{stamp}-hypotheses.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Hypothesis Queue Update - {stamp} UTC", ""]
    for item in hypotheses:
        lines.extend(
            [
                f"## {item['hypothesis_id']}",
                "",
                f"- title: {item['title']}",
                f"- family: {item['family']}",
                f"- status: {item['status']}",
                f"- source: {item['source_title']}",
                "",
                item["rationale"],
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _clean(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    replacements = {
        "â€”": "-",
        "â€“": "-",
        "â€˜": "'",
        "â€™": "'",
        "â€œ": '"',
        "â€�": '"',
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned


def _dedupe_key(title: str, url: str) -> str:
    key = url.strip().lower() or title.strip().lower()
    return re.sub(r"[^a-z0-9:/._-]+", "-", key).strip("-")


def _existing_keys(path: Path, field: str) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = item.get(field)
        if not key and field == "dedupe_key":
            key = _dedupe_key(item.get("title", ""), item.get("url", ""))
        if not key and field == "source_key":
            key = _dedupe_key(item.get("source_title", ""), item.get("source_url", ""))
        if key:
            keys.add(str(key))
    return keys
