"""Retrieve a small blocker-relevant set of hypothesis seeds with provenance."""

from __future__ import annotations

from typing import Any, Iterable

from hermes_knowledge.schema import validate_entry


BLOCKER_PREFERENCES: dict[str, tuple[str, ...]] = {
    "drawdown": (
        "volatility targeting",
        "cash fallback",
        "risk-off",
        "exposure cap",
        "position sizing",
    ),
    "walk_forward_robustness": (
        "simple",
        "parameter stability",
        "robust",
        "walk-forward",
        "monte carlo",
    ),
    "cost_stress": (
        "low-turnover",
        "turnover",
        "rebalance",
        "persistent signal",
    ),
}


def _entry_text(entry: dict[str, Any]) -> str:
    fields = [
        entry["concept"],
        entry["hypothesis"],
        entry["summary"],
        entry["expected_edge"],
        *entry["testable_rules"],
        *entry["known_failure_modes"],
    ]
    return " ".join(str(value) for value in fields).casefold()


def _normalize_blocker_tag(value: str) -> str:
    normalized = str(value).strip().casefold()
    if normalized == "drawdown_fail":
        return "drawdown"
    if normalized == "walk_forward_fail":
        return "walk_forward_robustness"
    return normalized


def retrieve_for_blocker(
    entries: Iterable[dict[str, Any]],
    blocker: str,
    limit: int = 5,
    *,
    note_priority_overlays: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be positive")
    normalized_blocker = blocker.strip().casefold()
    preferences = BLOCKER_PREFERENCES.get(normalized_blocker, ())
    validated = [validate_entry(raw) for raw in entries]
    direct_matches = [
        entry
        for entry in validated
        if normalized_blocker
        in {_normalize_blocker_tag(value) for value in entry["addresses_blockers"]}
    ]
    candidates = direct_matches or validated
    scored: list[tuple[float, str, dict[str, Any]]] = []
    overlays = note_priority_overlays or {}
    for entry in candidates:
        blockers = {_normalize_blocker_tag(value) for value in entry["addresses_blockers"]}
        text = _entry_text(entry)
        blocker_match = 40.0 if normalized_blocker in blockers else 0.0
        preference_match = sum(8.0 for phrase in preferences if phrase in text)
        overlay = max(
            -50.0,
            min(50.0, float(overlays.get(str(entry.get("note_id", "")), 0.0))),
        )
        score = (
            float(entry["priority_score"])
            + blocker_match
            + preference_match
            + overlay
        )
        scored.append((score, str(entry["concept"]).casefold(), entry))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]["book_id"]))
    return [item[2] for item in scored[:limit]]
