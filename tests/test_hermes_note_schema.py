import json

import pytest

from hermes_knowledge.runtime import audit_note_inventory
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
