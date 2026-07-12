from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hermes_knowledge.books import load_book_index
from hermes_knowledge.schema import KnowledgeValidationError, load_knowledge_jsonl
from research_lab.execution.experiment_manifest_contract_v1 import (
    _canonical_sha256,
    _reject_unknown_fields,
    _required_mapping,
    _required_text,
    _validate_provenance,
)
from research_lab.orchestration.schemas import canonical_blockers


REQUEST_VERSION = "knihomol_readonly_evidence_adapter_request_v1"
RESULT_VERSION = "knihomol_readonly_evidence_adapter_result_v1"
ADAPTER_VERSION = "knihomol_readonly_evidence_adapter_v1"
_CANONICAL_NOTE_FILES = {
    "drawdown_fail": "drawdown_fail.jsonl",
    "walk_forward_fail": "walk_forward_fail.jsonl",
}


def build_knihomol_readonly_evidence_adapter(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    book_index_path = _validated_child_file(validated["corpus_base"], "index", "book_index.json")
    blocker_paths = {
        blocker: _validated_child_file(validated["corpus_base"], "extracted_notes", filename)
        for blocker, filename in _required_blocker_files(validated["requested_notes"]).items()
    }
    pre_hashes = _source_hashes(book_index_path=book_index_path, blocker_paths=blocker_paths)
    books = load_book_index(book_index_path)
    book_by_id = {book.book_id: book for book in books}
    notes_by_blocker = {
        blocker: _load_notes_for_blocker(path, blocker=blocker)
        for blocker, path in blocker_paths.items()
    }
    notes = _resolve_requested_notes(
        requested_notes=validated["requested_notes"],
        notes_by_blocker=notes_by_blocker,
        book_by_id=book_by_id,
    )
    post_hashes = _source_hashes(book_index_path=book_index_path, blocker_paths=blocker_paths)
    if post_hashes != pre_hashes:
        raise ValueError("source corpus files changed during loading.")

    return {
        "version": RESULT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "status": "SUCCESS",
        "evidence_purpose": validated["evidence_purpose"],
        "requested_note_ids": [item["note_id"] for item in validated["requested_notes"]],
        "notes": notes,
        "content_sha256": _canonical_sha256(notes),
        "source_hashes": pre_hashes,
        "corpus_files_unchanged": True,
        "writes_performed": False,
        "promotion_performed": False,
        "provider_calls_used": 0,
        "network_used": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "corpus_base", "requested_notes", "evidence_purpose", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return {
        "version": version,
        "corpus_base": _validated_corpus_base(_required_text(payload, "corpus_base")),
        "requested_notes": _validate_requested_notes(payload.get("requested_notes")),
        "evidence_purpose": _required_text(payload, "evidence_purpose"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_requested_notes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("requested_notes must be a non-empty list.")
    normalized: list[dict[str, str]] = []
    seen_note_ids: set[str] = set()
    allowed_blockers = canonical_blockers()
    for item in value:
        note = _required_mapping(item, name="requested_note")
        _reject_unknown_fields(
            note,
            allowed={"note_id", "blocker", "expected_book_id", "expected_source_sha256"},
            name="requested_note",
        )
        note_id = _required_text(note, "note_id")
        if note_id in seen_note_ids:
            raise ValueError("requested_notes.note_id values must be unique.")
        seen_note_ids.add(note_id)
        blocker = _required_text(note, "blocker")
        if blocker not in allowed_blockers:
            raise ValueError(f"requested_note.blocker is not a canonical blocker: {blocker}")
        if blocker not in _CANONICAL_NOTE_FILES:
            raise ValueError(f"requested_note.blocker is unsupported for read-only evidence loading: {blocker}")
        entry = {
            "note_id": note_id,
            "blocker": blocker,
            "expected_book_id": "",
            "expected_source_sha256": "",
        }
        if "expected_book_id" in note:
            entry["expected_book_id"] = _required_text(note, "expected_book_id")
        if "expected_source_sha256" in note:
            entry["expected_source_sha256"] = _required_sha256(note["expected_source_sha256"], name="expected_source_sha256")
        normalized.append(entry)
    return sorted(normalized, key=lambda item: (item["blocker"], item["note_id"]))


def _validated_corpus_base(raw_path: str) -> Path:
    path = Path(raw_path.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("corpus_base must be an absolute local path.")
    if path.is_symlink():
        raise ValueError("corpus_base symlinks are not allowed.")
    if not path.exists():
        raise ValueError("corpus_base does not exist.")
    if not path.is_dir():
        raise ValueError("corpus_base must be a directory.")
    return path.resolve()


def _validated_child_file(base: Path, *parts: str) -> Path:
    candidate = base.joinpath(*parts)
    if candidate.is_symlink():
        raise ValueError(f"symlink source is not allowed: {candidate.name}")
    if not candidate.exists():
        raise ValueError(f"required canonical file is missing: {candidate.name}")
    if not candidate.is_file():
        raise ValueError(f"canonical source must be a regular file: {candidate.name}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"symlink escape is not allowed: {candidate.name}") from exc
    return resolved


def _required_blocker_files(requested_notes: list[dict[str, str]]) -> dict[str, str]:
    return {
        blocker: _CANONICAL_NOTE_FILES[blocker]
        for blocker in sorted({item["blocker"] for item in requested_notes})
    }


def _source_hashes(*, book_index_path: Path, blocker_paths: dict[str, Path]) -> dict[str, str]:
    hashes = {"index/book_index.json": _file_sha256(book_index_path)}
    for blocker, path in sorted(blocker_paths.items()):
        hashes[f"extracted_notes/{_CANONICAL_NOTE_FILES[blocker]}"] = _file_sha256(path)
    return hashes


def _load_notes_for_blocker(path: Path, *, blocker: str) -> dict[str, dict[str, Any]]:
    try:
        rows = load_knowledge_jsonl(path)
    except KnowledgeValidationError as exc:
        raise ValueError(f"{path.name} is invalid: {exc}") from exc
    notes_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        note_id = _required_note_provenance(row, path=path)
        blockers = row.get("addresses_blockers")
        if blocker not in blockers:
            continue
        if note_id in notes_by_id:
            raise ValueError(f"{path.name} contains duplicate note_id: {note_id}")
        notes_by_id[note_id] = row
    return notes_by_id


def _required_note_provenance(note: dict[str, Any], *, path: Path) -> str:
    for field in ("note_id", "source_location", "source_passage_id", "implementation_hint"):
        if field not in note:
            raise ValueError(f"{path.name} note is missing required promoted provenance: {field}")
    return _required_text(note, "note_id")


def _resolve_requested_notes(
    *,
    requested_notes: list[dict[str, str]],
    notes_by_blocker: dict[str, dict[str, dict[str, Any]]],
    book_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for requested in requested_notes:
        note = notes_by_blocker[requested["blocker"]].get(requested["note_id"])
        if note is None:
            raise ValueError(
                f"requested note was not found in canonical blocker file: {requested['blocker']}::{requested['note_id']}"
            )
        book_id = _required_text(note, "book_id")
        if requested["expected_book_id"] and requested["expected_book_id"] != book_id:
            raise ValueError(f"requested note expected_book_id mismatch for {requested['note_id']}.")
        source_sha256 = _required_sha256(note.get("source_sha256"), name="source_sha256")
        if requested["expected_source_sha256"] and requested["expected_source_sha256"] != source_sha256:
            raise ValueError(f"requested note expected_source_sha256 mismatch for {requested['note_id']}.")
        indexed_book = book_by_id.get(book_id)
        if indexed_book is None:
            raise ValueError(f"book_index.json is missing requested note book_id: {book_id}")
        if indexed_book.source_sha256 != source_sha256:
            raise ValueError(f"book_index.json source_sha256 mismatch for requested note {requested['note_id']}.")
        resolved.append(
            {
                "note_id": requested["note_id"],
                "blocker": requested["blocker"],
                "book_id": book_id,
                "source_title": _required_text(note, "source_title"),
                "source_sha256": source_sha256,
                "source_passage_id": _required_text(note, "source_passage_id"),
                "source_location": _required_text(note, "source_location"),
                "testable_rules": _required_text_list(note.get("testable_rules"), name="testable_rules"),
                "compatible_builders": _required_text_list(note.get("compatible_builders"), name="compatible_builders"),
                "implementation_hint": _required_text(note, "implementation_hint"),
                "priority_score": _required_priority_score(note.get("priority_score")),
            }
        )
    return sorted(resolved, key=lambda item: (item["blocker"], item["note_id"]))


def _required_text_list(value: Any, *, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must contain non-empty text values.")
        normalized.append(item.strip())
    return normalized


def _required_priority_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("priority_score must be numeric.")
    score = float(value)
    if score < 0.0 or score > 100.0:
        raise ValueError("priority_score must be between 0 and 100.")
    return score


def _required_sha256(value: Any, *, name: str) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{name} must be a lowercase sha256 hex digest.")
    return text


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
