"""Fail-open loading of short, validated private-book research notes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from hermes_knowledge.books import load_book_index
from hermes_knowledge.prompt import build_hermes_knowledge_prompt
from hermes_knowledge.retriever import retrieve_for_blocker
from hermes_knowledge.schema import (
    KnowledgeValidationError,
    load_knowledge_jsonl,
    validate_entry,
    validate_reextract_candidate_entry,
)


DEFAULT_BOOK_INDEX_PATH = Path(
    "/opt/trading/private/hermes_books/index/book_index.json"
)
DEFAULT_BOOK_NOTES_DIR = Path(
    "/opt/trading/private/hermes_books/extracted_notes"
)
FEEDBACK_OVERLAY_RELATIVE_PATH = "feedback/priorities.json"
EXCLUDED_BY_REASON_KEYS = (
    "legacy_format",
    "missing_note_id",
    "missing_source_location",
    "missing_source_passage_id",
    "no_recognized_blocker",
    "unknown_only_blockers",
)
MISSING_FIELD_KEYS = (
    "note_id",
    "source_location",
    "source_passage_id",
)
CURRENT_FORMAT_REQUIRED_FIELDS = (
    "book_id",
    "source_title",
    "source_path",
    "source_sha256",
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
    "priority_score",
    "implementation_hint",
)
REEXTRACTION_TARGET_SCHEMA_REQUIRED_FIELDS = (
    "note_id",
    "source_location",
    "source_passage_id",
    "blocker_tags",
    "thesis",
    "evidence_summary",
    "risk_control_hint",
)
BACKFILL_REASON_KEYS = (
    "legacy_format",
    "missing_source_file_metadata",
    "ambiguous_source_location",
    "missing_passage_anchor",
    "duplicate_candidate_identity",
)
BACKFILL_FIELD_KEYS = (
    "note_id",
    "source_location",
    "source_passage_id",
)
NOTE_ID_PROVIDER_FIELDS = (
    "concept",
    "hypothesis",
    "summary",
    "testable_rules",
    "compatible_builders",
    "asset_classes",
    "timeframes",
    "expected_edge",
    "known_failure_modes",
    "implementation_hint",
    "priority_score",
)
SOURCE_FILE_METADATA_FIELDS = (
    "book_id",
    "source_title",
    "source_path",
    "source_sha256",
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
    excluded_by_reason: dict[str, int] | None = None
    missing_field_counts: dict[str, int] | None = None
    canonical_blocker_preview: dict[str, int] | None = None
    feedback_overlay_present: bool = False
    feedback_overlay_expected_path: str = FEEDBACK_OVERLAY_RELATIVE_PATH
    remediation_readiness: str = "blocked"
    remediation_remaining_blockers: dict[str, int] | None = None
    ready_for_new_knihomol_hypothesis_generation: bool = False


@dataclass(frozen=True)
class NoteProvenanceBackfillPlan:
    total_rows: int = 0
    rows_missing_note_id: int = 0
    rows_missing_source_location: int = 0
    rows_missing_source_passage_id: int = 0
    rows_with_deterministic_source_file_metadata: int = 0
    rows_with_deterministic_passage_id_source: int = 0
    rows_backfillable_all_required_fields: int = 0
    rows_not_backfillable: int = 0
    not_backfillable_reasons: dict[str, int] | None = None
    proposed_backfill_fields: dict[str, int] | None = None
    safety_verdict: tuple[str, str, str] = (
        "plan_only",
        "no_write_performed",
        "generation_still_blocked",
    )


@dataclass(frozen=True)
class NoteReextractionPlan:
    existing_total_rows: int = 0
    existing_provenance_complete_rows: int = 0
    existing_unsalvageable_rows: int = 0
    candidate_source_count: int = 0
    rows_with_book_id: int = 0
    rows_missing_book_id: int = 0
    rows_with_ambiguous_source_identity: int = 0
    candidate_blocker_counts: dict[str, int] | None = None
    target_schema_required_fields: tuple[str, ...] = REEXTRACTION_TARGET_SCHEMA_REQUIRED_FIELDS
    future_write_required: bool = True
    current_pr_write_allowed: bool = False
    provider_required_for_future_execution: bool = True
    current_pr_provider_calls_allowed: bool = False
    generation_still_blocked: bool = True
    next_execution_mode: str = "separate_explicit_reextraction_pr"


@dataclass(frozen=True)
class ControlledReextractionRunPlan:
    command: str = "reextract-run"
    dry_run: bool = True
    aborted: bool = False
    abort_reason: str = "none"
    provider_allowed: bool = False
    provider_name_provided: bool = False
    provider_name_redacted: bool = True
    model_name_provided: bool = False
    model_name_redacted: bool = True
    provider_attempted: bool = False
    provider_calls_used: int = 0
    max_books: int = 0
    max_passages_per_book: int = 0
    max_notes: int = 0
    max_provider_calls: int = 0
    output_path: str = ""
    timestamped_output_required: bool = True
    overwrite_allowed: bool = False
    notes_generated: int = 0
    notes_written: int = 0
    notes_schema_valid: int = 0
    notes_schema_invalid: int = 0
    post_generation_audit_required: bool = True
    post_generation_audit_run: bool = False
    candidate_readiness: str = "blocked"
    promotion_allowed: bool = False
    queue_insertion_allowed: bool = False
    generation_still_blocked: bool = True
    active_generation_still_blocked: bool = True


@dataclass(frozen=True)
class ReextractCandidateReview:
    review_valid: bool = False
    total_candidates: int = 0
    valid_candidates: int = 0
    invalid_candidates: int = 0
    duplicate_note_ids: tuple[str, ...] = ()
    blocker_tags_seen: tuple[str, ...] = ()
    promotion_allowed: bool = False
    queue_insertion_allowed: bool = False
    active_generation_still_blocked: bool = True


def _normalize_retrieval_blocker_id(raw: str) -> str | None:
    normalized = str(raw).strip().casefold()
    if normalized == "drawdown":
        return "drawdown"
    if normalized == "walk_forward_robustness":
        return "walk_forward_robustness"
    if normalized == "cost_stress":
        return "cost_stress"
    if normalized == "drawdown_fail":
        return "drawdown"
    if normalized == "walk_forward_fail":
        return "walk_forward_robustness"
    return None


def review_reextract_candidate_file(path: str | Path) -> ReextractCandidateReview:
    candidate_path = Path(path)
    total_candidates = 0
    valid_candidates = 0
    invalid_candidates = 0
    seen_note_ids: set[str] = set()
    duplicate_note_ids: set[str] = set()
    blocker_tags_seen: set[str] = set()
    with candidate_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total_candidates += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                invalid_candidates += 1
                continue
            try:
                entry = validate_reextract_candidate_entry(raw)
            except KnowledgeValidationError:
                invalid_candidates += 1
                continue

            blockers = tuple(
                blocker
                for blocker in entry["blocker_tags"]
                if _normalize_retrieval_blocker_id(blocker) is not None
            )
            blocker_tags_seen.update(blockers)
            if len(blockers) != len(entry["blocker_tags"]):
                invalid_candidates += 1
                continue

            note_id = entry["note_id"]
            if note_id in seen_note_ids:
                duplicate_note_ids.add(note_id)
                invalid_candidates += 1
                continue
            seen_note_ids.add(note_id)
            valid_candidates += 1

    return ReextractCandidateReview(
        review_valid=(invalid_candidates == 0 and total_candidates > 0),
        total_candidates=total_candidates,
        valid_candidates=valid_candidates,
        invalid_candidates=invalid_candidates,
        duplicate_note_ids=tuple(sorted(duplicate_note_ids)),
        blocker_tags_seen=tuple(sorted(blocker_tags_seen)),
    )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _blank_reason_fields(entry: dict[str, Any]) -> tuple[str, ...]:
    missing = []
    for field in MISSING_FIELD_KEYS:
        if not _has_text(entry.get(field)):
            missing.append(field)
    return tuple(missing)


def _has_provenance(entry: dict[str, Any]) -> bool:
    return not _blank_reason_fields(entry)


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


def _looks_like_current_format(entry: dict[str, Any]) -> bool:
    return all(field in entry for field in CURRENT_FORMAT_REQUIRED_FIELDS)


def _has_source_file_metadata(entry: dict[str, Any]) -> bool:
    return all(_has_text(entry.get(field)) for field in SOURCE_FILE_METADATA_FIELDS)


def _deterministic_note_id_identity(entry: dict[str, Any]) -> str | None:
    book_id = entry.get("book_id")
    source_passage_id = entry.get("source_passage_id")
    if not (_has_text(book_id) and _has_text(source_passage_id)):
        return None
    blockers = entry.get("addresses_blockers")
    if not isinstance(blockers, list):
        return None
    raw_blockers = [str(item).strip() for item in blockers if isinstance(item, str) and item.strip()]
    if len(raw_blockers) != 1:
        return None
    provider_note = {}
    for field in NOTE_ID_PROVIDER_FIELDS:
        if field not in entry:
            return None
        provider_note[field] = entry[field]
    normalized = json.dumps(provider_note, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(
        "\n".join((raw_blockers[0], str(book_id), str(source_passage_id), normalized)).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"note-{digest[:16]}"


def _previewable_unknown_blockers(
    parsed_blockers: list[str],
    *,
    has_provenance: bool,
    has_recognized_blocker: bool,
    is_current_format: bool,
) -> tuple[str, ...]:
    if not (has_provenance and not has_recognized_blocker and is_current_format):
        return ()
    previewable = []
    for blocker in parsed_blockers:
        if _normalize_retrieval_blocker_id(blocker) is not None:
            continue
        normalized = blocker.strip().casefold()
        if re.fullmatch(r"[a-z0-9_]+", normalized):
            previewable.append(normalized)
    return tuple(dict.fromkeys(previewable))


def _blocker_diagnostic(raw: str, normalized: str) -> str:
    raw_value = str(raw).strip().casefold()
    return "exact" if raw_value == normalized else "canonicalized"


def audit_note_inventory(
    notes_dir: str | Path,
    *,
    feedback_overlay_required: bool = True,
) -> NoteInventoryAudit:
    notes_path = Path(notes_dir)
    candidate_file_mode = False
    if notes_path.is_dir() and notes_path.name.casefold() == "extracted_notes":
        candidate_paths = sorted(notes_path.glob("*.jsonl"))
        feedback_root = notes_path.parent
    elif notes_path.is_file() and notes_path.suffix.casefold() == ".jsonl":
        candidate_paths = [notes_path]
        feedback_root = notes_path.parent
        candidate_file_mode = True
    else:
        return NoteInventoryAudit(
            normalized_blocker_counts={},
            unknown_blocker_ids={},
        )
    normalized_counts: Counter[str] = Counter()
    unknown_counts: Counter[str] = Counter()
    preview_counts: Counter[str] = Counter()
    total_note_rows = 0
    current_format_note_rows = 0
    rows_with_note_id = 0
    rows_with_source_location = 0
    rows_with_source_passage_id = 0
    rows_with_blocker_tags = 0
    rows_eligible = 0
    excluded_from_promoted = 0
    excluded_by_reason = {key: 0 for key in EXCLUDED_BY_REASON_KEYS}
    missing_field_counts = {key: 0 for key in MISSING_FIELD_KEYS}
    for path in candidate_paths:
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
            missing_fields = _blank_reason_fields(raw)
            for field in missing_fields:
                missing_field_counts[field] += 1
            if _has_text(raw.get("note_id")):
                rows_with_note_id += 1
            if _has_text(raw.get("source_location")):
                rows_with_source_location += 1
            if _has_text(raw.get("source_passage_id")):
                rows_with_source_passage_id += 1
            blockers = raw.get("blocker_tags") if candidate_file_mode else raw.get("addresses_blockers")
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
            has_recognized_blocker = bool(parsed_blockers) and any(
                _normalize_retrieval_blocker_id(blocker) is not None
                for blocker in parsed_blockers
            )
            has_unknown_blocker = bool(parsed_blockers) and any(
                _normalize_retrieval_blocker_id(blocker) is None
                for blocker in parsed_blockers
            )
            has_provenance = not missing_fields
            is_current_format = candidate_file_mode or _looks_like_current_format(raw)
            for blocker in _previewable_unknown_blockers(
                parsed_blockers,
                has_provenance=has_provenance,
                has_recognized_blocker=has_recognized_blocker,
                is_current_format=is_current_format,
            ):
                preview_counts[blocker] += 1
            try:
                if candidate_file_mode:
                    validate_reextract_candidate_entry(raw)
                else:
                    validate_entry(raw)
                current_format_note_rows += 1
            except KnowledgeValidationError:
                if not is_current_format:
                    excluded_by_reason["legacy_format"] += 1
                if "note_id" in missing_fields:
                    excluded_by_reason["missing_note_id"] += 1
                if "source_location" in missing_fields:
                    excluded_by_reason["missing_source_location"] += 1
                if "source_passage_id" in missing_fields:
                    excluded_by_reason["missing_source_passage_id"] += 1
                if parsed_blockers and not has_recognized_blocker:
                    excluded_by_reason["no_recognized_blocker"] += 1
                if parsed_blockers and has_unknown_blocker and not has_recognized_blocker:
                    excluded_by_reason["unknown_only_blockers"] += 1
                excluded_from_promoted += 1
                continue
            if candidate_file_mode:
                rows_eligible += 1
            elif _is_promoted_evidence_eligible(raw):
                rows_eligible += 1
            else:
                if "note_id" in missing_fields:
                    excluded_by_reason["missing_note_id"] += 1
                if "source_location" in missing_fields:
                    excluded_by_reason["missing_source_location"] += 1
                if "source_passage_id" in missing_fields:
                    excluded_by_reason["missing_source_passage_id"] += 1
                if parsed_blockers and not has_recognized_blocker:
                    excluded_by_reason["no_recognized_blocker"] += 1
                if parsed_blockers and has_unknown_blocker and not has_recognized_blocker:
                    excluded_by_reason["unknown_only_blockers"] += 1
                excluded_from_promoted += 1
    legacy_note_rows = total_note_rows - current_format_note_rows
    feedback_overlay_present = (feedback_root / "feedback" / "priorities.json").exists()
    remediation_remaining_blockers = {
        **excluded_by_reason,
        "feedback_overlay_missing": 0 if (feedback_overlay_present or not feedback_overlay_required) else 1,
    }
    ready = bool(
        total_note_rows
        and current_format_note_rows == total_note_rows
        and excluded_from_promoted == 0
        and not unknown_counts
        and (feedback_overlay_present or not feedback_overlay_required)
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
        excluded_by_reason=excluded_by_reason,
        missing_field_counts=missing_field_counts,
        canonical_blocker_preview=dict(sorted(preview_counts.items())),
        feedback_overlay_present=feedback_overlay_present,
        feedback_overlay_expected_path=FEEDBACK_OVERLAY_RELATIVE_PATH,
        remediation_readiness="ready" if ready else "blocked",
        remediation_remaining_blockers=remediation_remaining_blockers,
        ready_for_new_knihomol_hypothesis_generation=ready,
    )


def plan_note_provenance_backfill(notes_dir: str | Path) -> NoteProvenanceBackfillPlan:
    notes_path = Path(notes_dir)
    if notes_path.name.casefold() != "extracted_notes" or not notes_path.is_dir():
        return NoteProvenanceBackfillPlan(
            not_backfillable_reasons={key: 0 for key in BACKFILL_REASON_KEYS},
            proposed_backfill_fields={key: 0 for key in BACKFILL_FIELD_KEYS},
        )

    reason_counts = {key: 0 for key in BACKFILL_REASON_KEYS}
    proposed_counts = {key: 0 for key in BACKFILL_FIELD_KEYS}
    total_rows = 0
    rows_missing_note_id = 0
    rows_missing_source_location = 0
    rows_missing_source_passage_id = 0
    rows_with_deterministic_source_file_metadata = 0
    rows_with_deterministic_passage_id_source = 0
    rows_not_backfillable = 0
    candidate_rows: dict[str, int] = {}

    for path in sorted(notes_path.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            total_rows += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                rows_not_backfillable += 1
                reason_counts["legacy_format"] += 1
                continue
            if not isinstance(raw, dict):
                rows_not_backfillable += 1
                reason_counts["legacy_format"] += 1
                continue

            missing_fields = _blank_reason_fields(raw)
            if "note_id" in missing_fields:
                rows_missing_note_id += 1
            if "source_location" in missing_fields:
                rows_missing_source_location += 1
            if "source_passage_id" in missing_fields:
                rows_missing_source_passage_id += 1
            if not missing_fields:
                continue
            if not _looks_like_current_format(raw):
                rows_not_backfillable += 1
                reason_counts["legacy_format"] += 1
                continue
            if not _has_source_file_metadata(raw):
                rows_not_backfillable += 1
                reason_counts["missing_source_file_metadata"] += 1
                continue

            rows_with_deterministic_source_file_metadata += 1
            if _has_text(raw.get("source_passage_id")):
                rows_with_deterministic_passage_id_source += 1
            if "source_passage_id" in missing_fields:
                rows_not_backfillable += 1
                reason_counts["missing_passage_anchor"] += 1
                continue
            if "source_location" in missing_fields:
                rows_not_backfillable += 1
                reason_counts["ambiguous_source_location"] += 1
                continue
            if missing_fields != ("note_id",):
                rows_not_backfillable += 1
                reason_counts["duplicate_candidate_identity"] += 1
                continue

            candidate_identity = _deterministic_note_id_identity(raw)
            if candidate_identity is None:
                rows_not_backfillable += 1
                reason_counts["duplicate_candidate_identity"] += 1
                continue
            candidate_rows[candidate_identity] = candidate_rows.get(candidate_identity, 0) + 1

    rows_backfillable_all_required_fields = 0
    for count in candidate_rows.values():
        if count == 1:
            rows_backfillable_all_required_fields += 1
            proposed_counts["note_id"] += 1
        else:
            rows_not_backfillable += count
            reason_counts["duplicate_candidate_identity"] += count

    return NoteProvenanceBackfillPlan(
        total_rows=total_rows,
        rows_missing_note_id=rows_missing_note_id,
        rows_missing_source_location=rows_missing_source_location,
        rows_missing_source_passage_id=rows_missing_source_passage_id,
        rows_with_deterministic_source_file_metadata=rows_with_deterministic_source_file_metadata,
        rows_with_deterministic_passage_id_source=rows_with_deterministic_passage_id_source,
        rows_backfillable_all_required_fields=rows_backfillable_all_required_fields,
        rows_not_backfillable=rows_not_backfillable,
        not_backfillable_reasons=reason_counts,
        proposed_backfill_fields=proposed_counts,
    )


def plan_note_reextraction(notes_dir: str | Path) -> NoteReextractionPlan:
    notes_path = Path(notes_dir)
    if notes_path.name.casefold() != "extracted_notes" or not notes_path.is_dir():
        return NoteReextractionPlan(candidate_blocker_counts={})

    existing_total_rows = 0
    existing_provenance_complete_rows = 0
    existing_unsalvageable_rows = 0
    rows_with_book_id = 0
    rows_missing_book_id = 0
    rows_with_ambiguous_source_identity = 0
    blocker_counts: Counter[str] = Counter()
    source_signatures: dict[str, set[tuple[str, str, str]]] = {}

    for path in sorted(notes_path.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            existing_total_rows += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                existing_unsalvageable_rows += 1
                continue
            if not isinstance(raw, dict):
                existing_unsalvageable_rows += 1
                continue

            missing_fields = _blank_reason_fields(raw)
            is_current_format = _looks_like_current_format(raw)
            has_book_id = _has_text(raw.get("book_id"))
            if not missing_fields and is_current_format:
                existing_provenance_complete_rows += 1
                continue

            existing_unsalvageable_rows += 1
            if has_book_id:
                rows_with_book_id += 1
            else:
                rows_missing_book_id += 1
            if not is_current_format:
                continue
            if not has_book_id:
                continue

            blockers = raw.get("addresses_blockers")
            if isinstance(blockers, list):
                seen_for_row: set[str] = set()
                for blocker in blockers:
                    if not isinstance(blocker, str) or not blocker.strip():
                        continue
                    normalized = _normalize_retrieval_blocker_id(blocker)
                    if normalized is None or normalized in seen_for_row:
                        continue
                    blocker_counts[normalized] += 1
                    seen_for_row.add(normalized)

            if _has_source_file_metadata(raw):
                book_id = str(raw["book_id"]).strip()
                signature = (
                    str(raw.get("source_title", "")).strip(),
                    str(raw.get("source_path", "")).strip(),
                    str(raw.get("source_sha256", "")).strip(),
                )
                signatures = source_signatures.setdefault(book_id, set())
                signatures.add(signature)

    candidate_source_count = 0
    for signatures in source_signatures.values():
        if len(signatures) == 1:
            candidate_source_count += 1
        else:
            rows_with_ambiguous_source_identity += 1

    return NoteReextractionPlan(
        existing_total_rows=existing_total_rows,
        existing_provenance_complete_rows=existing_provenance_complete_rows,
        existing_unsalvageable_rows=existing_unsalvageable_rows,
        candidate_source_count=candidate_source_count,
        rows_with_book_id=rows_with_book_id,
        rows_missing_book_id=rows_missing_book_id,
        rows_with_ambiguous_source_identity=rows_with_ambiguous_source_identity,
        candidate_blocker_counts=dict(sorted(blocker_counts.items())),
    )


def plan_controlled_reextraction_run(
    *,
    output_path: str | Path | None = None,
    max_books: int = 0,
    max_passages_per_book: int = 0,
    max_notes: int = 0,
    max_provider_calls: int = 0,
    dry_run: bool = True,
    allow_provider_calls: bool = False,
    provider: str = "",
    model: str = "",
    overwrite_requested: bool = False,
    promotion_requested: bool = False,
    queue_insertion_requested: bool = False,
) -> ControlledReextractionRunPlan:
    output = "" if output_path is None else str(output_path).strip()
    provider_name = str(provider).strip()
    model_name = str(model).strip()
    plan = ControlledReextractionRunPlan(
        dry_run=bool(dry_run),
        provider_allowed=bool(allow_provider_calls),
        provider_name_provided=bool(provider_name),
        model_name_provided=bool(model_name),
        max_books=int(max_books),
        max_passages_per_book=int(max_passages_per_book),
        max_notes=int(max_notes),
        max_provider_calls=int(max_provider_calls),
        output_path=output,
    )

    if not output:
        return ControlledReextractionRunPlan(**{**plan.__dict__, "aborted": True, "abort_reason": "output_path_required"})
    if not dry_run:
        return ControlledReextractionRunPlan(**{**plan.__dict__, "aborted": True, "abort_reason": "dry_run_required"})
    if overwrite_requested:
        return ControlledReextractionRunPlan(**{**plan.__dict__, "aborted": True, "abort_reason": "overwrite_forbidden"})
    if promotion_requested:
        return ControlledReextractionRunPlan(**{**plan.__dict__, "aborted": True, "abort_reason": "promotion_forbidden"})
    if queue_insertion_requested:
        return ControlledReextractionRunPlan(
            **{
                **plan.__dict__,
                "aborted": True,
                "abort_reason": "queue_insertion_forbidden",
            }
        )
    if max_provider_calls > 0 and not allow_provider_calls:
        return ControlledReextractionRunPlan(
            **{
                **plan.__dict__,
                "aborted": True,
                "abort_reason": "allow_provider_calls_required",
                "provider_allowed": False,
                "provider_attempted": False,
                "provider_calls_used": 0,
            }
        )
    if not allow_provider_calls:
        return plan
    if not provider_name:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "provider_required"}
        )
    if not model_name:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "model_required"}
        )
    if max_provider_calls != 1:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "max_provider_calls_must_equal_one"}
        )
    if max_books != 1:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "max_books_must_equal_one"}
        )
    if max_passages_per_book not in {1, 3}:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "max_passages_per_book_invalid"}
        )
    if max_notes not in {1, 3}:
        return ControlledReextractionRunPlan(
            **{**plan.__dict__, "aborted": True, "abort_reason": "max_notes_invalid"}
        )
    return plan


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
