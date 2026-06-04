from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_lab.queue_dedupe import audit_queue_file, candidate_fingerprint


def hypothesis_fingerprint(item: dict[str, Any]) -> str:
    return candidate_fingerprint(item)


def existing_hypothesis_fingerprints(path: Path) -> set[str]:
    fingerprints = set()
    for item in read_hypotheses(path):
        try:
            fingerprints.add(hypothesis_fingerprint(item))
        except ValueError:
            continue
    return fingerprints


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
    result = audit_items(items)
    kept = [_with_fingerprint(item) for item in result.retained_records]
    duplicates = [_with_fingerprint(item) for item in result.duplicate_records]
    return kept, duplicates


def audit_items(items: list[dict[str, Any]]):
    from research_lab.queue_dedupe import dedupe_candidates

    return dedupe_candidates(items)


def audit_hypothesis_queue(root: Path, apply: bool = False) -> dict[str, Any]:
    path = root / "registry" / "hypothesis_queue.jsonl"
    result = audit_queue_file(path, write=apply, backup_stamp=_archive_stamp() if apply else None)
    report_path = root / "reports" / "self_improvement" / "hypothesis_dedupe_audit.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, result)
    return {
        "total": result.input_count,
        "kept": result.retained_count,
        "duplicates": result.duplicate_count,
        "report_path": report_path,
        "applied": result.applied,
        "archive_path": result.backup_path,
        "malformed": result.malformed_count,
        "fingerprints_generated": result.fingerprints_generated,
        "duplicate_groups": result.duplicate_groups,
        "warnings": result.warnings,
    }


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _with_fingerprint(item: dict[str, Any]) -> dict[str, Any]:
    try:
        return {**item, "dedupe_fingerprint": hypothesis_fingerprint(item)}
    except ValueError:
        return dict(item)


def _write_report(path: Path, result) -> None:
    lines = [
        "# Hypothesis Dedupe Audit",
        "",
        f"- input_count: {result.input_count}",
        f"- retained_count: {result.retained_count}",
        f"- duplicate_count: {result.duplicate_count}",
        f"- malformed_count: {result.malformed_count}",
        f"- fingerprints_generated: {result.fingerprints_generated}",
        f"- applied: {result.applied}",
        f"- backup_path: {result.backup_path or ''}",
        f"- archive_path: {result.backup_path or ''}",
        "",
        "## Duplicate Groups",
        "",
    ]
    for group in result.duplicate_groups[:50]:
        lines.extend(
            [
                f"- fingerprint: {group.get('fingerprint', '')}",
                f"  retained_index: {group.get('retained_index', '')}",
                f"  duplicate_indices: {group.get('duplicate_indices', [])}",
                f"  reason: {group.get('reason', '')}",
            ]
        )
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings[:50])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
