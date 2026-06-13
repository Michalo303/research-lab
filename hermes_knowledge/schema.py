"""Validate short hypothesis seeds and reject book-text-sized content."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


MAX_EXCERPT_CHARS = 280
MAX_SUMMARY_CHARS = 600
MAX_HYPOTHESIS_CHARS = 500
MAX_RULE_CHARS = 300
MAX_TOTAL_TEXT_CHARS = 2000

REQUIRED_FIELDS = {
    "book_id",
    "source_title",
    "source_path",
    "source_sha256",
    "concept",
    "hypothesis",
    "summary",
    "testable_rules",
    "compatible_builders",
    "asset_classes",
    "timeframes",
    "expected_edge",
    "known_failure_modes",
    "addresses_blockers",
    "priority_score",
}


class KnowledgeValidationError(ValueError):
    pass


def _require_short_text(entry: dict[str, Any], field: str, maximum: int) -> None:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeValidationError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise KnowledgeValidationError(f"{field} exceeds {maximum} characters")


def _require_text_list(entry: dict[str, Any], field: str) -> None:
    value = entry.get(field)
    if not isinstance(value, list) or not value:
        raise KnowledgeValidationError(f"{field} must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise KnowledgeValidationError(f"{field} must contain non-empty strings")


def validate_entry(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("knowledge entry must be an object")
    missing = sorted(REQUIRED_FIELDS - raw.keys())
    if missing:
        raise KnowledgeValidationError(f"missing required fields: {', '.join(missing)}")

    entry = dict(raw)
    for field, maximum in (
        ("book_id", 64),
        ("source_title", 300),
        ("source_path", 1000),
        ("concept", 200),
        ("hypothesis", MAX_HYPOTHESIS_CHARS),
        ("summary", MAX_SUMMARY_CHARS),
        ("expected_edge", 400),
    ):
        _require_short_text(entry, field, maximum)

    if not re.fullmatch(r"book-[0-9a-fA-F]{12}", entry["book_id"]):
        raise KnowledgeValidationError(
            "book_id must use book- followed by 12 hash characters"
        )

    excerpt = entry.get("source_excerpt", "")
    if not isinstance(excerpt, str):
        raise KnowledgeValidationError("source_excerpt must be text")
    if len(excerpt) > MAX_EXCERPT_CHARS:
        raise KnowledgeValidationError(
            f"source_excerpt exceeds {MAX_EXCERPT_CHARS} characters"
        )

    sha256 = entry.get("source_sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise KnowledgeValidationError("source_sha256 must be a 64-character hash")
    if entry["book_id"].casefold() != f"book-{sha256[:12]}".casefold():
        raise KnowledgeValidationError(
            "book_id must match the first 12 characters of source_sha256"
        )

    for field in (
        "testable_rules",
        "compatible_builders",
        "asset_classes",
        "timeframes",
        "known_failure_modes",
        "addresses_blockers",
    ):
        _require_text_list(entry, field)
    if any(len(rule) > MAX_RULE_CHARS for rule in entry["testable_rules"]):
        raise KnowledgeValidationError(
            f"testable_rules entries may not exceed {MAX_RULE_CHARS} characters"
        )

    priority = entry.get("priority_score")
    if not isinstance(priority, (int, float)) or isinstance(priority, bool):
        raise KnowledgeValidationError("priority_score must be numeric")
    if not 0 <= float(priority) <= 100:
        raise KnowledgeValidationError("priority_score must be between 0 and 100")
    entry["priority_score"] = float(priority)
    entry["source_sha256"] = sha256.lower()
    entry["source_excerpt"] = excerpt
    total_text_chars = sum(
        len(value)
        for key, value in entry.items()
        if key not in {"source_path", "source_sha256"}
        if isinstance(value, str)
    ) + sum(
        len(item)
        for value in entry.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, str)
    )
    if total_text_chars > MAX_TOTAL_TEXT_CHARS:
        raise KnowledgeValidationError(
            f"total text exceeds {MAX_TOTAL_TEXT_CHARS} characters"
        )
    return entry


def load_knowledge_jsonl(path: str | Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                entries.append(validate_entry(raw))
            except (json.JSONDecodeError, KnowledgeValidationError) as exc:
                raise KnowledgeValidationError(f"line {line_number}: {exc}") from exc
    return entries
