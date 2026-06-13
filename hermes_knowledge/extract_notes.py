"""Create bounded metadata-only note skeletons without reading PDF content."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from hermes_knowledge.books import BookRecord, load_book_index, select_top_books
from hermes_knowledge.schema import validate_entry


OUTPUT_FILENAME = "note_skeletons.jsonl"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def build_note_skeleton(book: BookRecord, topic: str) -> dict[str, object]:
    clean_topic = " ".join(topic.split()).strip()
    entry = {
        "book_id": book.book_id,
        "source_title": book.title,
        "source_path": f"private-book:{book.book_id}",
        "source_sha256": book.source_sha256,
        "concept": f"Manual review required: {clean_topic}",
        "hypothesis": (
            "This metadata-only skeleton must be replaced by a short, "
            "source-verified hypothesis before runtime use."
        ),
        "summary": "No PDF text has been extracted; this is a review placeholder.",
        "source_excerpt": "",
        "testable_rules": [
            "Add one short source-verified rule before raising priority above zero."
        ],
        "compatible_builders": ["manual_review_required"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "None until a reviewer verifies and curates this note.",
        "known_failure_modes": [
            "Metadata-only skeletons are not evidence and must not seed strategies."
        ],
        "addresses_blockers": [clean_topic],
        "priority_score": 0,
    }
    return validate_entry(entry)


def prepare_note_skeletons(
    book_index: str | Path,
    notes_dir: str | Path,
    topics: Sequence[str],
    *,
    max_books: int,
    max_notes_per_book: int,
    dry_run: bool,
) -> tuple[Path, list[dict[str, object]]]:
    if max_books < 1 or max_notes_per_book < 1:
        raise ValueError("max book and note counts must be positive")
    clean_topics = [" ".join(topic.split()).strip() for topic in topics]
    if not clean_topics or any(not topic or len(topic) > 100 for topic in clean_topics):
        raise ValueError("topics must contain non-empty values of at most 100 characters")

    output_dir = Path(notes_dir).resolve()
    if output_dir == SOURCE_ROOT or SOURCE_ROOT in output_dir.parents:
        raise ValueError("notes-dir must remain outside the source Git repository")
    output_path = output_dir / OUTPUT_FILENAME
    books = select_top_books(load_book_index(book_index), limit=max_books)
    entries = [
        build_note_skeleton(book, topic)
        for book in books
        for topic in clean_topics[:max_notes_per_book]
    ]
    if dry_run:
        return output_path, entries

    output_dir.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
    return output_path, entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare schema-valid metadata-only Hermes note skeletons."
    )
    parser.add_argument("--book-index", type=Path, required=True)
    parser.add_argument("--notes-dir", type=Path, required=True)
    parser.add_argument("--topics", nargs="+", required=True)
    parser.add_argument("--max-books", type=int, required=True)
    parser.add_argument("--max-notes-per-book", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    output_path, entries = prepare_note_skeletons(
        args.book_index,
        args.notes_dir,
        args.topics,
        max_books=args.max_books,
        max_notes_per_book=args.max_notes_per_book,
        dry_run=args.dry_run,
    )
    book_ids = sorted({str(entry["book_id"]) for entry in entries})
    print(
        f"notes={len(entries)} books={','.join(book_ids) or 'none'} "
        f"dry_run={str(args.dry_run).lower()} output={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
