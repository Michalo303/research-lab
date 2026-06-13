import json

import pytest

from hermes_knowledge.note_generator import generate_proposed_notes
from hermes_knowledge.passage_extractor import PassageCandidate
from hermes_knowledge.schema import (
    KnowledgeValidationError,
    validate_entry,
    validate_proposed_note,
)
from research_lab.hermes.providers import ProviderResult


def _passage(marker: str = "1") -> PassageCandidate:
    return PassageCandidate(
        passage_id=f"passage-{marker * 16}",
        book_id="book-aaaaaaaaaaaa",
        source_title="Trading Systems and Methods",
        source_sha256="a" * 64,
        blocker="walk_forward_fail",
        location="page:214",
        matched_terms=("parameter stability", "robustness"),
        text="Broad stable parameter regions can be more robust than sharp optima.",
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
