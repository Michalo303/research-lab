"""Transform one bounded passage at a time into validated proposed notes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping

from hermes_knowledge.passage_extractor import PassageCandidate
from hermes_knowledge.schema import KnowledgeValidationError, validate_proposed_note
from research_lab.hermes.providers import ProviderResult, invoke_provider


ProviderInvoker = Callable[[str, str, Mapping[str, str]], ProviderResult]

PROVIDER_FIELDS = {
    "concept",
    "hypothesis",
    "summary",
    "testable_rules",
    "compatible_builders",
    "asset_classes",
    "timeframes",
    "expected_edge",
    "known_failure_modes",
    "implementation_hint",
    "priority_score",
}


@dataclass(frozen=True)
class NoteGenerationDiagnostic:
    passage_id: str
    code: str
    message: str


def _prompt(candidate: PassageCandidate) -> str:
    return "\n".join(
        [
            "Convert the bounded book evidence below into exactly one JSON object.",
            "Return JSON only, with exactly these fields:",
            ", ".join(sorted(PROVIDER_FIELDS)),
            "The note must be concise, testable, and must not relax validation gates.",
            "Do not return executable code, leverage expansion, private paths, or generic advice.",
            f"Blocker: {candidate.blocker}",
            f"Book ID: {candidate.book_id}",
            f"Book title: {candidate.source_title}",
            f"Location: {candidate.location}",
            f"Evidence: {candidate.text}",
        ]
    )


def _note_id(candidate: PassageCandidate, provider_note: dict[str, Any]) -> str:
    normalized = json.dumps(provider_note, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(
        "\n".join(
            (candidate.blocker, candidate.book_id, candidate.passage_id, normalized)
        ).encode("utf-8")
    ).hexdigest()
    return f"note-{digest[:16]}"


def _proposal(
    candidate: PassageCandidate, provider_note: dict[str, Any]
) -> dict[str, Any]:
    if set(provider_note) != PROVIDER_FIELDS:
        missing = sorted(PROVIDER_FIELDS - provider_note.keys())
        extra = sorted(provider_note.keys() - PROVIDER_FIELDS)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"extra={','.join(extra)}")
        raise KnowledgeValidationError("invalid provider fields: " + " ".join(details))
    entry = {
        "book_id": candidate.book_id,
        "source_title": candidate.source_title,
        "source_path": f"private-book:{candidate.book_id}",
        "source_sha256": candidate.source_sha256,
        "concept": provider_note["concept"],
        "hypothesis": provider_note["hypothesis"],
        "summary": provider_note["summary"],
        "source_excerpt": candidate.text[:280],
        "testable_rules": provider_note["testable_rules"],
        "compatible_builders": provider_note["compatible_builders"],
        "asset_classes": provider_note["asset_classes"],
        "timeframes": provider_note["timeframes"],
        "expected_edge": provider_note["expected_edge"],
        "known_failure_modes": provider_note["known_failure_modes"],
        "addresses_blockers": [candidate.blocker],
        "priority_score": provider_note["priority_score"],
        "note_id": _note_id(candidate, provider_note),
        "source_location": candidate.location,
        "source_passage_id": candidate.passage_id,
        "implementation_hint": provider_note["implementation_hint"],
    }
    return validate_proposed_note(
        {
            "status": "proposed",
            "source_passage_id": candidate.passage_id,
            "entry": entry,
        }
    )


def generate_proposed_notes(
    candidates: Iterable[PassageCandidate],
    *,
    provider: str,
    env: Mapping[str, str],
    provider_invoker: ProviderInvoker = invoke_provider,
) -> tuple[list[dict[str, Any]], list[NoteGenerationDiagnostic]]:
    proposals: list[dict[str, Any]] = []
    diagnostics: list[NoteGenerationDiagnostic] = []
    for candidate in candidates:
        result = provider_invoker(provider, _prompt(candidate), env)
        if result.status != "ok" or not result.output:
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    result.status or "provider_error",
                    "Provider did not return a usable note.",
                )
            )
            continue
        try:
            provider_note = json.loads(result.output)
        except (json.JSONDecodeError, TypeError):
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "invalid_json",
                    "Provider output was not valid JSON.",
                )
            )
            continue
        if not isinstance(provider_note, dict):
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "invalid_json",
                    "Provider output was not a JSON object.",
                )
            )
            continue
        try:
            proposals.append(_proposal(candidate, provider_note))
        except (KnowledgeValidationError, KeyError, TypeError, ValueError):
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "schema_violation",
                    "Provider note failed local schema validation.",
                )
            )
    return proposals, diagnostics
