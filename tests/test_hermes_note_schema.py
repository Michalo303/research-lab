import json
from pathlib import Path

import pytest

import hermes_knowledge.note_generator as note_generator
import hermes_knowledge.runtime as knowledge_runtime

import hermes_knowledge.runtime as knowledge_runtime
from hermes_knowledge.book_selector import SelectedBook
from hermes_knowledge.books import load_book_index
from hermes_knowledge.passage_extractor import extract_passages
from hermes_knowledge.runtime import (
    audit_note_inventory,
    load_book_knowledge_context,
    plan_controlled_reextraction_run,
    plan_note_provenance_backfill,
    plan_note_reextraction,
)
from hermes_knowledge.note_generator import generate_proposed_notes
from hermes_knowledge.passage_extractor import PassageCandidate
from hermes_knowledge.schema import (
    KnowledgeValidationError,
    validate_entry,
    validate_proposed_note,
)
from research_lab.hermes.providers import ProviderResult


def _passage(
    marker: str = "1",
    *,
    text: str = (
        "Broad stable parameter regions can improve walk-forward robustness "
        "relative to sharp optima."
    ),
    source_title: str = "Trading Systems and Methods",
    blocker: str = "walk_forward_fail",
) -> PassageCandidate:
    return PassageCandidate(
        passage_id=f"passage-{marker * 16}",
        book_id="book-aaaaaaaaaaaa",
        source_title=source_title,
        source_sha256="a" * 64,
        blocker=blocker,
        location="page:214",
        matched_terms=("parameter stability", "robustness"),
        text=text,
        extraction_reason="Matched blocker terms.",
    )


def _provider_note(**overrides):
    note = {
        "concept": "Parameter neighborhood stability",
        "hypothesis": "Broad stable parameter regions improve walk-forward reliability.",
        "summary": "Prefer stable neighborhoods over isolated parameter optima.",
        "testable_rules": [
            "Penalize parameter sets whose adjacent values materially degrade walk-forward metrics."
        ],
        "compatible_builders": ["active_momentum_rotation"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Increase walk-forward pass rate without relaxing gates.",
        "known_failure_modes": ["Wide plateaus may still decay after regime change."],
        "implementation_hint": "Compute dispersion across adjacent parameter sweep results.",
        "priority_score": 72,
    }
    note.update(overrides)
    return note


def _candidate_review_entry(**overrides):
    entry = {
        "note_id": "note-1111111111111111",
        "source_location": "page:12",
        "source_passage_id": "passage-1111111111111111",
        "blocker_tags": ["walk_forward_fail"],
        "thesis": "Stable parameter neighborhoods improve walk-forward reliability.",
        "evidence_summary": "Prefer broad stable regions over isolated optima.",
        "risk_control_hint": "Measure adjacent parameter dispersion.",
    }
    entry.update(overrides)
    return entry


def _promotion_base(tmp_path):
    base = tmp_path / "hermes_books"
    (base / "extracted_notes").mkdir(parents=True)
    return base


def _promotion_fixture(tmp_path):
    base = _promotion_base(tmp_path)
    index = base / "index" / "book_index.json"
    text = base / "text" / "book-aaaaaaaaaaaa.txt"
    index.parent.mkdir(parents=True, exist_ok=True)
    text.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps(
            {
                "books": [
                    {
                        "name": "Trading Systems and Methods.pdf",
                        "path": str(base / "raw" / "book.pdf"),
                        "size_bytes": 100,
                        "sha256": "a" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    text.write_text(
        "Parameter stability and walk-forward robustness reduce overfitting.",
        encoding="utf-8",
    )
    return base


def _read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _resolved_candidate_review_entry(base: Path, **overrides):
    book = load_book_index(base / "index" / "book_index.json")[0]
    passages, diagnostics = extract_passages(
        [SelectedBook(book=book, score=0.0, matched_terms=(), reasons=("reextract_candidate",))],
        "walk_forward_fail",
        text_dir=base / "text",
        passages_per_book=1,
    )
    assert diagnostics == []
    assert len(passages) == 1
    passage = passages[0]
    entry = {
        "note_id": "note-1111111111111111",
        "source_location": passage.location,
        "source_passage_id": passage.passage_id,
        "blocker_tags": ["walk_forward_fail"],
        "thesis": "Stable parameter neighborhoods improve walk-forward reliability.",
        "evidence_summary": "Prefer broad stable regions over isolated optima.",
        "risk_control_hint": "Measure adjacent parameter dispersion.",
    }
    entry.update(overrides)
    return entry


def test_generated_note_has_repository_owned_provenance_and_stable_id():
    calls = []

    def fake_provider(provider, prompt, env):
        calls.append((provider, prompt, env))
        return ProviderResult("ok", output=json.dumps(_provider_note()))

    first, diagnostics = generate_proposed_notes(
        [_passage()],
        provider="command",
        env={"HERMES_COMMAND": "fake"},
        provider_invoker=fake_provider,
    )
    second, _ = generate_proposed_notes(
        [_passage()],
        provider="command",
        env={},
        provider_invoker=fake_provider,
    )

    assert diagnostics == []
    assert len(calls) == 2
    proposal = validate_proposed_note(first[0])
    entry = proposal["entry"]
    assert proposal["status"] == "proposed"
    assert proposal["source_passage_id"] == "passage-1111111111111111"
    assert entry["note_id"] == second[0]["entry"]["note_id"]
    assert entry["addresses_blockers"] == ["walk_forward_fail"]
    assert entry["source_location"] == "page:214"
    assert entry["source_path"] == "private-book:book-aaaaaaaaaaaa"
    assert len(entry["source_excerpt"]) <= 280
    assert "exactly one JSON object" in calls[0][1]


def test_provider_prompt_declares_exact_minimal_json_contract():
    prompt = note_generator._prompt(_passage())

    assert "Return exactly one JSON object and nothing else." in prompt
    assert "Do not return prose, markdown, code fences, comments, or extra keys." in prompt
    assert '"concept": "string"' in prompt
    assert '"testable_rules": ["string"]' in prompt
    assert '"priority_score": 0' in prompt
    assert '"source_excerpt"' not in prompt


def test_proposal_envelope_cannot_pass_runtime_entry_validation():
    proposal = {
        "status": "proposed",
        "source_passage_id": "passage-1111111111111111",
        "entry": {},
    }

    with pytest.raises(KnowledgeValidationError, match="missing required fields"):
        validate_entry(proposal)


def test_generation_skips_only_failed_passage():
    responses = iter(
        [
            ProviderResult("provider_error", message="failed"),
            ProviderResult("ok", output="not json"),
            ProviderResult("ok", output=json.dumps(_provider_note(priority_score=999))),
            ProviderResult("ok", output=json.dumps(_provider_note())),
        ]
    )

    def fake_provider(provider, prompt, env):
        return next(responses)

    proposals, diagnostics = generate_proposed_notes(
        [_passage(str(index)) for index in range(1, 5)],
        provider="command",
        env={},
        provider_invoker=fake_provider,
    )

    assert len(proposals) == 1
    assert [item.code for item in diagnostics] == [
        "provider_error",
        "invalid_json",
        "schema_violation",
    ]
    assert all("not json" not in item.message for item in diagnostics)


def test_generation_reports_redacted_schema_reason_without_provider_output():
    provider_note = _provider_note()
    provider_note.pop("summary")

    proposals, diagnostics = generate_proposed_notes(
        [_passage()],
        provider="command",
        env={},
        provider_invoker=lambda *_args: ProviderResult(
            "ok", output=json.dumps(provider_note)
        ),
    )

    assert proposals == []
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "schema_violation"
    assert diagnostics[0].message == "Provider note failed local schema validation."
    assert diagnostics[0].reason == "missing_required_field"


def test_generation_preserves_fixed_provider_reason_without_provider_text():
    proposals, diagnostics = generate_proposed_notes(
        [_passage()],
        provider="openai_compatible",
        env={},
        provider_invoker=lambda *_args: ProviderResult(
            "provider_error",
            message="OpenAI-compatible provider request failed.",
            reason="authentication_failure",
        ),
    )

    assert proposals == []
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "provider_error"
    assert diagnostics[0].message == "Provider did not return a usable note."
    assert diagnostics[0].reason == "authentication_failure"


def test_proposed_note_requires_generation_provenance():
    with pytest.raises(KnowledgeValidationError, match="source_passage_id"):
        validate_proposed_note({"status": "proposed", "entry": {}})


MA_EVIDENCE = (
    "Open long when the market close is above the moving average and close the "
    "position when it falls below the moving average. Choose the moving-average "
    "length logically rather than optimizing it, because optimization is data fitting."
)


def _generate_grounded(provider_note, *, passage=None):
    prompts = []

    def fake_provider(provider, prompt, env):
        prompts.append(prompt)
        return ProviderResult("ok", output=json.dumps(provider_note))

    proposals, diagnostics = generate_proposed_notes(
        [passage or _passage(text=MA_EVIDENCE)],
        provider="command",
        env={},
        provider_invoker=fake_provider,
    )
    return proposals, diagnostics, prompts


def test_grounding_normalizes_unsupported_sensitive_fields_for_ma_excerpt():
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule and avoid parameter optimization.",
        summary="The passage describes a fixed moving-average rule and rejects optimization as data fitting.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["equities", "futures", "FX"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="The system has positive expectancy across markets.",
        known_failure_modes=[
            "walk_forward_fail",
            "edge collapses after a volatility regime shift",
        ],
        implementation_hint="Keep the moving-average length fixed and do not optimize it.",
    )

    proposals, diagnostics, prompts = _generate_grounded(provider_note)

    assert diagnostics == []
    assert len(proposals) == 1
    entry = proposals[0]["entry"]
    assert entry["asset_classes"] == ["unknown"]
    assert entry["expected_edge"] == "unknown"
    assert entry["known_failure_modes"] == ["generic_risk:unknown"]
    assert entry["concept"] == "Fixed moving-average rule"
    assert entry["implementation_hint"] == (
        "Keep the moving-average length fixed and do not optimize it."
    )
    assert entry["testable_rules"] == [
        "Use one fixed moving-average length selected before testing."
    ]
    prompt = prompts[0]
    assert "Do not claim positive expectancy" in prompt
    assert "Do not infer asset classes" in prompt
    assert "Do not claim walk-forward failure" in prompt
    assert "Do not add regime or volatility failure modes" in prompt
    assert "same claim type and polarity" in prompt


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "hypothesis": "This system failed walk-forward validation.",
            "summary": "Use a fixed moving-average rule.",
        },
        {
            "hypothesis": "Use a fixed moving-average rule.",
            "summary": "The system has positive expectancy.",
        },
    ],
)
def test_unsupported_material_claim_rejects_proposed_note(overrides):
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
        **overrides,
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note)

    assert proposals == []
    assert [item.code for item in diagnostics] == ["grounding_violation"]


def test_grounding_uses_only_evidence_not_title_or_blocker_metadata():
    passage = _passage(
        text=MA_EVIDENCE,
        source_title="Equities Futures FX Walk-Forward Failure Handbook",
        blocker="walk_forward_fail",
    )
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule and avoid parameter optimization.",
        summary="The passage describes a fixed moving-average rule and rejects optimization as data fitting.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["equities", "futures", "FX"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["walk_forward_fail"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note, passage=passage)

    assert diagnostics == []
    assert len(proposals) == 1
    assert proposals[0]["entry"]["asset_classes"] == ["unknown"]
    assert proposals[0]["entry"]["known_failure_modes"] == [
        "generic_risk:unknown"
    ]


@pytest.mark.parametrize(
    ("field", "claim"),
    [
        ("hypothesis", "The fixed moving-average strategy is profitable."),
        ("hypothesis", "The fixed moving-average strategy made money."),
        ("summary", "The strategy has positive edge."),
        ("hypothesis", "The strategy has poor walk-forward robustness."),
        ("summary", "The strategy has weak walk-forward robustness."),
        ("hypothesis", "The strategy fails out-of-sample."),
        ("summary", "The strategy has robust out-of-sample performance."),
    ],
)
def test_sensitive_claim_synonyms_require_direct_evidence(field, claim):
    overrides = {
        "hypothesis": "Use a fixed moving-average rule.",
        "summary": "Keep the moving-average length fixed.",
        field: claim,
    }
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
        **overrides,
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note)

    assert proposals == []
    assert [item.code for item in diagnostics] == ["grounding_violation"]


def test_unsupported_profit_claim_in_expected_edge_is_normalized():
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule.",
        summary="Keep the moving-average length fixed.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="The strategy should generate profits.",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note)

    assert diagnostics == []
    assert proposals[0]["entry"]["expected_edge"] == "unknown"


@pytest.mark.parametrize(
    ("evidence", "claim"),
    [
        (
            "The fixed moving-average system was profitable in the reported test.",
            "The fixed moving-average system was profitable.",
        ),
        (
            "Walk-forward validation failed for the fixed moving-average rule.",
            "Walk-forward validation failed for the fixed moving-average rule.",
        ),
    ],
)
def test_sensitive_claim_is_allowed_when_passage_directly_supports_it(evidence, claim):
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis=claim,
        summary="Use a fixed moving-average rule.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(
        provider_note,
        passage=_passage(text=evidence),
    )

    assert diagnostics == []
    assert len(proposals) == 1


@pytest.mark.parametrize(
    ("evidence", "claim"),
    [
        ("The system was not profitable.", "The system was profitable."),
        ("The strategy lost money.", "The strategy should generate profits."),
        (
            "The system was not profitable but had stable parameters.",
            "The system was profitable.",
        ),
        (
            "Walk-forward validation passed.",
            "Walk-forward validation failed.",
        ),
        (
            "Walk-forward validation failed.",
            "Walk-forward validation passed.",
        ),
        (
            "The system passed out-of-sample validation.",
            "The system failed out-of-sample validation.",
        ),
        (
            "The system failed out-of-sample validation.",
            "The system was robust out-of-sample.",
        ),
        (
            "The model was robust in the original sample.",
            "The model generalizes to unseen markets.",
        ),
        (
            "The model was robust in sample.",
            "The model is robust out-of-sample.",
        ),
    ],
)
def test_contradictory_or_wrong_scope_sensitive_claim_is_rejected(evidence, claim):
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis=claim,
        summary="Use a fixed moving-average rule.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(
        provider_note,
        passage=_passage(text=evidence),
    )

    assert proposals == []
    assert [item.code for item in diagnostics] == ["grounding_violation"]


def test_negative_profitability_evidence_normalizes_positive_expected_edge():
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule.",
        summary="The strategy lost money in the cited test.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="The strategy should generate profits.",
        known_failure_modes=["lost money"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(
        provider_note,
        passage=_passage(text="The strategy lost money."),
    )


def _audit_entry(**overrides):
    entry = {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Risk Management Systems",
        "source_path": "private-book:book-aaaaaaaaaaaa",
        "source_sha256": "a" * 64,
        "concept": "Volatility targeting",
        "hypothesis": "Lower exposure when realized volatility rises.",
        "summary": "A short curated note.",
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


def test_audit_inventory_reports_mixed_legacy_current_and_normalized_blockers(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    incomplete_current = _audit_entry(
        note_id="note-3333333333333333",
        source_passage_id="passage-3333333333333333",
        addresses_blockers=["walk_forward_fail"],
    )
    incomplete_current.pop("source_location")
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(_audit_entry()),
                json.dumps(
                    _audit_entry(
                        note_id="note-2222222222222222",
                        source_passage_id="passage-2222222222222222",
                        addresses_blockers=["drawdown_fail", "unknown_blocker"],
                    )
                ),
                json.dumps(incomplete_current),
                json.dumps(
                    {
                        "book_id": "book-aaaaaaaaaaaa",
                        "summary": "legacy row",
                        "addresses_blockers": ["drawdown_fail"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.total_note_rows == 4
    assert audit.current_format_note_rows == 3
    assert audit.legacy_note_rows == 1
    assert audit.rows_with_note_id == 3
    assert audit.rows_with_source_location == 2
    assert audit.rows_with_source_passage_id == 3
    assert audit.rows_with_blocker_tags == 4
    assert audit.normalized_blocker_counts == {
        "drawdown": 3,
        "walk_forward_robustness": 1,
    }
    assert audit.unknown_blocker_ids == {"unknown_blocker": 1}
    assert audit.rows_eligible_for_provenance_aware_retrieval == 2
    assert audit.rows_excluded_from_promoted_used_note_ids == 2
    assert audit.feedback_overlay_present is False
    assert audit.ready_for_new_knihomol_hypothesis_generation is False


def test_audit_inventory_reports_remediation_diagnostics_without_changing_eligibility(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    missing_location = _audit_entry(
        note_id="note-2222222222222222",
        source_passage_id="passage-2222222222222222",
        addresses_blockers=["walk_forward_fail"],
    )
    missing_location.pop("source_location")
    missing_note_id = _audit_entry(
        source_passage_id="passage-3333333333333333",
        addresses_blockers=["drawdown_fail"],
    )
    missing_note_id.pop("note_id")
    unknown_only_complete = _audit_entry(
        note_id="note-4444444444444444",
        source_passage_id="passage-4444444444444444",
        addresses_blockers=["portfolio_concentration"],
    )
    unknown_only_missing_passage = _audit_entry(
        note_id="note-5555555555555555",
        addresses_blockers=["regime_instability"],
    )
    unknown_only_missing_passage.pop("source_passage_id")
    legacy_row = {
        "book_id": "book-aaaaaaaaaaaa",
        "summary": "legacy row",
        "addresses_blockers": ["drawdown_fail"],
    }
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(_audit_entry()),
                json.dumps(missing_location),
                json.dumps(missing_note_id),
                json.dumps(unknown_only_complete),
                json.dumps(unknown_only_missing_passage),
                json.dumps(legacy_row),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.total_note_rows == 6
    assert audit.current_format_note_rows == 5
    assert audit.legacy_note_rows == 1
    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 5
    assert audit.excluded_by_reason == {
        "legacy_format": 1,
        "missing_note_id": 2,
        "missing_source_location": 2,
        "missing_source_passage_id": 2,
        "no_recognized_blocker": 2,
        "unknown_only_blockers": 2,
    }
    assert audit.missing_field_counts == {
        "note_id": 2,
        "source_location": 2,
        "source_passage_id": 2,
    }
    assert audit.unknown_blocker_ids == {
        "portfolio_concentration": 1,
        "regime_instability": 1,
    }
    assert audit.canonical_blocker_preview == {"portfolio_concentration": 1}
    assert audit.feedback_overlay_expected_path == "feedback/priorities.json"
    assert audit.remediation_readiness == "blocked"
    assert audit.remediation_remaining_blockers == {
        "legacy_format": 1,
        "missing_note_id": 2,
        "missing_source_location": 2,
        "missing_source_passage_id": 2,
        "no_recognized_blocker": 2,
        "unknown_only_blockers": 2,
        "feedback_overlay_missing": 1,
    }


def test_audit_inventory_only_counts_provenance_complete_notes_as_promoted_evidence(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    incomplete_current = _audit_entry(
        note_id="note-3333333333333333",
        source_passage_id="passage-3333333333333333",
    )
    incomplete_current.pop("source_location")
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(_audit_entry()),
                json.dumps(
                    _audit_entry(
                        note_id="note-2222222222222222",
                        source_passage_id="passage-2222222222222222",
                    )
                ),
                json.dumps(incomplete_current),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.rows_eligible_for_provenance_aware_retrieval == 2
    assert audit.rows_excluded_from_promoted_used_note_ids == 1


def test_audit_inventory_preview_does_not_change_actual_eligibility(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    _audit_entry(
                        addresses_blockers=["portfolio_concentration"],
                    )
                ),
                json.dumps(
                    _audit_entry(
                        note_id="note-2222222222222222",
                        source_passage_id="passage-2222222222222222",
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 1
    assert audit.canonical_blocker_preview == {"portfolio_concentration": 1}


def test_audit_inventory_reports_unknown_blocker_current_note_but_excludes_it_from_promoted_evidence(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    _audit_entry(
                        addresses_blockers=["mystery_blocker"],
                    )
                ),
                json.dumps(
                    _audit_entry(
                        note_id="note-2222222222222222",
                        source_passage_id="passage-2222222222222222",
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.current_format_note_rows == 2
    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 1
    assert audit.unknown_blocker_ids == {"mystery_blocker": 1}


@pytest.mark.parametrize("blocker", ["slippage stress", "max drawdown breach"])
def test_audit_inventory_reports_broad_phrase_alias_as_unknown_and_excluded(
    tmp_path,
    blocker,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    _audit_entry(
                        addresses_blockers=[blocker],
                    )
                ),
                json.dumps(
                    _audit_entry(
                        note_id="note-2222222222222222",
                        source_passage_id="passage-2222222222222222",
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.current_format_note_rows == 2
    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 1
    assert audit.unknown_blocker_ids == {blocker: 1}


def test_audit_inventory_treats_drawdown_fail_alias_as_promoted_evidence_eligible(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(_audit_entry(addresses_blockers=["drawdown_fail"])) + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 0
    assert audit.normalized_blocker_counts == {"drawdown": 1}
    assert audit.unknown_blocker_ids == {}


def test_audit_inventory_treats_walk_forward_fail_alias_as_promoted_evidence_eligible(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(_audit_entry(addresses_blockers=["walk_forward_fail"])) + "\n",
        encoding="utf-8",
    )

    audit = audit_note_inventory(notes_dir)

    assert audit.rows_eligible_for_provenance_aware_retrieval == 1
    assert audit.rows_excluded_from_promoted_used_note_ids == 0
    assert audit.normalized_blocker_counts == {"walk_forward_robustness": 1}
    assert audit.unknown_blocker_ids == {}


def test_provenance_backfill_plan_reports_fully_backfillable_note_id_only_rows(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    backfillable = _audit_entry(
        note_id="note-2222222222222222",
        source_location="page:22",
        source_passage_id="passage-2222222222222222",
        addresses_blockers=["walk_forward_fail"],
    )
    backfillable.pop("note_id")
    (notes_dir / "notes.jsonl").write_text(
        json.dumps(backfillable) + "\n",
        encoding="utf-8",
    )

    plan = plan_note_provenance_backfill(notes_dir)

    assert plan.total_rows == 1
    assert plan.rows_missing_note_id == 1
    assert plan.rows_missing_source_location == 0
    assert plan.rows_missing_source_passage_id == 0
    assert plan.rows_with_deterministic_source_file_metadata == 1
    assert plan.rows_with_deterministic_passage_id_source == 1
    assert plan.rows_backfillable_all_required_fields == 1
    assert plan.rows_not_backfillable == 0
    assert plan.not_backfillable_reasons == {
        "legacy_format": 0,
        "missing_source_file_metadata": 0,
        "ambiguous_source_location": 0,
        "missing_passage_anchor": 0,
        "duplicate_candidate_identity": 0,
    }
    assert plan.proposed_backfill_fields == {
        "note_id": 1,
        "source_location": 0,
        "source_passage_id": 0,
    }
    assert plan.safety_verdict == (
        "plan_only",
        "no_write_performed",
        "generation_still_blocked",
    )


def test_provenance_backfill_plan_reports_legacy_and_missing_anchor_rows_as_not_backfillable(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    missing_anchor = _audit_entry(
        note_id="note-3333333333333333",
        source_location="page:33",
        addresses_blockers=["drawdown_fail"],
    )
    missing_anchor.pop("source_passage_id")
    legacy_row = {
        "book_id": "book-aaaaaaaaaaaa",
        "summary": "legacy row",
        "addresses_blockers": ["drawdown_fail"],
    }
    (notes_dir / "notes.jsonl").write_text(
        "\n".join([json.dumps(missing_anchor), json.dumps(legacy_row)]) + "\n",
        encoding="utf-8",
    )

    plan = plan_note_provenance_backfill(notes_dir)

    assert plan.total_rows == 2
    assert plan.rows_missing_source_passage_id == 2
    assert plan.rows_with_deterministic_source_file_metadata == 1
    assert plan.rows_with_deterministic_passage_id_source == 0
    assert plan.rows_backfillable_all_required_fields == 0
    assert plan.rows_not_backfillable == 2
    assert plan.not_backfillable_reasons == {
        "legacy_format": 1,
        "missing_source_file_metadata": 0,
        "ambiguous_source_location": 0,
        "missing_passage_anchor": 1,
        "duplicate_candidate_identity": 0,
    }


def test_provenance_backfill_plan_reports_duplicate_candidate_identity_as_not_backfillable(
    tmp_path,
):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    duplicate = _audit_entry(
        note_id="note-4444444444444444",
        source_location="page:44",
        source_passage_id="passage-4444444444444444",
        addresses_blockers=["drawdown_fail"],
    )
    duplicate.pop("note_id")
    (notes_dir / "notes.jsonl").write_text(
        "\n".join([json.dumps(duplicate), json.dumps(duplicate)]) + "\n",
        encoding="utf-8",
    )

    plan = plan_note_provenance_backfill(notes_dir)

    assert plan.rows_backfillable_all_required_fields == 0
    assert plan.rows_not_backfillable == 2
    assert plan.not_backfillable_reasons == {
        "legacy_format": 0,
        "missing_source_file_metadata": 0,
        "ambiguous_source_location": 0,
        "missing_passage_anchor": 0,
        "duplicate_candidate_identity": 2,
    }


def test_provenance_backfill_plan_does_not_modify_private_note_files(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    path = notes_dir / "notes.jsonl"
    path.write_text(
        json.dumps(
            {
                "book_id": "book-aaaaaaaaaaaa",
                "summary": "legacy row",
                "addresses_blockers": ["drawdown_fail"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before = path.read_bytes()

    _ = plan_note_provenance_backfill(notes_dir)

    assert path.read_bytes() == before


def test_reextraction_plan_reports_safe_aggregate_counts_and_flags(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    complete = _audit_entry()
    unsalvageable_drawdown = _audit_entry(
        note_id="note-2222222222222222",
        source_location="page:22",
        source_passage_id="passage-2222222222222222",
        addresses_blockers=["drawdown_fail"],
    )
    unsalvageable_drawdown.pop("note_id")
    unsalvageable_walk_forward = _audit_entry(
        book_id="book-bbbbbbbbbbbb",
        source_title="Other Private Book",
        source_path="private-book:book-bbbbbbbbbbbb",
        source_sha256="b" * 64,
        note_id="note-3333333333333333",
        source_location="page:33",
        source_passage_id="passage-3333333333333333",
        addresses_blockers=["walk_forward_fail"],
    )
    unsalvageable_walk_forward.pop("source_location")
    legacy_row = {
        "book_id": "book-cccccccccccc",
        "summary": "legacy row",
        "addresses_blockers": ["drawdown_fail"],
    }
    (notes_dir / "notes.jsonl").write_text(
        "\n".join(
            [
                json.dumps(complete),
                json.dumps(unsalvageable_drawdown),
                json.dumps(unsalvageable_walk_forward),
                json.dumps(legacy_row),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    plan = plan_note_reextraction(notes_dir)

    assert plan.existing_total_rows == 4
    assert plan.existing_provenance_complete_rows == 1
    assert plan.existing_unsalvageable_rows == 3
    assert plan.candidate_source_count == 2
    assert plan.rows_with_book_id == 3
    assert plan.rows_missing_book_id == 0
    assert plan.rows_with_ambiguous_source_identity == 0
    assert plan.candidate_blocker_counts == {
        "drawdown": 1,
        "walk_forward_robustness": 1,
    }
    assert plan.target_schema_required_fields == (
        "note_id",
        "source_location",
        "source_passage_id",
        "blocker_tags",
        "thesis",
        "evidence_summary",
        "risk_control_hint",
    )
    assert plan.future_write_required is True
    assert plan.current_pr_write_allowed is False
    assert plan.provider_required_for_future_execution is True
    assert plan.current_pr_provider_calls_allowed is False
    assert plan.generation_still_blocked is True
    assert plan.next_execution_mode == "separate_explicit_reextraction_pr"


def test_reextraction_plan_never_exposes_source_identity_values_and_does_not_write(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    path = notes_dir / "notes.jsonl"
    row = _audit_entry(
        book_id="book-secretsecret",
        source_title="Very Private Book",
        source_path="private-book:book-secretsecret",
        source_sha256="f" * 64,
        note_id="note-4444444444444444",
        source_location="page:44",
        source_passage_id="passage-4444444444444444",
    )
    row.pop("note_id")
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()

    plan = plan_note_reextraction(notes_dir)

    assert path.read_bytes() == before
    assert plan.candidate_source_count == 1
    assert plan.rows_with_book_id == 1
    assert "book-secretsecret" not in repr(plan)
    assert "Very Private Book" not in repr(plan)
    assert "private-book:book-secretsecret" not in repr(plan)
    assert "f" * 64 not in repr(plan)


def test_review_reextract_candidate_file_reports_valid_single_row(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    path.write_text(json.dumps(_candidate_review_entry()) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is True
    assert review.total_candidates == 1
    assert review.valid_candidates == 1
    assert review.invalid_candidates == 0
    assert review.duplicate_note_ids == ()
    assert review.blocker_tags_seen == ("walk_forward_fail",)
    assert review.promotion_allowed is False
    assert review.queue_insertion_allowed is False
    assert review.active_generation_still_blocked is True


def test_review_reextract_candidate_file_reports_multiple_valid_rows(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    rows = [
        _candidate_review_entry(),
        _candidate_review_entry(
            note_id="note-2222222222222222",
            source_location="page:13",
            source_passage_id="passage-2222222222222222",
            blocker_tags=["drawdown_fail", "walk_forward_fail"],
        ),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is True
    assert review.total_candidates == 2
    assert review.valid_candidates == 2
    assert review.invalid_candidates == 0
    assert review.duplicate_note_ids == ()
    assert review.blocker_tags_seen == ("drawdown_fail", "walk_forward_fail")


def test_review_reextract_candidate_file_rejects_invalid_json(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 1
    assert review.valid_candidates == 0
    assert review.invalid_candidates == 1
    assert review.duplicate_note_ids == ()
    assert review.blocker_tags_seen == ()


def test_review_reextract_candidate_file_rejects_empty_file(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    path.write_text("", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 0
    assert review.valid_candidates == 0
    assert review.invalid_candidates == 0
    assert review.duplicate_note_ids == ()
    assert review.blocker_tags_seen == ()
    assert review.promotion_allowed is False
    assert review.queue_insertion_allowed is False
    assert review.active_generation_still_blocked is True


def test_review_reextract_candidate_file_rejects_source_excerpt_extra_key(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    row = _candidate_review_entry(source_excerpt="private excerpt")
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 1
    assert review.valid_candidates == 0
    assert review.invalid_candidates == 1


def test_review_reextract_candidate_file_rejects_missing_required_field(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    row = _candidate_review_entry()
    row.pop("thesis")
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 1
    assert review.valid_candidates == 0
    assert review.invalid_candidates == 1


def test_review_reextract_candidate_file_rejects_duplicate_note_ids(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    rows = [
        _candidate_review_entry(),
        _candidate_review_entry(
            source_location="page:99",
            source_passage_id="passage-9999999999999999",
        ),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 2
    assert review.valid_candidates == 1
    assert review.invalid_candidates == 1
    assert review.duplicate_note_ids == ("note-1111111111111111",)
    assert review.blocker_tags_seen == ("walk_forward_fail",)


def test_review_reextract_candidate_file_rejects_unknown_blocker_tags(tmp_path):
    path = tmp_path / "candidate-output.jsonl"
    row = _candidate_review_entry(blocker_tags=["walk_forward_fail", "mystery_blocker"])
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    review = knowledge_runtime.review_reextract_candidate_file(path)

    assert review.review_valid is False
    assert review.total_candidates == 1
    assert review.valid_candidates == 0
    assert review.invalid_candidates == 1
    assert review.duplicate_note_ids == ()
    assert review.blocker_tags_seen == ("walk_forward_fail",)


def test_promote_reextract_candidate_refuses_empty_candidate_file(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text("", encoding="utf-8")

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_attempted is True
    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False
    assert promotion.promoted_note_id is None
    assert promotion.target_blocker is None
    assert promotion.target_file_relative is None
    assert promotion.explicit_promotion_used is False
    assert promotion.active_generation_still_blocked is True
    assert promotion.queue_insertion_allowed is False
    assert promotion.provider_calls_used == 0


def test_promote_reextract_candidate_refuses_missing_selected_note_id(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(json.dumps(_candidate_review_entry()) + "\n", encoding="utf-8")

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-ffffffffffffffff",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False
    assert promotion.promoted_note_id is None


def test_promote_reextract_candidate_refuses_duplicate_note_id_candidate_file(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    rows = [
        _candidate_review_entry(),
        _candidate_review_entry(
            source_location="page:22",
            source_passage_id="passage-2222222222222222",
        ),
    ]
    candidate_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False


def test_promote_reextract_candidate_refuses_source_excerpt_extra_key(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(_candidate_review_entry(source_excerpt="private excerpt")) + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False


def test_promote_reextract_candidate_refuses_unknown_blocker_tag(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(_candidate_review_entry(blocker_tags=["mystery_blocker"])) + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False


def test_promote_reextract_candidate_refuses_empty_blocker_tags(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(_candidate_review_entry(blocker_tags=[])) + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False


def test_promote_reextract_candidate_refuses_multiple_blocker_tags(tmp_path):
    base = _promotion_base(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(
            _candidate_review_entry(blocker_tags=["drawdown_fail", "walk_forward_fail"])
        )
        + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False


def test_promote_reextract_candidate_refuses_existing_active_note_id(tmp_path):
    base = _promotion_fixture(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(_resolved_candidate_review_entry(base)) + "\n",
        encoding="utf-8",
    )
    target_path = base / "extracted_notes" / "walk_forward_robustness.jsonl"
    target_path.write_text(
        json.dumps(_audit_entry(addresses_blockers=["walk_forward_robustness"])) + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False
    assert len(_read_jsonl(target_path)) == 1


def test_promote_reextract_candidate_refuses_unresolvable_current_source_identity(tmp_path):
    base = _promotion_fixture(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    unresolved = _resolved_candidate_review_entry(base)
    unresolved["source_passage_id"] = "passage-ffffffffffffffff"
    candidate_path.write_text(json.dumps(unresolved) + "\n", encoding="utf-8")

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    assert promotion.promotion_allowed is False
    assert promotion.promotion_succeeded is False
    assert promotion.promoted_note_id is None


def test_promote_reextract_candidate_writes_exactly_one_active_note(tmp_path):
    base = _promotion_fixture(tmp_path)
    candidate_path = tmp_path / "candidate-output.jsonl"
    candidate_path.write_text(
        json.dumps(_resolved_candidate_review_entry(base)) + "\n",
        encoding="utf-8",
    )

    promotion = knowledge_runtime.promote_reextract_candidate(
        base_dir=base,
        input_path=candidate_path,
        note_id="note-1111111111111111",
    )

    target_path = base / "extracted_notes" / "walk_forward_robustness.jsonl"
    rows = _read_jsonl(target_path)
    assert promotion.promotion_attempted is True
    assert promotion.promotion_allowed is True
    assert promotion.promotion_succeeded is True
    assert promotion.promoted_note_id == "note-1111111111111111"
    assert promotion.target_blocker == "walk_forward_robustness"
    assert promotion.target_file_relative == "extracted_notes/walk_forward_robustness.jsonl"
    assert promotion.explicit_promotion_used is True
    assert promotion.active_generation_still_blocked is True
    assert promotion.queue_insertion_allowed is False
    assert promotion.provider_calls_used == 0
    assert len(rows) == 1
    entry = validate_entry(rows[0])
    assert entry["note_id"] == "note-1111111111111111"
    assert entry["hypothesis"] == "Stable parameter neighborhoods improve walk-forward reliability."
    assert entry["summary"] == "Prefer broad stable regions over isolated optima."
    assert entry["implementation_hint"] == "Measure adjacent parameter dispersion."
    assert entry["addresses_blockers"] == ["walk_forward_robustness"]
    assert entry["source_excerpt"] == ""
    context = load_book_knowledge_context(
        book_index_path=base / "index" / "book_index.json",
        notes_dir=base / "extracted_notes",
        dominant_blocker="walk_forward_robustness",
    )
    assert context.selected_note_ids == ("note-1111111111111111",)


def test_controlled_reextraction_run_plan_reports_safe_default_dry_run_noop():
    plan = plan_controlled_reextraction_run(
        output_path="candidate-output.jsonl",
        max_books=2,
        max_passages_per_book=3,
        max_notes=4,
    )

    assert plan.command == "reextract-run"
    assert plan.dry_run is True
    assert plan.aborted is False
    assert plan.abort_reason == "none"
    assert plan.provider_allowed is False
    assert plan.provider_attempted is False
    assert plan.provider_calls_used == 0
    assert plan.max_books == 2
    assert plan.max_passages_per_book == 3
    assert plan.max_notes == 4
    assert plan.max_provider_calls == 0
    assert plan.output_path == "candidate-output.jsonl"
    assert plan.timestamped_output_required is True
    assert plan.overwrite_allowed is False
    assert plan.notes_generated == 0
    assert plan.notes_written == 0
    assert plan.notes_schema_valid == 0
    assert plan.notes_schema_invalid == 0
    assert plan.post_generation_audit_required is True
    assert plan.post_generation_audit_run is False
    assert plan.promotion_allowed is False
    assert plan.queue_insertion_allowed is False
    assert plan.generation_still_blocked is True


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"dry_run": False}, "dry_run_required"),
        ({"allow_provider_calls": True}, "provider_required"),
        ({"max_provider_calls": 1}, "allow_provider_calls_required"),
        ({}, "output_path_required"),
        ({"overwrite_requested": True}, "overwrite_forbidden"),
        ({"promotion_requested": True}, "promotion_forbidden"),
        ({"queue_insertion_requested": True}, "queue_insertion_forbidden"),
    ],
)
def test_controlled_reextraction_run_plan_fails_closed(kwargs, reason):
    base_kwargs = {
        "output_path": "candidate-output.jsonl",
        "max_books": 2,
        "max_passages_per_book": 3,
        "max_notes": 4,
    }
    if reason == "output_path_required":
        base_kwargs.pop("output_path")
    base_kwargs.update(kwargs)

    plan = plan_controlled_reextraction_run(**base_kwargs)

    assert plan.aborted is True
    assert plan.abort_reason == reason
    assert plan.provider_attempted is False
    assert plan.provider_calls_used == 0
    assert plan.notes_generated == 0
    assert plan.notes_written == 0
    assert plan.notes_schema_valid == 0
    assert plan.notes_schema_invalid == 0


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"allow_provider_calls": True, "provider": ""}, "provider_required"),
        ({"allow_provider_calls": True, "provider": "command", "model": ""}, "model_required"),
        (
            {
                "allow_provider_calls": True,
                "provider": "command",
                "model": "test-model",
                "max_provider_calls": 2,
            },
            "max_provider_calls_must_equal_one",
        ),
        (
            {
                "allow_provider_calls": True,
                "provider": "command",
                "model": "test-model",
                "max_provider_calls": 1,
                "max_books": 2,
            },
            "max_books_must_equal_one",
        ),
        (
            {
                "allow_provider_calls": True,
                "provider": "command",
                "model": "test-model",
                "max_provider_calls": 1,
                "max_passages_per_book": 2,
            },
            "max_passages_per_book_invalid",
        ),
        (
            {
                "allow_provider_calls": True,
                "provider": "command",
                "model": "test-model",
                "max_provider_calls": 1,
                "max_notes": 2,
            },
            "max_notes_invalid",
        ),
    ],
)
def test_controlled_reextraction_run_live_gate_requires_explicit_contract(kwargs, reason):
    base_kwargs = {
        "output_path": "candidate-output.jsonl",
        "max_books": 1,
        "max_passages_per_book": 1,
        "max_notes": 1,
        "max_provider_calls": 1,
        "allow_provider_calls": True,
        "provider": "command",
        "model": "test-model",
    }
    base_kwargs.update(kwargs)

    plan = plan_controlled_reextraction_run(**base_kwargs)

    assert plan.aborted is True
    assert plan.abort_reason == reason
    assert plan.provider_attempted is False
    assert plan.provider_calls_used == 0


def test_controlled_reextraction_run_live_gate_accepts_single_call_dry_run_contract():
    plan = plan_controlled_reextraction_run(
        output_path="candidate-output.jsonl",
        max_books=1,
        max_passages_per_book=3,
        max_notes=3,
        max_provider_calls=1,
        allow_provider_calls=True,
        provider="command",
        model="test-model",
    )

    assert plan.aborted is False
    assert plan.abort_reason == "none"
    assert plan.provider_allowed is True
    assert plan.provider_attempted is False
    assert plan.provider_calls_used == 0
    assert plan.max_provider_calls == 1


def test_controlled_reextraction_run_plan_does_not_write_or_expose_private_values(tmp_path):
    notes_dir = tmp_path / "extracted_notes"
    notes_dir.mkdir(parents=True)
    path = notes_dir / "notes.jsonl"
    path.write_text(json.dumps(_audit_entry()) + "\n", encoding="utf-8")
    before = path.read_bytes()

    plan = plan_controlled_reextraction_run(
        output_path="candidate-output.jsonl",
        max_books=2,
        max_passages_per_book=3,
        max_notes=4,
    )

    assert path.read_bytes() == before
    assert "book-aaaaaaaaaaaa" not in repr(plan)
    assert "Risk Management Systems" not in repr(plan)
    assert "private-book:book-aaaaaaaaaaaa" not in repr(plan)


@pytest.mark.parametrize(
    ("evidence", "claim"),
    [
        ("The system was profitable.", "The system was profitable."),
        ("The system was not profitable.", "The system was not profitable."),
        (
            "Walk-forward validation failed.",
            "Walk-forward validation failed.",
        ),
        (
            "The system was robust out-of-sample.",
            "The system was robust out-of-sample.",
        ),
        (
            "The model was tested on unseen markets.",
            "The model generalizes to unseen markets.",
        ),
        (
            "The model was tested out-of-sample.",
            "The model generalizes to unseen markets.",
        ),
    ],
)
def test_same_category_and_polarity_sensitive_claim_is_allowed(evidence, claim):
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis=claim,
        summary="Use a fixed moving-average rule.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(
        provider_note,
        passage=_passage(text=evidence),
    )

    assert diagnostics == []
    assert len(proposals) == 1


def test_sensitive_claim_does_not_use_title_or_blocker_as_evidence():
    passage = _passage(
        text=(
            "This section describes a moving-average rule and avoiding optimization."
        ),
        source_title="Profitable Futures Walk-Forward Handbook",
        blocker="walk_forward_fail",
    )
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="The system is profitable and walk-forward robust.",
        summary="Use a fixed moving-average rule.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["generic_risk:unknown"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note, passage=passage)

    assert proposals == []
    assert [item.code for item in diagnostics] == ["grounding_violation"]


def test_unsupported_sensitive_failure_modes_are_removed_without_blocker_support():
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule.",
        summary="Avoid optimizing the moving-average length.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["walk_forward_fail", "failed out-of-sample"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(provider_note)

    assert diagnostics == []
    assert proposals[0]["entry"]["known_failure_modes"] == [
        "generic_risk:unknown"
    ]


@pytest.mark.parametrize(
    ("evidence", "expected_failure_modes"),
    [
        ("Walk-forward validation passed.", ["generic_risk:unknown"]),
        ("Walk-forward validation failed.", ["walk_forward_fail"]),
    ],
)
def test_sensitive_failure_mode_requires_matching_evidence_polarity(
    evidence, expected_failure_modes
):
    provider_note = _provider_note(
        concept="Fixed moving-average rule",
        hypothesis="Use a fixed moving-average rule.",
        summary="Keep the moving-average length fixed.",
        testable_rules=["Use one fixed moving-average length selected before testing."],
        asset_classes=["unknown"],
        timeframes=["not_specified_in_evidence"],
        expected_edge="unknown",
        known_failure_modes=["walk_forward_fail"],
        implementation_hint="Keep the moving-average length fixed.",
    )

    proposals, diagnostics, _ = _generate_grounded(
        provider_note,
        passage=_passage(text=evidence),
    )

    assert diagnostics == []
    assert proposals[0]["entry"]["known_failure_modes"] == expected_failure_modes
