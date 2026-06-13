"""Select a bounded set of books for one research blocker."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping

from hermes_knowledge.blocker_taxonomy import get_blocker_definition
from hermes_knowledge.books import BookRecord


MAX_BOOKS = 5


@dataclass(frozen=True)
class SelectedBook:
    book: BookRecord
    score: float
    matched_terms: tuple[str, ...]
    reasons: tuple[str, ...]


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def select_books_for_blocker(
    books: Iterable[BookRecord],
    blocker: str,
    *,
    limit: int = MAX_BOOKS,
    text_previews: Mapping[str, str] | None = None,
    book_priority_overlays: Mapping[str, float] | None = None,
) -> list[SelectedBook]:
    if not 1 <= limit <= MAX_BOOKS:
        raise ValueError(f"limit must be between 1 and at most {MAX_BOOKS}")
    definition = get_blocker_definition(blocker)
    previews = text_previews or {}
    overlays = book_priority_overlays or {}
    unique: dict[str, BookRecord] = {}
    for book in sorted(
        books,
        key=lambda item: (
            _normalize(item.title),
            len(item.title),
            item.title.casefold(),
            item.book_id,
        ),
    ):
        unique.setdefault(_normalize(book.title), book)

    selected: list[SelectedBook] = []
    for book in unique.values():
        title = _normalize(book.title)
        preview = _normalize(str(previews.get(book.book_id, ""))[:20_000])
        matches: list[str] = []
        score = 0.0
        for term, weight in definition.term_weights.items():
            normalized_term = _normalize(term)
            if normalized_term and normalized_term in title:
                matches.append(term)
                score += weight * 2.0
            elif normalized_term and normalized_term in preview:
                matches.append(term)
                score += weight
        if not matches:
            continue
        overlay = float(overlays.get(book.book_id, 0.0))
        score += max(-50.0, min(50.0, overlay))
        selected.append(
            SelectedBook(
                book=book,
                score=round(score, 4),
                matched_terms=tuple(dict.fromkeys(matches)),
                reasons=tuple(f"matched:{term}" for term in dict.fromkeys(matches)),
            )
        )
    selected.sort(
        key=lambda item: (-item.score, item.book.title.casefold(), item.book.book_id)
    )
    return selected[:limit]
