"""Private evidence/proposal storage and explicit single-note promotion."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from hermes_knowledge.books import load_book_index
from hermes_knowledge.passage_extractor import PassageCandidate
from hermes_knowledge.schema import (
    KnowledgeValidationError,
    load_knowledge_jsonl,
    validate_proposed_note,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WriteSummary:
    written: int
    duplicates: int


@dataclass(frozen=True)
class ValidationSummary:
    valid: int
    invalid: int
    duplicates: int


def _ensure_private_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == SOURCE_ROOT or SOURCE_ROOT in resolved.parents:
        raise ValueError("private Hermes artifacts must remain outside the source repository")
    return resolved


def _atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path = _ensure_private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_raw_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        return []
    rows: list[Any] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_passage_candidates(
    path: str | Path, candidates: Iterable[PassageCandidate]
) -> WriteSummary:
    destination = Path(path)
    existing = _read_raw_jsonl(destination)
    rows = list(existing)
    seen = {str(row.get("passage_id", "")) for row in rows if isinstance(row, dict)}
    written = 0
    duplicates = 0
    for candidate in candidates:
        if candidate.passage_id in seen:
            duplicates += 1
            continue
        rows.append(candidate.to_dict())
        seen.add(candidate.passage_id)
        written += 1
    if written:
        _atomic_jsonl(destination, rows)
    return WriteSummary(written, duplicates)


def _semantic_fingerprint(entry: dict[str, Any]) -> str:
    payload = {
        "blockers": sorted(str(value).casefold() for value in entry["addresses_blockers"]),
        "hypothesis": " ".join(str(entry["hypothesis"]).casefold().split()),
        "rules": sorted(
            " ".join(str(value).casefold().split()) for value in entry["testable_rules"]
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def write_proposed_notes(
    path: str | Path, proposals: Iterable[dict[str, Any]]
) -> WriteSummary:
    destination = Path(path)
    existing = [validate_proposed_note(row) for row in _read_raw_jsonl(destination)]
    rows = list(existing)
    note_ids = {str(row["entry"]["note_id"]) for row in rows}
    fingerprints = {_semantic_fingerprint(row["entry"]) for row in rows}
    written = 0
    duplicates = 0
    for raw in proposals:
        proposal = validate_proposed_note(raw)
        note_id = str(proposal["entry"]["note_id"])
        fingerprint = _semantic_fingerprint(proposal["entry"])
        if note_id in note_ids or fingerprint in fingerprints:
            duplicates += 1
            continue
        rows.append(proposal)
        note_ids.add(note_id)
        fingerprints.add(fingerprint)
        written += 1
    if written:
        _atomic_jsonl(destination, rows)
    return WriteSummary(written, duplicates)


def validate_proposed_file(path: str | Path) -> ValidationSummary:
    note_ids: set[str] = set()
    fingerprints: set[str] = set()
    valid = 0
    invalid = 0
    duplicates = 0
    try:
        rows = _read_raw_jsonl(Path(path))
    except (OSError, json.JSONDecodeError):
        return ValidationSummary(0, 1, 0)
    for raw in rows:
        try:
            proposal = validate_proposed_note(raw)
        except (KnowledgeValidationError, TypeError, ValueError):
            invalid += 1
            continue
        note_id = str(proposal["entry"]["note_id"])
        fingerprint = _semantic_fingerprint(proposal["entry"])
        if note_id in note_ids or fingerprint in fingerprints:
            duplicates += 1
            continue
        note_ids.add(note_id)
        fingerprints.add(fingerprint)
        valid += 1
    return ValidationSummary(valid, invalid, duplicates)


def promote_note(
    note_id: str,
    *,
    proposed_path: str | Path,
    extracted_path: str | Path,
    book_index_path: str | Path,
) -> dict[str, Any]:
    proposals = [
        validate_proposed_note(row) for row in _read_raw_jsonl(Path(proposed_path))
    ]
    matches = [row for row in proposals if row["entry"]["note_id"] == note_id]
    if len(matches) != 1:
        raise ValueError(f"proposed note not found or not unique: {note_id}")
    entry = matches[0]["entry"]
    indexed_hashes = {
        book.book_id: book.source_sha256 for book in load_book_index(book_index_path)
    }
    if indexed_hashes.get(entry["book_id"]) != entry["source_sha256"]:
        raise ValueError("proposed note book hash does not match current index")
    destination = Path(extracted_path)
    existing = load_knowledge_jsonl(destination) if destination.exists() else []
    if any(row.get("note_id") == note_id for row in existing):
        raise ValueError(f"note already promoted: {note_id}")
    _atomic_jsonl(destination, [*existing, entry])
    return entry
