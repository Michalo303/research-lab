from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_lab.edge import classify_edge


def hypothesis_fingerprint(item: dict[str, Any]) -> str:
    family = _token(item.get("family", "unknown"))
    ticker = _token(item.get("ticker", ""))
    source_group = _source_group(item)
    edge_bucket = classify_edge(item)["edge_bucket"]
    title_family = _title_family(item)
    if ticker:
        return "|".join(["ticker", family, source_group, ticker, edge_bucket])
    return "|".join(["theme", family, source_group, edge_bucket, title_family])


def existing_hypothesis_fingerprints(path: Path) -> set[str]:
    return {hypothesis_fingerprint(item) for item in read_hypotheses(path)}


def read_hypotheses(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def dedupe_hypotheses(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept = []
    duplicates = []
    seen = set()
    for item in items:
        fingerprint = hypothesis_fingerprint(item)
        enriched = {**item, "dedupe_fingerprint": fingerprint}
        if fingerprint in seen:
            duplicates.append(enriched)
            continue
        kept.append(enriched)
        seen.add(fingerprint)
    return kept, duplicates


def audit_hypothesis_queue(root: Path, apply: bool = False) -> dict[str, Any]:
    path = root / "registry" / "hypothesis_queue.jsonl"
    items = read_hypotheses(path)
    kept, duplicates = dedupe_hypotheses(items)
    report_path = root / "reports" / "self_improvement" / "hypothesis_dedupe_audit.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path: Path | None = None
    if apply and path.exists():
        archive_path = path.with_name(f"{path.stem}.{_archive_stamp()}.before_dedupe{path.suffix}")
        archive_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with path.open("w", encoding="utf-8") as handle:
            for item in kept:
                handle.write(json.dumps(_without_runtime_fingerprint(item), sort_keys=True) + "\n")
    _write_report(report_path, len(items), kept, duplicates, apply, archive_path)
    return {
        "total": len(items),
        "kept": len(kept),
        "duplicates": len(duplicates),
        "report_path": report_path,
        "applied": apply,
        "archive_path": archive_path,
    }


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _source_group(item: dict[str, Any]) -> str:
    source_key = str(item.get("source_key", "")).lower()
    source_title = str(item.get("source_title", "")).lower()
    source_url = str(item.get("source_url", "")).lower()
    for value in (source_key, source_title, source_url):
        if value.startswith("smartmoney"):
            return "smartmoney"
        if "dataroma" in value or "13f" in value:
            return "filings"
        if "quantocracy" in value:
            return "quantocracy"
        if "arxiv" in value:
            return "arxiv"
        if value:
            return _token(value.split(":")[0].split("/")[2] if "://" in value and len(value.split("/")) > 2 else value.split(":")[0])
    return "unknown"


def _title_family(item: dict[str, Any]) -> str:
    title = str(item.get("title", ""))
    normalized = title.lower()
    replacements = {
        "regime-filtered": "momentum",
        "top-n": "momentum",
        "dual momentum": "momentum",
        "pullback": "pullback",
        "mean reversion": "pullback",
        "volatility-targeted": "volatility",
        "vwap": "intraday",
    }
    for phrase, family in replacements.items():
        if phrase in normalized:
            return family
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    return "-".join(tokens[:4]) or "untitled"


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower()).strip("-")


def _without_runtime_fingerprint(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    output.pop("dedupe_fingerprint", None)
    return output


def _write_report(
    path: Path,
    total: int,
    kept: list[dict[str, Any]],
    duplicates: list[dict[str, Any]],
    applied: bool,
    archive_path: Path | None,
) -> None:
    lines = [
        "# Hypothesis Dedupe Audit",
        "",
        f"- total hypotheses: {total}",
        f"- kept: {len(kept)}",
        f"- duplicates: {len(duplicates)}",
        f"- applied: {applied}",
        f"- archive_path: {archive_path or ''}",
        "",
        "## Duplicate Examples",
        "",
    ]
    for item in duplicates[:50]:
        lines.extend(
            [
                f"- {item.get('hypothesis_id', '')}: {item.get('title', '')}",
                f"  fingerprint: {item.get('dedupe_fingerprint', '')}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
