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

PROFITABILITY_POSITIVE_PATTERNS = (
    r"\bprofitability\b",
    r"\bprofitable\b",
    r"\bgenerat(?:e|es|ed|ing) profits?\b",
    r"\b(?:make|makes|making|made) money\b",
    r"\bpositive expect(?:ancy|ation)\b",
    r"\bpositive expected returns?\b",
    r"\bpositive returns?\b",
    r"\bpositive edge\b",
    r"\bhas an edge\b",
    r"\bmarket edge\b",
    r"\balpha\b",
    r"\boutperform(?:s|ed|ing|ance)?\b",
)

PROFITABILITY_NEGATIVE_PATTERNS = (
    r"\bnot profitable\b",
    r"\bunprofitable\b",
    r"\blost money\b",
    r"\blos(?:e|es|ing) money\b",
    r"\bnegative expect(?:ancy|ation)\b",
    r"\bnegative expected returns?\b",
    r"\bnegative returns?\b",
    r"\bno edge\b",
    r"\bwithout (?:an )?edge\b",
    r"\bfailed to make money\b",
    r"\bdid not make money\b",
    r"\bdidn t make money\b",
)

WALK_FORWARD_FAILED_PATTERNS = (
    r"\bwalk ?forward\b.*\b(?:fail(?:s|ed|ure)?|poor|weak|insufficient)\b",
    r"\b(?:fail(?:s|ed|ure)?|poor|weak|insufficient)\b.*\bwalk ?forward\b",
    r"\bwalk ?forward robustness below target\b",
)

WALK_FORWARD_PASSED_PATTERNS = (
    r"\bwalk ?forward\b.*\b(?:pass(?:es|ed)?|validat(?:e|es|ed)|robust(?:ness)?|stable|reliab(?:le|ility))\b",
    r"\b(?:pass(?:es|ed)?|validat(?:e|es|ed)|robust(?:ness)?|stable|reliab(?:le|ility))\b.*\bwalk ?forward\b",
)

OOS_FAILED_PATTERNS = (
    r"\b(?:out of sample|oos)\b.*\b(?:fail(?:s|ed|ure)?|poor|weak)\b",
    r"\b(?:fail(?:s|ed|ure)?|poor|weak)\b.*\b(?:out of sample|oos)\b",
)

OOS_PASSED_PATTERNS = (
    r"\b(?:out of sample|oos)\b.*\b(?:pass(?:es|ed)?|validat(?:e|es|ed)|robust|strong|stable)\b",
    r"\b(?:pass(?:es|ed)?|validat(?:e|es|ed)|robust|strong|stable)\b.*\b(?:out of sample|oos)\b",
)

GENERALIZATION_PATTERNS = (
    r"\bgeneraliz(?:e|es|ed|ation)\b",
    r"\bworks? on (?:unseen|new) markets\b",
    r"\b(?:future|unseen) data\b",
    r"\b(?:new|unseen) markets\b",
)

GENERALIZATION_EVIDENCE_PATTERNS = GENERALIZATION_PATTERNS + (
    r"\b(?:tested|validated) on unseen\b",
    r"\btested out of sample\b",
)

IN_SAMPLE_ROBUSTNESS_PATTERNS = (
    r"\b(?:robust|stable|parameter stable) (?:in|within) (?:the )?(?:original )?sample\b",
    r"\bin sample (?:robustness|stability)\b",
)

OOS_ROBUSTNESS_PATTERNS = (
    r"\b(?:robust|stable) (?:out of sample|oos)\b",
    r"\b(?:out of sample|oos) (?:robustness|stability)\b",
)

SENSITIVE_CLAIM_PATTERNS = {
    "profitability_positive": PROFITABILITY_POSITIVE_PATTERNS,
    "profitability_negative": PROFITABILITY_NEGATIVE_PATTERNS,
    "walk_forward_failed": WALK_FORWARD_FAILED_PATTERNS,
    "walk_forward_passed": WALK_FORWARD_PASSED_PATTERNS,
    "oos_failed": OOS_FAILED_PATTERNS,
    "oos_passed": OOS_PASSED_PATTERNS,
    "generalization_claim": GENERALIZATION_PATTERNS,
    "in_sample_robustness_claim": IN_SAMPLE_ROBUSTNESS_PATTERNS,
    "oos_robustness_claim": OOS_ROBUSTNESS_PATTERNS,
}


@dataclass(frozen=True)
class NoteGenerationDiagnostic:
    passage_id: str
    code: str
    message: str
    reason: str = "none"


class GroundingValidationError(ValueError):
    pass


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(_normalize(phrase) in text for phrase in phrases)


def _matches_any_pattern(value: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize(value)
    return any(re.search(pattern, normalized) for pattern in patterns)


def _classify_sensitive_claims(value: str) -> set[str]:
    claims = {
        category
        for category, patterns in SENSITIVE_CLAIM_PATTERNS.items()
        if _matches_any_pattern(value, patterns)
    }
    if "profitability_negative" in claims:
        claims.discard("profitability_positive")
    return claims


def _evidence_claim_support(value: str) -> set[str]:
    support = _classify_sensitive_claims(value)
    contradictory_pairs = (
        ("profitability_positive", "profitability_negative"),
        ("walk_forward_failed", "walk_forward_passed"),
        ("oos_failed", "oos_passed"),
    )
    for left, right in contradictory_pairs:
        if left in support and right in support:
            support.difference_update((left, right))
    if _matches_any_pattern(value, GENERALIZATION_EVIDENCE_PATTERNS):
        support.add("generalization_claim")
    if "oos_passed" in support:
        support.add("generalization_claim")
    return support


def _unsupported_sensitive_claim(value: str, evidence_support: set[str]) -> bool:
    return not _classify_sensitive_claims(value).issubset(evidence_support)


def _asset_class_supported(asset_class: str, evidence: str) -> bool:
    normalized_class = _normalize(asset_class)
    if normalized_class == "unknown":
        return True
    terms = ASSET_CLASS_TERMS.get(normalized_class, (normalized_class,))
    return _contains_any(evidence, terms)


def _failure_mode_supported(
    failure_mode: str, evidence: str, evidence_support: set[str]
) -> bool:
    normalized = _normalize(failure_mode)
    if normalized == "generic risk unknown":
        return True
    sensitive_claims = _classify_sensitive_claims(failure_mode)
    if sensitive_claims:
        return sensitive_claims.issubset(evidence_support)
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
    evidence_support = _evidence_claim_support(evidence)
    for field in ("hypothesis", "summary"):
        value = provider_note.get(field)
        if not isinstance(value, str):
            continue
        if _unsupported_sensitive_claim(value, evidence_support):
            raise GroundingValidationError(
                f"unsupported sensitive claim in {field}"
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
        and "profitability_positive" in _classify_sensitive_claims(expected_edge)
        and "profitability_positive" not in evidence_support
    ):
        grounded["expected_edge"] = "unknown"

    failure_modes = provider_note.get("known_failure_modes")
    if isinstance(failure_modes, list):
        supported_modes = [
            value
            for value in failure_modes
            if isinstance(value, str)
            and _failure_mode_supported(value, evidence, evidence_support)
        ]
        grounded["known_failure_modes"] = supported_modes or ["generic_risk:unknown"]
    return grounded


def _prompt(candidate: PassageCandidate) -> str:
    contract = {
        "concept": "string",
        "hypothesis": "string",
        "summary": "string",
        "testable_rules": ["string"],
        "compatible_builders": ["string"],
        "asset_classes": ["string"],
        "timeframes": ["string"],
        "expected_edge": "string",
        "known_failure_modes": ["string"],
        "implementation_hint": "string",
        "priority_score": 0,
    }
    return "\n".join(
        [
            "Convert the bounded book evidence below into exactly one JSON object.",
            "Return exactly one JSON object and nothing else.",
            "Do not return prose, markdown, code fences, comments, or extra keys.",
            "Use this exact top-level object shape and key names:",
            json.dumps(contract, ensure_ascii=True, sort_keys=True),
            "All non-array fields must be strings except priority_score, which must be a number from 0 to 100.",
            "testable_rules, compatible_builders, asset_classes, timeframes, and known_failure_modes must each be arrays of non-empty strings.",
            "Do not use null, booleans, nested objects, nested arrays, or placeholder keys.",
            "The note must be concise, testable, and must not relax validation gates.",
            "Do not return executable code, leverage expansion, private paths, or generic advice.",
            "Ground every claim only in the Evidence text below, not the blocker, title, book metadata, or general knowledge.",
            "Sensitive performance and validation claims require explicit Evidence support of the same claim type and polarity.",
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


def _schema_violation_reason(exc: Exception) -> str:
    message = str(exc).casefold()
    if "missing required fields" in message or "missing proposed note provenance" in message:
        return "missing_required_field"
    if "unexpected fields" in message or "unexpected proposed note fields" in message:
        return "extra_key"
    if "invalid provider fields:" in message:
        if "missing=" in message:
            return "missing_required_field"
        if "extra=" in message:
            return "extra_key"
    if (
        "must be an object" in message
        or "must be text" in message
        or "must be non-empty text" in message
        or "must be an array" in message
        or "must contain non-empty strings" in message
        or "must be numeric" in message
    ):
        return "invalid_field_type"
    return "invalid_field_value"


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
                    reason=result.reason if result.reason != "none" else (result.status or "provider_error"),
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
                    reason="invalid_json",
                )
            )
            continue
        if not isinstance(provider_note, dict):
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "invalid_json",
                    "Provider output was not a JSON object.",
                    reason="invalid_json",
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
                    reason="grounding_violation",
                )
            )
        except (KnowledgeValidationError, KeyError, TypeError, ValueError) as exc:
            diagnostics.append(
                NoteGenerationDiagnostic(
                    candidate.passage_id,
                    "schema_violation",
                    "Provider note failed local schema validation.",
                    reason=_schema_violation_reason(exc),
                )
            )
    return proposals, diagnostics
