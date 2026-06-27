"""Fail-open loading of short, validated private-book research notes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from hermes_knowledge.books import load_book_index
from hermes_knowledge.blocker_taxonomy import canonicalize_blocker_id
from hermes_knowledge.prompt import build_hermes_knowledge_prompt
from hermes_knowledge.retriever import retrieve_for_blocker
from hermes_knowledge.schema import KnowledgeValidationError, load_knowledge_jsonl, validate_entry


DEFAULT_BOOK_INDEX_PATH = Path(
    "/opt/trading/private/hermes_books/index/book_index.json"
)
DEFAULT_BOOK_NOTES_DIR = Path(
    "/opt/trading/private/hermes_books/extracted_notes"
)


@dataclass(frozen=True)
class BookKnowledgeContext:
    prompt: str = ""
    note_count: int = 0
    skipped_note_count: int = 0
    selected_book_ids: tuple[str, ...] = ()
    selected_note_ids: tuple[str, ...] = ()
    canonical_blocker_id: str = ""
    blocker_diagnostic: str = ""


@dataclass(frozen=True)
class NoteInventoryAudit:
    total_note_rows: int = 0
    current_format_note_rows: int = 0
    legacy_note_rows: int = 0
    rows_with_note_id: int = 0
    rows_with_source_location: int = 0
    rows_with_source_passage_id: int = 0
    rows_with_blocker_tags: int = 0
    normalized_blocker_counts: dict[str, int] | None = None
    unknown_blocker_ids: dict[str, int] | None = None
    rows_eligible_for_provenance_aware_retrieval: int = 0
    rows_excluded_from_promoted_used_note_ids: int = 0
    feedback_overlay_present: bool = False
    ready_for_new_knihomol_hypothesis_generation: bool = False


def _normalize_retrieval_blocker_id(raw: str) -> str | None:
    normalized = str(raw).strip().casefold()
    if normalized == "drawdown":
        return "drawdown"
    if normalized == "walk_forward_robustness":
        return "walk_forward_robustness"
    if normalized == "cost_stress":
        return "cost_stress"
    canonical = canonicalize_blocker_id(raw)
    if canonical == "drawdown_fail":
        return "drawdown"
    if canonical == "walk_forward_fail":
        return "walk_forward_robustness"
    return canonical


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_provenance(entry: dict[str, Any]) -> bool:
    return all(
        _has_text(entry.get(field))
        for field in ("note_id", "source_location", "source_passage_id")
    )


def _has_recognized_blocker_tag(entry: dict[str, Any]) -> bool:
    blockers = entry.get("addresses_blockers")
    if not isinstance(blockers, list):
        return False
    return any(
        isinstance(blocker, str) and _normalize_retrieval_blocker_id(blocker) is not None
        for blocker in blockers
    )


def _is_promoted_evidence_eligible(entry: dict[str, Any]) -> bool:
    return _has_provenance(entry) and _has_recognized_blocker_tag(entry)


def _blocker_diagnostic(raw: str, normalized: str) -> str:
    raw_value = str(raw).strip().casefold()
    accepted_exact = {
        normalized,
        "drawdown_fail" if normalized == "drawdown" else "",
        "walk_forward_fail" if normalized == "walk_forward_robustness" else "",
    }
    return "exact" if raw_value in accepted_exact else "canonicalized"


def audit_note_inventory(notes_dir: str | Path) -> NoteInventoryAudit:
    notes_path = Path(notes_dir)
    if notes_path.name.casefold() != "extracted_notes" or not notes_path.is_dir():
        return NoteInventoryAudit(
            normalized_blocker_counts={},
            unknown_blocker_ids={},
        )
    normalized_counts: Counter[str] = Counter()
    unknown_counts: Counter[str] = Counter()
    total_note_rows = 0
    current_format_note_rows = 0
    rows_with_note_id = 0
    rows_with_source_location = 0
    rows_with_source_passage_id = 0
    rows_with_blocker_tags = 0
    rows_eligible = 0
    excluded_from_promoted = 0
    for path in sorted(notes_path.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            total_note_rows += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                excluded_from_promoted += 1
                continue
            if not isinstance(raw, dict):
                excluded_from_promoted += 1
                continue
            if _has_text(raw.get("note_id")):
                rows_with_note_id += 1
            if _has_text(raw.get("source_location")):
                rows_with_source_location += 1
            if _has_text(raw.get("source_passage_id")):
                rows_with_source_passage_id += 1
            blockers = raw.get("addresses_blockers")
            parsed_blockers = []
            if isinstance(blockers, list):
                parsed_blockers = [
                    str(item).strip()
                    for item in blockers
                    if isinstance(item, str) and item.strip()
                ]
            if parsed_blockers:
                rows_with_blocker_tags += 1
                for blocker in parsed_blockers:
                    normalized = _normalize_retrieval_blocker_id(blocker)
                    if normalized is None:
                        unknown_counts[blocker] += 1
                    else:
                        normalized_counts[normalized] += 1
            try:
                validate_entry(raw)
                current_format_note_rows += 1
            except KnowledgeValidationError:
                excluded_from_promoted += 1
                continue
            if _is_promoted_evidence_eligible(raw):
                rows_eligible += 1
            else:
                excluded_from_promoted += 1
    legacy_note_rows = total_note_rows - current_format_note_rows
    feedback_overlay_present = (notes_path.parent / "feedback" / "priorities.json").exists()
    ready = bool(
        total_note_rows
        and current_format_note_rows == total_note_rows
        and excluded_from_promoted == 0
        and not unknown_counts
        and feedback_overlay_present
    )
    return NoteInventoryAudit(
        total_note_rows=total_note_rows,
        current_format_note_rows=current_format_note_rows,
        legacy_note_rows=legacy_note_rows,
        rows_with_note_id=rows_with_note_id,
        rows_with_source_location=rows_with_source_location,
        rows_with_source_passage_id=rows_with_source_passage_id,
        rows_with_blocker_tags=rows_with_blocker_tags,
        normalized_blocker_counts=dict(sorted(normalized_counts.items())),
        unknown_blocker_ids=dict(sorted(unknown_counts.items())),
        rows_eligible_for_provenance_aware_retrieval=rows_eligible,
        rows_excluded_from_promoted_used_note_ids=excluded_from_promoted,
        feedback_overlay_present=feedback_overlay_present,
        ready_for_new_knihomol_hypothesis_generation=ready,
    )


def _priority_overlays(notes_dir: Path) -> dict[str, float]:
    path = notes_dir.parent / "feedback" / "priorities.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        notes = payload.get("notes", {})
        if not isinstance(notes, dict):
            return {}
        return {
            str(note_id): float(value)
            for note_id, value in notes.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def load_book_knowledge_context(
    book_index_path: str | Path = DEFAULT_BOOK_INDEX_PATH,
    notes_dir: str | Path = DEFAULT_BOOK_NOTES_DIR,
    *,
    dominant_blocker: str,
    limit: int = 5,
) -> BookKnowledgeContext:
    """Return bounded prompt context, or an empty context on unavailable input."""
    try:
        canonical_blocker = _normalize_retrieval_blocker_id(dominant_blocker)
        if canonical_blocker is None:
            return BookKnowledgeContext(blocker_diagnostic="unrecognized_blocker")
        books = load_book_index(book_index_path)
        indexed_hashes = {book.book_id: book.source_sha256 for book in books}
        notes_path = Path(notes_dir)
        if notes_path.name.casefold() != "extracted_notes":
            return BookKnowledgeContext()
        if not notes_path.is_dir():
            return BookKnowledgeContext()
        entries = []
        skipped_note_count = 0
        for path in sorted(notes_path.glob("*.jsonl")):
            try:
                line_count = sum(
                    1
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except OSError:
                line_count = 1
            try:
                candidates = load_knowledge_jsonl(path)
            except (OSError, KnowledgeValidationError, ValueError):
                skipped_note_count += line_count
                continue
            for entry in candidates:
                if float(entry["priority_score"]) <= 0:
                    skipped_note_count += 1
                    continue
                if indexed_hashes.get(entry["book_id"]) != entry["source_sha256"]:
                    skipped_note_count += 1
                    continue
                entries.append(entry)
        if not entries:
            return BookKnowledgeContext(skipped_note_count=skipped_note_count)
        selected = retrieve_for_blocker(
            entries,
            canonical_blocker,
            limit=limit,
            note_priority_overlays=_priority_overlays(notes_path),
        )
        if not selected:
            return BookKnowledgeContext(skipped_note_count=skipped_note_count)
        prompt = build_hermes_knowledge_prompt(
            selected,
            dominant_blocker=canonical_blocker,
            limit=len(selected),
        )
        return BookKnowledgeContext(
            prompt=prompt,
            note_count=len(selected),
            skipped_note_count=skipped_note_count,
            selected_book_ids=tuple(
                dict.fromkeys(str(entry["book_id"]) for entry in selected)
            ),
            selected_note_ids=tuple(
                dict.fromkeys(
                    str(entry["note_id"])
                    for entry in selected
                    if entry.get("note_id") and _is_promoted_evidence_eligible(entry)
                )
            ),
            canonical_blocker_id=canonical_blocker,
            blocker_diagnostic=_blocker_diagnostic(dominant_blocker, canonical_blocker),
        )
    except (OSError, KeyError, TypeError, ValueError):
        return BookKnowledgeContext()
