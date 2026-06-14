"""Transform one bounded passage at a time into validated proposed notes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
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

ASSET_CLASS_TERMS = {
    "equities": ("equity", "equities", "stock", "stocks"),
    "futures": ("future", "futures"),
    "fx": ("fx", "forex", "foreign exchange"),
    "etf": ("etf", "etfs", "exchange traded fund", "exchange traded funds"),
    "crypto": ("crypto", "cryptocurrency", "bitcoin", "ethereum"),
}

FAILURE_MODE_STOPWORDS = {
    "a",
    "after",
    "an",
    "and",
    "are",
    "as",
    "be",
    "can",
    "cause",
    "caused",
    "causes",
    "could",
    "during",
    "for",
    "from",
    "in",
    "is",
    "may",
    "might",
    "mode",
    "of",
    "on",
    "or",
    "risk",
    "still",
    "strategy",
    "system",
    "the",
    "to",
    "when",
    "with",
    "without",
}

POSITIVE_CLAIM_PATTERNS = (
    r"\bprofit(?:able|ability)\b",
    r"\bgenerat(?:e|es|ed|ing) profits?\b",
    r"\bmak(?:e|es|ing) money\b",
    r"\bpositive expect(?:ancy|ation)\b",
    r"\bpositive expected returns?\b",
    r"\bpositive returns?\b",
    r"\bpositive edge\b",
    r"\bhas an edge\b",
    r"\bmarket edge\b",
    r"\balpha\b",
    r"\boutperform(?:s|ed|ing|ance)?\b",
)

WALK_FORWARD_CLAIM_PATTERNS = (
    r"\bwalk forward\b",
    r"\bwalkforward\b",
)

OUT_OF_SAMPLE_CLAIM_PATTERNS = (
    r"\bout of sample\b",
    r"\boos\b",
)

ROBUSTNESS_CLAIM_PATTERNS = (
    r"\brobust\b",
    r"\brobustness\b",
    r"\bgeneralizes?\b",
    r"\bgeneralization\b",
)


@dataclass(frozen=True)
class NoteGenerationDiagnostic:
    passage_id: str
    code: str
    message: str


class GroundingValidationError(ValueError):
    pass


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(_normalize(phrase) in text for phrase in phrases)


def _matches_any_pattern(value: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize(value)
    return any(re.search(pattern, normalized) for pattern in patterns)


def _claims_positive_performance(value: str) -> bool:
    return _matches_any_pattern(value, POSITIVE_CLAIM_PATTERNS)


def _unsupported_validation_claim(value: str, evidence: str) -> bool:
    claim_groups = (
        WALK_FORWARD_CLAIM_PATTERNS,
        OUT_OF_SAMPLE_CLAIM_PATTERNS,
        ROBUSTNESS_CLAIM_PATTERNS,
    )
    return any(
        _matches_any_pattern(value, patterns)
        and not _matches_any_pattern(evidence, patterns)
        for patterns in claim_groups
    )


def _asset_class_supported(asset_class: str, evidence: str) -> bool:
    normalized_class = _normalize(asset_class)
    if normalized_class == "unknown":
        return True
    terms = ASSET_CLASS_TERMS.get(normalized_class, (normalized_class,))
    return _contains_any(evidence, terms)


def _failure_mode_supported(failure_mode: str, evidence: str) -> bool:
    normalized = _normalize(failure_mode)
    if normalized == "generic risk unknown":
        return True
    terms = {
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in FAILURE_MODE_STOPWORDS
    }
    return bool(terms) and all(term in evidence.split() for term in terms)


def _ground_provider_note(
    candidate: PassageCandidate, provider_note: dict[str, Any]
) -> dict[str, Any]:
    evidence = _normalize(candidate.text)
    for field in ("hypothesis", "summary"):
        value = provider_note.get(field)
        if not isinstance(value, str):
            continue
        if _unsupported_validation_claim(value, evidence):
            raise GroundingValidationError(
                f"unsupported validation robustness claim in {field}"
            )
        if _claims_positive_performance(value) and not _claims_positive_performance(
            evidence
        ):
            raise GroundingValidationError(
                f"unsupported positive-expectancy claim in {field}"
            )

    grounded = dict(provider_note)
    asset_classes = provider_note.get("asset_classes")
    if isinstance(asset_classes, list):
        supported_assets = [
            value
            for value in asset_classes
            if isinstance(value, str) and _asset_class_supported(value, evidence)
        ]
        grounded["asset_classes"] = supported_assets or ["unknown"]

    expected_edge = provider_note.get("expected_edge")
    if (
        isinstance(expected_edge, str)
        and _claims_positive_performance(expected_edge)
        and not _claims_positive_performance(evidence)
    ):
        grounded["expected_edge"] = "unknown"

    failure_modes = provider_note.get("known_failure_modes")
    if isinstance(failure_modes, list):
        supported_modes = [
            value
            for value in failure_modes
            if isinstance(value, str) and _failure_mode_supported(value, evidence)
        ]
        grounded["known_failure_modes"] = supported_modes or ["generic_risk:unknown"]
    return grounded


def _prompt(candidate: PassageCandidate) -> str:
    return "\n".join(
        [
            "Convert the bounded book evidence below into exactly one JSON object.",
            "Return JSON only, with exactly these fields:",
            ", ".join(sorted(PROVIDER_FIELDS)),
            "The note must be concise, testable, and must not relax validation gates.",
            "Do not return executable code, leverage expansion, private paths, or generic advice.",
            "Ground every claim only in the Evidence text below, not the blocker, title, book metadata, or general knowledge.",
            "Do not claim positive expectancy unless the passage explicitly supports it; otherwise use expected_edge=unknown.",
            "Do not infer asset classes unless the passage names them; otherwise use asset_classes=[\"unknown\"].",
            "Do not claim walk-forward failure unless the passage explicitly states or directly supports it.",
            "Do not add regime or volatility failure modes unless the passage supports them.",
            "For unsupported failure modes use known_failure_modes=[\"generic_risk:unknown\"].",
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
            grounded_note = _ground_provider_note(candidate, provider_note)
            proposals.append(_proposal(candidate, grounded_note))
        except GroundingValidationError:
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "grounding_violation",
                    "Provider note made a material claim unsupported by the passage.",
                )
            )
        except (KnowledgeValidationError, KeyError, TypeError, ValueError):
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "schema_violation",
                    "Provider note failed local schema validation.",
                )
            )
    return proposals, diagnostics
