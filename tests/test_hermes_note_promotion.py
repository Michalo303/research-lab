import json

import pytest

from hermes_knowledge.note_store import (
    promote_note,
    validate_proposed_file,
    write_passage_candidates,
    write_proposed_notes,
)
from hermes_knowledge.passage_extractor import PassageCandidate
from hermes_knowledge.schema import KnowledgeValidationError, load_knowledge_jsonl


def _entry(note_id="note-1111111111111111", sha256="a" * 64):
    return {
        "book_id": f"book-{sha256[:12]}",
        "source_title": "Trading Systems and Methods",
        "source_path": f"private-book:book-{sha256[:12]}",
        "source_sha256": sha256,
        "concept": "Parameter stability",
        "hypothesis": "Stable parameter neighborhoods improve walk-forward reliability.",
        "summary": "Prefer broad stable regions.",
        "source_excerpt": "Short evidence.",
        "testable_rules": ["Penalize unstable adjacent parameter values."],
        "compatible_builders": ["active_momentum_rotation"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Improve walk-forward pass rate.",
        "known_failure_modes": ["Regime changes can invalidate the region."],
        "addresses_blockers": ["walk_forward_fail"],
        "priority_score": 70,
        "note_id": note_id,
        "source_location": "page:214",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Score dispersion in adjacent sweep results.",
    }


def _proposal(**entry_overrides):
    entry = _entry()
    entry.update(entry_overrides)
    return {
        "status": "proposed",
        "source_passage_id": entry["source_passage_id"],
        "entry": entry,
    }


def _write_index(path, sha256="a" * 64):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "books": [
                    {
                        "name": "Trading Systems and Methods.pdf",
                        "path": "/private/raw/book.pdf",
                        "size_bytes": 100,
                        "sha256": sha256,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_candidate_and_proposal_writes_do_not_create_extracted_notes(tmp_path):
    candidate_path = tmp_path / "passage_candidates" / "walk_forward_fail.jsonl"
    proposed_path = tmp_path / "proposed_notes" / "walk_forward_fail.jsonl"
    extracted_path = tmp_path / "extracted_notes" / "walk_forward_fail.jsonl"
    candidate = PassageCandidate(
        "passage-1111111111111111",
        "book-aaaaaaaaaaaa",
        "Trading Systems and Methods",
        "a" * 64,
        "walk_forward_fail",
        "page:214",
        ("robustness",),
        "Short evidence.",
        "Matched robustness.",
    )

    write_passage_candidates(candidate_path, [candidate])
    result = write_proposed_notes(proposed_path, [_proposal()])

    assert candidate_path.exists()
    assert proposed_path.exists()
    assert result.written == 1
    assert not extracted_path.exists()


def test_validate_is_read_only_and_reports_duplicates(tmp_path):
    proposed_path = tmp_path / "proposed.jsonl"
    rows = [_proposal(), _proposal()]
    original = "".join(json.dumps(row) + "\n" for row in rows)
    proposed_path.write_text(original, encoding="utf-8")

    summary = validate_proposed_file(proposed_path)

    assert summary.valid == 1
    assert summary.duplicates == 1
    assert summary.invalid == 0
    assert proposed_path.read_text(encoding="utf-8") == original


def test_promote_requires_exact_note_and_matching_book_hash(tmp_path):
    index_path = tmp_path / "index" / "book_index.json"
    proposed_path = tmp_path / "proposed.jsonl"
    extracted_path = tmp_path / "extracted.jsonl"
    _write_index(index_path)
    write_proposed_notes(proposed_path, [_proposal()])

    promoted = promote_note(
        "note-1111111111111111",
        proposed_path=proposed_path,
        extracted_path=extracted_path,
        book_index_path=index_path,
    )

    assert promoted["note_id"] == "note-1111111111111111"
    assert load_knowledge_jsonl(extracted_path) == [promoted]
    assert proposed_path.exists()

    with pytest.raises(ValueError, match="already promoted"):
        promote_note(
            "note-1111111111111111",
            proposed_path=proposed_path,
            extracted_path=extracted_path,
            book_index_path=index_path,
        )


def test_promote_rejects_missing_note_and_hash_mismatch(tmp_path):
    index_path = tmp_path / "index.json"
    proposed_path = tmp_path / "proposed.jsonl"
    extracted_path = tmp_path / "extracted.jsonl"
    _write_index(index_path, sha256="b" * 64)
    write_proposed_notes(proposed_path, [_proposal()])

    with pytest.raises(ValueError, match="not found"):
        promote_note(
            "note-2222222222222222",
            proposed_path=proposed_path,
            extracted_path=extracted_path,
            book_index_path=index_path,
        )
    with pytest.raises(ValueError, match="book hash"):
        promote_note(
            "note-1111111111111111",
            proposed_path=proposed_path,
            extracted_path=extracted_path,
            book_index_path=index_path,
        )
    assert not extracted_path.exists()


def test_proposal_envelope_copied_to_extracted_is_rejected(tmp_path):
    extracted_path = tmp_path / "extracted.jsonl"
    extracted_path.write_text(json.dumps(_proposal()) + "\n", encoding="utf-8")

    with pytest.raises(KnowledgeValidationError):
        load_knowledge_jsonl(extracted_path)
