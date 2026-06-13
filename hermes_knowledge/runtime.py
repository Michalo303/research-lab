"""Fail-open loading of short, validated private-book research notes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hermes_knowledge.books import load_book_index
from hermes_knowledge.prompt import build_hermes_knowledge_prompt
from hermes_knowledge.retriever import retrieve_for_blocker
from hermes_knowledge.schema import KnowledgeValidationError, load_knowledge_jsonl


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
    selected_book_ids: tuple[str, ...] = ()


def load_book_knowledge_context(
    book_index_path: str | Path = DEFAULT_BOOK_INDEX_PATH,
    notes_dir: str | Path = DEFAULT_BOOK_NOTES_DIR,
    *,
    dominant_blocker: str,
    limit: int = 5,
) -> BookKnowledgeContext:
    """Return bounded prompt context, or an empty context on unavailable input."""
    try:
        books = load_book_index(book_index_path)
        indexed_hashes = {book.book_id: book.source_sha256 for book in books}
        notes_path = Path(notes_dir)
        if not notes_path.is_dir():
            return BookKnowledgeContext()
        entries = []
        for path in sorted(notes_path.glob("*.jsonl")):
            try:
                candidates = load_knowledge_jsonl(path)
            except (OSError, KnowledgeValidationError, ValueError):
                continue
            for entry in candidates:
                if float(entry["priority_score"]) <= 0:
                    continue
                if indexed_hashes.get(entry["book_id"]) != entry["source_sha256"]:
                    continue
                entries.append(entry)
        if not entries:
            return BookKnowledgeContext()
        selected = retrieve_for_blocker(entries, dominant_blocker, limit=limit)
        if not selected:
            return BookKnowledgeContext()
        prompt = build_hermes_knowledge_prompt(
            selected,
            dominant_blocker=dominant_blocker,
            limit=len(selected),
        )
        return BookKnowledgeContext(
            prompt=prompt,
            note_count=len(selected),
            selected_book_ids=tuple(
                dict.fromkeys(str(entry["book_id"]) for entry in selected)
            ),
        )
    except (OSError, KeyError, TypeError, ValueError):
        return BookKnowledgeContext()
