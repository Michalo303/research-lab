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
MIN_LIST_ITEMS = 1
MAX_LIST_ITEMS = 12
CANDIDATE_BLOCKER_MAX_CHARS = 100
CANDIDATE_SAFE_FIELDS = (
    "note_id",
    "source_location",
    "source_passage_id",
    "blocker_tags",
    "thesis",
    "evidence_summary",
    "risk_control_hint",
)

LIST_ITEM_MAX_CHARS = {
    "testable_rules": MAX_RULE_CHARS,
    "compatible_builders": 100,
    "asset_classes": 100,
    "timeframes": 50,
    "known_failure_modes": 300,
    "addresses_blockers": 100,
}

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
OPTIONAL_FIELDS = {
    "source_excerpt",
    "note_id",
    "source_location",
    "source_passage_id",
    "implementation_hint",
    "blocker_tags",
    "thesis",
    "evidence_summary",
    "risk_control_hint",
}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

FORBIDDEN_PROMPT_MARKERS = (
    "/opt/trading/private/",
    "hermes_books/raw",
    ".pdf",
    "file://",
)
PROMPT_BOUND_FIELDS = (
    "source_title",
    "concept",
    "hypothesis",
    "summary",
    "source_excerpt",
    "testable_rules",
    "compatible_builders",
    "asset_classes",
    "timeframes",
    "expected_edge",
    "known_failure_modes",
    "addresses_blockers",
    "rationale",
    "tags",
    "topics",
    "source_reference",
    "source_location",
    "implementation_hint",
)


class KnowledgeValidationError(ValueError):
    pass


def contains_forbidden_prompt_reference(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.casefold().replace("\\", "/")
        return any(marker in normalized for marker in FORBIDDEN_PROMPT_MARKERS)
    if isinstance(value, (list, tuple)):
        return any(contains_forbidden_prompt_reference(item) for item in value)
    return False


def forbidden_prompt_reference_field(raw: dict[str, Any]) -> str | None:
    for field in PROMPT_BOUND_FIELDS:
        if field in raw and contains_forbidden_prompt_reference(raw[field]):
            return field
    return None


def _require_short_text(entry: dict[str, Any], field: str, maximum: int) -> None:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeValidationError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise KnowledgeValidationError(f"{field} exceeds {maximum} characters")


def _require_text_list(
    entry: dict[str, Any], field: str, maximum_item_chars: int
) -> None:
    value = entry.get(field)
    if not isinstance(value, list):
        raise KnowledgeValidationError(f"{field} must be an array")
    if not MIN_LIST_ITEMS <= len(value) <= MAX_LIST_ITEMS:
        raise KnowledgeValidationError(
            f"{field} must contain between {MIN_LIST_ITEMS} and "
            f"{MAX_LIST_ITEMS} items"
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise KnowledgeValidationError(f"{field} must contain non-empty strings")
    if any(len(item) > maximum_item_chars for item in value):
        raise KnowledgeValidationError(
            f"{field} entries may not exceed {maximum_item_chars} characters"
        )


def validate_entry(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("knowledge entry must be an object")
    forbidden_field = forbidden_prompt_reference_field(raw)
    if forbidden_field:
        raise KnowledgeValidationError(
            f"forbidden reference in {forbidden_field}"
        )
    missing = sorted(REQUIRED_FIELDS - raw.keys())
    if missing:
        raise KnowledgeValidationError(f"missing required fields: {', '.join(missing)}")
    unexpected = sorted(raw.keys() - ALLOWED_FIELDS)
    if unexpected:
        raise KnowledgeValidationError(
            f"unexpected fields: {', '.join(unexpected)}"
        )

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

    for field, maximum in (
        ("note_id", 64),
        ("source_location", 100),
        ("source_passage_id", 64),
        ("implementation_hint", 300),
        ("thesis", MAX_HYPOTHESIS_CHARS),
        ("evidence_summary", MAX_SUMMARY_CHARS),
        ("risk_control_hint", 300),
    ):
        if field in entry:
            _require_short_text(entry, field, maximum)

    if "note_id" in entry and not re.fullmatch(
        r"note-[0-9a-fA-F]{16}", entry["note_id"]
    ):
        raise KnowledgeValidationError("note_id must use note- followed by 16 hash characters")
    if "source_passage_id" in entry and not re.fullmatch(
        r"passage-[0-9a-fA-F]{16}", entry["source_passage_id"]
    ):
        raise KnowledgeValidationError(
            "source_passage_id must use passage- followed by 16 hash characters"
        )

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

    for field, maximum_item_chars in LIST_ITEM_MAX_CHARS.items():
        _require_text_list(entry, field, maximum_item_chars)
    if "blocker_tags" in entry:
        _require_text_list(entry, "blocker_tags", 100)

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


def validate_reextract_candidate_entry(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("candidate note must be an object")
    forbidden_field = forbidden_prompt_reference_field(raw)
    if forbidden_field:
        raise KnowledgeValidationError(f"forbidden reference in {forbidden_field}")
    missing = sorted(set(CANDIDATE_SAFE_FIELDS) - raw.keys())
    if missing:
        raise KnowledgeValidationError(
            f"missing candidate fields: {', '.join(missing)}"
        )
    unexpected = sorted(raw.keys() - set(CANDIDATE_SAFE_FIELDS))
    if unexpected:
        raise KnowledgeValidationError(
            f"unexpected candidate fields: {', '.join(unexpected)}"
        )

    entry = dict(raw)
    for field, maximum in (
        ("note_id", 64),
        ("source_location", 100),
        ("source_passage_id", 64),
        ("thesis", MAX_HYPOTHESIS_CHARS),
        ("evidence_summary", MAX_SUMMARY_CHARS),
        ("risk_control_hint", 300),
    ):
        _require_short_text(entry, field, maximum)

    if not re.fullmatch(r"note-[0-9a-fA-F]{16}", entry["note_id"]):
        raise KnowledgeValidationError(
            "note_id must use note- followed by 16 hash characters"
        )
    if not re.fullmatch(r"passage-[0-9a-fA-F]{16}", entry["source_passage_id"]):
        raise KnowledgeValidationError(
            "source_passage_id must use passage- followed by 16 hash characters"
        )

    _require_text_list(entry, "blocker_tags", CANDIDATE_BLOCKER_MAX_CHARS)
    total_text_chars = sum(
        len(value) for value in entry.values() if isinstance(value, str)
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


def validate_proposed_note(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("proposed note must be an object")
    allowed = {"status", "source_passage_id", "entry"}
    unexpected = sorted(raw.keys() - allowed)
    if unexpected:
        raise KnowledgeValidationError(
            f"unexpected proposed note fields: {', '.join(unexpected)}"
        )
    if raw.get("status") != "proposed":
        raise KnowledgeValidationError("proposed note status must be proposed")
    source_passage_id = raw.get("source_passage_id")
    if not isinstance(source_passage_id, str) or not re.fullmatch(
        r"passage-[0-9a-fA-F]{16}", source_passage_id
    ):
        raise KnowledgeValidationError("source_passage_id is required")
    entry = validate_entry(raw.get("entry"))
    required_provenance = {
        "note_id",
        "source_location",
        "source_passage_id",
        "implementation_hint",
    }
    missing = sorted(required_provenance - entry.keys())
    if missing:
        raise KnowledgeValidationError(
            f"missing proposed note provenance: {', '.join(missing)}"
        )
    if entry["source_passage_id"] != source_passage_id:
        raise KnowledgeValidationError("source_passage_id does not match entry")
    return {
        "status": "proposed",
        "source_passage_id": source_passage_id,
        "entry": entry,
    }


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
