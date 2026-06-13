"""CLI for blocker-first private-book evidence and note lifecycle operations."""

from __future__ import annotations

import argparse
from collections import Counter
import os
import json
from pathlib import Path
from typing import Mapping

from hermes_knowledge.book_selector import (
    MAX_BOOKS,
    load_text_previews,
    select_books_for_blocker,
)
from hermes_knowledge.books import load_book_index
from hermes_knowledge.feedback import (
    apply_feedback,
    load_priority_overlays,
    note_book_map,
)
from hermes_knowledge.note_generator import ProviderInvoker, generate_proposed_notes
from hermes_knowledge.note_store import (
    promote_note,
    validate_proposed_file,
    write_passage_candidates,
    write_proposed_notes,
)
from hermes_knowledge.passage_extractor import (
    MAX_PASSAGES_PER_BOOK,
    extract_passages,
)
from research_lab.hermes.providers import invoke_provider


DEFAULT_BASE_DIR = Path("/opt/trading/private/hermes_books")


def _bounded_int(name: str, maximum: int):
    def parse(value: str) -> int:
        number = int(value)
        if not 1 <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be between 1 and {maximum}"
            )
        return number

    return parse


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    base = Path(args.base_dir)
    blocker = str(args.blocker)
    return (
        Path(getattr(args, "book_index", None) or base / "index" / "book_index.json"),
        Path(getattr(args, "text_dir", None) or base / "text"),
        base / "passage_candidates" / f"{blocker}.jsonl",
        base / "proposed_notes" / f"{blocker}.jsonl",
        base / "extracted_notes" / f"{blocker}.jsonl",
    )


def _extract(
    args: argparse.Namespace,
    env: Mapping[str, str],
    provider_invoker: ProviderInvoker,
) -> int:
    index_path, text_dir, candidate_path, proposed_path, _ = _paths(args)
    books = load_book_index(index_path)
    overlays = load_priority_overlays(Path(args.base_dir) / "feedback" / "priorities.json")
    previews = load_text_previews(books, text_dir)
    selected = select_books_for_blocker(
        books,
        args.blocker,
        limit=args.limit_books,
        text_previews=previews,
        book_priority_overlays=overlays["books"],
    )
    candidates, extraction_diagnostics = extract_passages(
        selected,
        args.blocker,
        text_dir=text_dir,
        passages_per_book=args.passages_per_book,
    )
    proposals, generation_diagnostics = generate_proposed_notes(
        candidates,
        provider=str(env.get("HERMES_PROVIDER", "")).strip().casefold(),
        env=env,
        provider_invoker=provider_invoker,
    )
    candidate_result = write_passage_candidates(candidate_path, candidates)
    proposal_result = write_proposed_notes(proposed_path, proposals)
    diagnostic_counts = Counter(
        item.code for item in [*extraction_diagnostics, *generation_diagnostics]
    )
    diagnostic_summary = ",".join(
        f"{code}:{count}" for code, count in sorted(diagnostic_counts.items())
    ) or "none"
    print(
        " ".join(
            [
                f"selected_books={len(selected)}",
                f"passages={candidate_result.written}",
                f"proposed={proposal_result.written}",
                f"duplicates={candidate_result.duplicates + proposal_result.duplicates}",
                f"skipped={len(extraction_diagnostics) + len(generation_diagnostics)}",
                f"diagnostics={diagnostic_summary}",
            ]
        )
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    _, _, _, proposed_path, _ = _paths(args)
    summary = validate_proposed_file(proposed_path)
    print(
        f"valid={summary.valid} invalid={summary.invalid} duplicates={summary.duplicates}"
    )
    return 0 if summary.invalid == 0 else 1


def _promote(args: argparse.Namespace) -> int:
    index_path, _, _, proposed_path, extracted_path = _paths(args)
    entry = promote_note(
        args.note_id,
        proposed_path=proposed_path,
        extracted_path=extracted_path,
        book_index_path=index_path,
    )
    print(f"promoted={entry['note_id']} blocker={args.blocker}")
    return 0


def _feedback(args: argparse.Namespace) -> int:
    base = Path(args.base_dir)
    events = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = apply_feedback(
        events,
        note_to_book=note_book_map(base / "extracted_notes"),
        event_path=base / "feedback" / "note_feedback.jsonl",
        priorities_path=base / "feedback" / "priorities.json",
    )
    print(
        f"accepted={summary.accepted} rejected={summary.rejected} "
        f"duplicates={summary.duplicates}"
    )
    return 0 if summary.rejected == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes blocker-first book learning agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract")
    extract.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    extract.add_argument("--book-index", type=Path)
    extract.add_argument("--text-dir", type=Path)
    extract.add_argument("--blocker", required=True)
    extract.add_argument(
        "--limit-books",
        type=_bounded_int("limit-books", MAX_BOOKS),
        default=MAX_BOOKS,
    )
    extract.add_argument(
        "--passages-per-book",
        type=_bounded_int("passages-per-book", MAX_PASSAGES_PER_BOOK),
        default=MAX_PASSAGES_PER_BOOK,
    )

    validate = subparsers.add_parser("validate")
    validate.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    validate.add_argument("--blocker", required=True)

    promote = subparsers.add_parser("promote")
    promote.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    promote.add_argument("--book-index", type=Path)
    promote.add_argument("--blocker", required=True)
    promote.add_argument("--note-id", required=True)

    feedback = subparsers.add_parser("feedback")
    feedback.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    feedback.add_argument("--input", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    provider_invoker: ProviderInvoker = invoke_provider,
) -> int:
    args = _parser().parse_args(argv)
    current_env = dict(os.environ if env is None else env)
    if args.command == "extract":
        return _extract(args, current_env, provider_invoker)
    if args.command == "validate":
        return _validate(args)
    if args.command == "promote":
        return _promote(args)
    if args.command == "feedback":
        return _feedback(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
