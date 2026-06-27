import json

import pytest

from hermes_knowledge.feedback import apply_feedback
from hermes_knowledge.runtime import load_book_knowledge_context
from hermes_knowledge.schema import KnowledgeValidationError, validate_entry
from hermes_knowledge.prompt import build_hermes_knowledge_prompt
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
        "note_id": "note-1111111111111111",
        "source_location": "page:10",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Lower exposure as realized volatility rises.",
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
    assert context.selected_note_ids == ("note-1111111111111111",)
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
    assert context.selected_note_ids == ()


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
        "skipped_note_count": 0,
        "selected_book_ids": ["book-aaaaaaaaaaaa"],
        "selected_note_ids": ["note-1111111111111111"],
        "canonical_blocker_id": "drawdown",
        "blocker_diagnostic": "canonicalized",
    }
    artifact_text = outcome["artifact_path"].read_text(encoding="utf-8")
    assert "Dominant blocker: drawdown" in prompts[0]
    assert "/opt/trading/private/hermes_books/raw" not in artifact_text
    assert "short phrase" not in artifact_text


def test_orchestrator_adds_selected_note_ids_to_queue_provenance(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_note(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text("- biggest risk discovered: drawdown\n", encoding="utf-8")
    hypothesis = {
        "title": "Conservative trend cap",
        "family": "LONGTERM",
        "builder": "long_term_vol_target_cap",
        "rationale": "Reduce drawdown before seeking return.",
        "parameters": {
            "symbol": "SPY",
            "sma": 200,
            "vol_window": 63,
            "target_vol": 0.08,
            "max_weight": 0.65,
        },
        "risk_controls": {
            "volatility_targeting": "target portfolio volatility",
            "drawdown_circuit_breakers": "move to cash after drawdown threshold",
            "cash_defensive_regimes": "hold cash in risk-off regimes",
            "exposure_caps": "cap gross and single-asset exposure",
            "correlation_aware_portfolio_risk": "avoid correlated sleeves",
            "crisis_period_diagnostics": "test crisis windows",
            "cost_slippage_stress": "double cost stress",
            "parameter_neighborhood_stability": "test adjacent parameters",
        },
        "used_note_ids": ["note-1111111111111111"],
    }
    queued = []

    outcome = run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=lambda *_: ProviderResult(
            "ok", output=json.dumps({"hypotheses": [hypothesis]})
        ),
        queue_committer=lambda _path, rows: queued.extend(rows),
    )

    assert outcome["book_knowledge"]["selected_note_ids"] == [
        "note-1111111111111111"
    ]
    assert queued[0]["used_note_ids"] == ["note-1111111111111111"]


def test_runtime_excludes_incomplete_provenance_note_ids_from_promoted_evidence(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = tmp_path / "private" / "extracted_notes"
    notes_dir.mkdir(parents=True)
    incomplete_note = _note(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        concept="Incomplete provenance note",
        priority_score=89,
    )
    incomplete_note.pop("source_location")
    notes = [
        _note(),
        incomplete_note,
    ]
    (notes_dir / "notes.jsonl").write_text(
        "".join(json.dumps(note) + "\n" for note in notes),
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
        limit=5,
    )

    assert context.note_count == 2
    assert context.selected_note_ids == ("note-1111111111111111",)


def _write_three_attribution_notes(root):
    notes_dir = root / "extracted_notes"
    notes_dir.mkdir(parents=True)
    notes = [
        _note(
            note_id=f"note-{marker * 16}",
            source_passage_id=f"passage-{marker * 16}",
            concept=f"Context note {marker}",
            priority_score=90 - index,
        )
        for index, marker in enumerate(("1", "2", "3"))
    ]
    (notes_dir / "notes.jsonl").write_text(
        "".join(json.dumps(note) + "\n" for note in notes),
        encoding="utf-8",
    )
    return notes_dir


def _attribution_hypothesis(**overrides):
    hypothesis = {
        "title": "Attributed conservative trend cap",
        "family": "LONGTERM",
        "builder": "long_term_vol_target_cap",
        "rationale": "Reduce drawdown before seeking return.",
        "parameters": {
            "symbol": "SPY",
            "sma": 200,
            "vol_window": 63,
            "target_vol": 0.08,
            "max_weight": 0.65,
        },
        "risk_controls": {
            "volatility_targeting": "target portfolio volatility",
            "drawdown_circuit_breakers": "move to cash after drawdown threshold",
            "cash_defensive_regimes": "hold cash in risk-off regimes",
            "exposure_caps": "cap gross and single-asset exposure",
            "correlation_aware_portfolio_risk": "avoid correlated sleeves",
            "crisis_period_diagnostics": "test crisis windows",
            "cost_slippage_stress": "double cost stress",
            "parameter_neighborhood_stability": "test adjacent parameters",
        },
    }
    hypothesis.update(overrides)
    return hypothesis


def test_provider_attribution_is_limited_to_explicit_used_note_subset(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_three_attribution_notes(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text("- biggest risk discovered: excessive drawdown\n", encoding="utf-8")
    queued = []

    def provider(_name, prompt, _env):
        assert all(f"note-{marker * 16}" in prompt for marker in ("1", "2", "3"))
        return ProviderResult(
            "ok",
            output=json.dumps(
                {
                    "hypotheses": [
                        _attribution_hypothesis(
                            used_note_ids=["note-2222222222222222"]
                        )
                    ]
                }
            ),
        )

    run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=provider,
        queue_committer=lambda _path, rows: queued.extend(rows),
    )

    assert queued[0]["used_note_ids"] == ["note-2222222222222222"]
    priorities_path = tmp_path / "private" / "feedback" / "priorities.json"
    apply_feedback(
        [
            {
                "event_id": "event-attribution",
                "used_note_ids": queued[0]["used_note_ids"],
                "gate_passed": True,
            }
        ],
        note_to_book={
            "note-1111111111111111": "book-aaaaaaaaaaaa",
            "note-2222222222222222": "book-aaaaaaaaaaaa",
            "note-3333333333333333": "book-aaaaaaaaaaaa",
        },
        event_path=tmp_path / "private" / "feedback" / "events.jsonl",
        priorities_path=priorities_path,
    )
    priorities = json.loads(priorities_path.read_text(encoding="utf-8"))
    assert priorities["notes"] == {"note-2222222222222222": 5.0}


def test_missing_provider_attribution_defaults_to_empty_list(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_three_attribution_notes(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text("- biggest risk discovered: excessive drawdown\n", encoding="utf-8")
    queued = []

    run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=lambda *_: ProviderResult(
            "ok",
            output=json.dumps({"hypotheses": [_attribution_hypothesis()]}),
        ),
        queue_committer=lambda _path, rows: queued.extend(rows),
    )

    assert queued[0]["used_note_ids"] == []


def test_unknown_provider_note_attribution_rejects_hypothesis(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_three_attribution_notes(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text("- biggest risk discovered: excessive drawdown\n", encoding="utf-8")
    queued = []

    outcome = run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=lambda *_: ProviderResult(
            "ok",
            output=json.dumps(
                {
                    "hypotheses": [
                        _attribution_hypothesis(
                            used_note_ids=["note-ffffffffffffffff"]
                        )
                    ]
                }
            ),
        ),
        queue_committer=lambda _path, rows: queued.extend(rows),
    )

    assert queued == []
    assert "hypothesis_1:unknown_used_note_id" in outcome["rejection_reasons"]


def test_runtime_rejects_proposed_notes_directory(tmp_path):
    index_path = _write_index(tmp_path)
    proposed_dir = tmp_path / "proposed_notes"
    proposed_dir.mkdir()
    (proposed_dir / "notes.jsonl").write_text(
        json.dumps(_note()) + "\n", encoding="utf-8"
    )

    context = load_book_knowledge_context(
        index_path, proposed_dir, dominant_blocker="drawdown"
    )

    assert context.note_count == 0
    assert context.selected_note_ids == ()


def test_runtime_canonicalizes_walk_forward_fail_alias_without_global_fallback(
    tmp_path,
):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    walk_forward_note = _note(
        note_id="note-1111111111111111",
        source_passage_id="passage-1111111111111111",
        concept="Parameter stability",
        addresses_blockers=["walk_forward_fail"],
        priority_score=10,
    )
    unrelated_note = _note(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        concept="High priority unrelated note",
        addresses_blockers=["cost_stress"],
        priority_score=100,
    )
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(walk_forward_note) + "\n" + json.dumps(unrelated_note) + "\n",
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="walk_forward_fail",
        limit=1,
    )

    assert context.canonical_blocker_id == "walk_forward_robustness"
    assert context.blocker_diagnostic == "canonicalized"
    assert context.selected_note_ids == ("note-1111111111111111",)
    assert "High priority unrelated note" not in context.prompt


def test_runtime_rejects_unrecognized_blocker_instead_of_global_fallback(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = _write_note(tmp_path)

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="provider coverage gap",
    )

    assert context.prompt == ""
    assert context.canonical_blocker_id == ""
    assert context.blocker_diagnostic == "unrecognized_blocker"


def test_runtime_excludes_unknown_only_blocker_note_from_selected_note_ids(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    unknown_blocker_note = _note(
        addresses_blockers=["mystery_blocker"],
    )
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(unknown_blocker_note) + "\n",
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
        limit=5,
    )

    assert context.note_count == 1
    assert context.selected_note_ids == ()


@pytest.mark.parametrize("blocker", ["slippage stress", "max drawdown breach"])
def test_runtime_excludes_broad_phrase_alias_note_from_selected_note_ids(
    tmp_path,
    blocker,
):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    broad_alias_note = _note(
        addresses_blockers=[blocker],
    )
    eligible_note = _note(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        concept="Eligible drawdown note",
        addresses_blockers=["drawdown_fail"],
    )
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(broad_alias_note) + "\n" + json.dumps(eligible_note) + "\n",
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
        limit=5,
    )

    assert context.selected_note_ids == ("note-2222222222222222",)


def test_runtime_excludes_preview_only_unknown_canonical_blocker_from_selected_note_ids(
    tmp_path,
):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    preview_only_note = _note(
        note_id="note-1111111111111111",
        source_passage_id="passage-1111111111111111",
        addresses_blockers=["portfolio_concentration"],
    )
    eligible_note = _note(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        concept="Eligible drawdown note",
        addresses_blockers=["drawdown_fail"],
    )
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(preview_only_note) + "\n" + json.dumps(eligible_note) + "\n",
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
        limit=5,
    )

    assert context.selected_note_ids == ("note-2222222222222222",)


def test_provenance_backfill_planning_does_not_change_selected_note_ids(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    backfillable_missing_note_id = _note(
        note_id="note-3333333333333333",
        source_location="page:33",
        source_passage_id="passage-3333333333333333",
        addresses_blockers=["drawdown_fail"],
    )
    backfillable_missing_note_id.pop("note_id")
    eligible_note = _note(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        concept="Eligible drawdown note",
        addresses_blockers=["drawdown_fail"],
    )
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(backfillable_missing_note_id)
        + "\n"
        + json.dumps(eligible_note)
        + "\n",
        encoding="utf-8",
    )

    from hermes_knowledge.runtime import plan_note_provenance_backfill

    plan = plan_note_provenance_backfill(notes_dir)
    context = load_book_knowledge_context(
        index_path,
        notes_dir,
        dominant_blocker="drawdown",
        limit=5,
    )

    assert plan.rows_backfillable_all_required_fields == 1
    assert context.selected_note_ids == ("note-2222222222222222",)


def test_orchestrator_artifact_diagnoses_unrecognized_book_blocker(tmp_path):
    index_path = _write_index(tmp_path / "private")
    notes_dir = _write_note(tmp_path / "private")
    report = tmp_path / "reports" / "daily" / "2026-06-12.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "- biggest risk discovered: provider coverage gap\n", encoding="utf-8"
    )

    outcome = run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "command",
            "HERMES_BOOK_INDEX_PATH": str(index_path),
            "HERMES_BOOK_NOTES_DIR": str(notes_dir),
        },
        provider_invoker=lambda *_: ProviderResult(
            "ok", output=json.dumps({"hypotheses": []})
        ),
    )

    assert outcome["book_knowledge"]["canonical_blocker_id"] == ""
    assert (
        outcome["book_knowledge"]["blocker_diagnostic"]
        == "unrecognized_blocker"
    )


def test_schema_rejects_long_text_and_unknown_fields():
    with pytest.raises(KnowledgeValidationError, match="summary exceeds"):
        validate_entry(_note(summary="x" * 601))

    with pytest.raises(KnowledgeValidationError, match="unexpected fields"):
        validate_entry(_note(raw_pdf_text="forbidden"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hypothesis", "Read /opt/trading/private/hermes_books/raw/foo.pdf first."),
        ("testable_rules", ["Read C:\\private\\hermes_books\\raw\\foo.pdf first."]),
        ("expected_edge", "See file:///opt/trading/private/foo for support."),
        ("rationale", "Derived from hermes_books/raw/foo.pdf."),
        ("tags", ["file://private/source"]),
        ("topics", ["C:\\private\\library\\foo.pdf"]),
        ("source_reference", "/opt/trading/private/source"),
    ],
)
def test_schema_rejects_forbidden_references_without_echoing_values(field, value):
    with pytest.raises(KnowledgeValidationError) as exc_info:
        validate_entry(_note(**{field: value}))

    assert f"forbidden reference in {field}" in str(exc_info.value)
    assert "foo.pdf" not in str(exc_info.value)
    assert "/opt/trading/private" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hypothesis", "Read /opt/trading/private/hermes_books/raw/foo.pdf first."),
        ("testable_rules", ["Read hermes_books/raw/foo.pdf first."]),
        ("expected_edge", "See file://private/foo.pdf for support."),
    ],
)
def test_prompt_guard_skips_entire_note_with_forbidden_reference(field, value):
    unsafe = _note(**{field: value})
    safe = _note(
        source_sha256="b" * 64,
        book_id="book-bbbbbbbbbbbb",
        concept="Safe volatility cap",
    )

    prompt = build_hermes_knowledge_prompt(
        [unsafe, safe], dominant_blocker="drawdown", limit=5
    )

    assert "Safe volatility cap" in prompt
    assert "Volatility targeting" not in prompt
    for marker in (
        "/opt/trading/private/",
        "hermes_books/raw",
        ".pdf",
        "file://",
    ):
        assert marker not in prompt.casefold()


def test_runtime_skips_forbidden_note_and_counts_it_safely(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir()
    (notes_dir / "unsafe.jsonl").write_text(
        json.dumps(
            _note(
                hypothesis=(
                    "Read /opt/trading/private/hermes_books/raw/foo.pdf first."
                )
            )
        )
        + "\n",
        encoding="utf-8",
    )

    context = load_book_knowledge_context(
        index_path, notes_dir, dominant_blocker="drawdown"
    )

    assert context.prompt == ""
    assert context.note_count == 0
    assert context.skipped_note_count == 1
    assert context.selected_book_ids == ()
    assert context.selected_note_ids == ()


def test_main_prompt_drops_unsafe_dynamic_context(tmp_path):
    source_items = tmp_path / "registry" / "source_items.jsonl"
    source_items.parent.mkdir(parents=True)
    source_items.write_text(
        json.dumps(
            {
                "title": "Unsafe source",
                "source": "private",
                "url": "file:///opt/trading/private/hermes_books/raw/foo.pdf",
                "tags": ["risk"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    prompt = build_hermes_prompt(
        tmp_path,
        diagnostics_text=(
            "Inspect /opt/trading/private/hermes_books/raw/foo.pdf"
        ),
        input_report_path="file:///opt/trading/private/report.pdf",
    )

    assert "Unsafe source" not in prompt
    for marker in (
        "/opt/trading/private/",
        "hermes_books/raw",
        ".pdf",
        "file://",
    ):
        assert marker not in prompt.casefold()
