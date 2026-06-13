import pytest

from hermes_knowledge.book_selector import select_books_for_blocker
from hermes_knowledge.books import BookRecord
from hermes_knowledge.blocker_taxonomy import (
    canonicalize_blocker_id,
    get_blocker_definition,
)


def _book(title: str, marker: str) -> BookRecord:
    sha256 = marker * 64
    return BookRecord(
        book_id=f"book-{sha256[:12]}",
        title=title,
        source_path=f"/private/{title}.pdf",
        source_sha256=sha256,
        size_bytes=100,
    )


def test_walk_forward_fail_has_weighted_evidence_terms():
    definition = get_blocker_definition("walk_forward_fail")

    assert definition.name == "walk_forward_fail"
    assert definition.term_weights["walk-forward"] > 0
    assert definition.term_weights["parameter stability"] > 0
    assert definition.term_weights["regime"] > 0


def test_unsupported_blocker_is_rejected():
    with pytest.raises(ValueError, match="unsupported blocker"):
        get_blocker_definition("unknown")


@pytest.mark.parametrize(
    "raw",
    [
        "insufficient rolling walk-forward robustness",
        "walk forward robustness below target",
        "wf pass rate below target",
        "walk-forward fail",
    ],
)
def test_walk_forward_diagnostics_map_to_canonical_blocker(raw):
    assert canonicalize_blocker_id(raw) == "walk_forward_fail"


def test_unknown_diagnostic_has_no_canonical_blocker():
    assert canonicalize_blocker_id("provider coverage gap") is None


def test_selector_is_blocker_specific_deterministic_and_deduplicated():
    books = [
        _book("Trading Systems and Methods", "a"),
        _book("Trading  Systems and Methods", "b"),
        _book("Adaptive Trading Across Regimes", "c"),
        _book("Cooking At Home", "d"),
    ]
    previews = {
        "book-aaaaaaaaaaaa": "parameter stability and walk-forward robustness",
        "book-cccccccccccc": "regime change and model decay",
    }

    first = select_books_for_blocker(
        books, "walk_forward_fail", limit=5, text_previews=previews
    )
    second = select_books_for_blocker(
        list(reversed(books)), "walk_forward_fail", limit=5, text_previews=previews
    )

    assert [item.book.book_id for item in first] == [
        item.book.book_id for item in second
    ]
    assert len([item for item in first if "Trading Systems" in item.book.title]) == 1
    assert all(item.matched_terms for item in first)
    assert all("Cooking" not in item.book.title for item in first)


def test_selector_applies_feedback_overlay_and_hard_limit():
    books = [
        _book(f"Walk Forward Robustness Volume {index}", f"{index + 1:x}")
        for index in range(7)
    ]
    boosted_id = books[-1].book_id

    selected = select_books_for_blocker(
        books,
        "walk_forward_fail",
        limit=5,
        book_priority_overlays={boosted_id: 50.0},
    )

    assert len(selected) == 5
    assert selected[0].book.book_id == boosted_id


def test_selector_rejects_limit_above_v1_maximum():
    with pytest.raises(ValueError, match="at most 5"):
        select_books_for_blocker([], "walk_forward_fail", limit=6)
