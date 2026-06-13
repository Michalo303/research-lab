import json

import pytest

from hermes_knowledge.runtime import load_book_knowledge_context
from hermes_knowledge.schema import KnowledgeValidationError, validate_entry
from research_lab.hermes.providers import ProviderResult
from research_lab.hermes.run_hypothesis_generation import run_hypothesis_generation
from research_lab.llm.hypothesis_adapter import build_hermes_prompt


def _write_index(root):
    path = root / "index" / "book_index.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "books": [
                    {
                        "name": "Risk Management Systems.pdf",
                        "path": "/opt/trading/private/hermes_books/raw/secret-book.pdf",
                        "extension": ".pdf",
                        "size_bytes": 123,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _note(**overrides):
    entry = {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Risk Management Systems",
        "source_path": "/opt/trading/private/hermes_books/raw/secret-book.pdf",
        "source_sha256": "a" * 64,
        "concept": "Volatility targeting",
        "hypothesis": "Lower exposure when realized volatility rises.",
        "summary": "A short curated note, not raw book text.",
        "source_excerpt": "short phrase",
        "testable_rules": ["Target eight percent annualized volatility."],
        "compatible_builders": ["long_term_vol_target_cap"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Contain drawdown in unstable regimes.",
        "known_failure_modes": ["Fast reversals may cause underexposure."],
        "addresses_blockers": ["drawdown"],
        "priority_score": 90,
    }
    entry.update(overrides)
    return entry


def _write_note(root, entry=None):
    notes_dir = root / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(entry or _note()) + "\n",
        encoding="utf-8",
    )
    return notes_dir


def test_prompt_includes_valid_book_context_and_safe_metadata(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = _write_note(tmp_path)

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
    )
    prompt = build_hermes_prompt(
        tmp_path,
        dominant_blocker="drawdown",
        book_index_path=index_path,
        book_notes_dir=notes_dir,
    )

    assert "BOOK-DERIVED RESEARCH CONTEXT" in prompt
    assert "Volatility targeting" in prompt
    assert context.note_count == 1
    assert context.selected_book_ids == ("book-aaaaaaaaaaaa",)
    assert "/opt/trading/private/hermes_books/raw/secret-book.pdf" not in prompt
    assert "short phrase" not in prompt


def test_prompt_builds_when_notes_are_absent(tmp_path):
    index_path = _write_index(tmp_path)

    prompt = build_hermes_prompt(
        tmp_path,
        dominant_blocker="drawdown",
        book_index_path=index_path,
        book_notes_dir=tmp_path / "missing-notes",
    )

    assert "BOOK-DERIVED RESEARCH CONTEXT" not in prompt
    assert "Return one JSON object" in prompt


def test_prompt_builds_when_book_index_is_absent(tmp_path):
    notes_dir = _write_note(tmp_path)

    prompt = build_hermes_prompt(
        tmp_path,
        dominant_blocker="drawdown",
        book_index_path=tmp_path / "missing-index.json",
        book_notes_dir=notes_dir,
    )

    assert "BOOK-DERIVED RESEARCH CONTEXT" not in prompt
    assert "Return one JSON object" in prompt


def test_runtime_ignores_unreviewed_zero_priority_skeletons(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = _write_note(tmp_path, _note(priority_score=0))

    context = load_book_knowledge_context(index_path, notes_dir, dominant_blocker="drawdown")

    assert context.prompt == ""
    assert context.note_count == 0
    assert context.selected_book_ids == ()


def test_orchestrator_artifact_logs_only_safe_book_metadata(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_note(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text("- biggest risk discovered: drawdown\n", encoding="utf-8")

    prompts = []

    def provider(_name, prompt, _env):
        prompts.append(prompt)
        return ProviderResult("ok", output=json.dumps({"hypotheses": []}))

    outcome = run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=provider,
    )

    assert outcome["book_knowledge"] == {
        "note_count": 1,
        "selected_book_ids": ["book-aaaaaaaaaaaa"],
    }
    artifact_text = outcome["artifact_path"].read_text(encoding="utf-8")
    assert "Dominant blocker: drawdown" in prompts[0]
    assert "/opt/trading/private/hermes_books/raw" not in artifact_text
    assert "short phrase" not in artifact_text


def test_schema_rejects_long_text_and_unknown_fields():
    with pytest.raises(KnowledgeValidationError, match="summary exceeds"):
        validate_entry(_note(summary="x" * 601))

    with pytest.raises(KnowledgeValidationError, match="unexpected fields"):
        validate_entry(_note(raw_pdf_text="forbidden"))
