"""Render curated hypothesis seeds, never source-book text, into Hermes prompts."""

from __future__ import annotations

from typing import Any, Iterable

from hermes_knowledge.retriever import retrieve_for_blocker
from hermes_knowledge.schema import forbidden_prompt_reference_field


def build_hermes_knowledge_prompt(
    entries: Iterable[dict[str, Any]], dominant_blocker: str, limit: int = 5
) -> str:
    safe_entries = [
        entry for entry in entries if not forbidden_prompt_reference_field(entry)
    ]
    selected = [
        entry
        for entry in retrieve_for_blocker(
            safe_entries, blocker=dominant_blocker, limit=limit
        )
        if not forbidden_prompt_reference_field(entry)
    ]
    if not selected:
        return ""
    lines = [
        "Hermes curated book-inspired hypothesis seeds",
        f"Dominant blocker: {dominant_blocker}",
        "Use only the entries below. Treat them as hypotheses to test, not facts.",
        "Preserve knowledge provenance in every proposed experiment.",
        "For each hypothesis, set used_note_ids to only the note IDs actually used; use [] when none were used.",
    ]
    for entry in selected:
        rules = "; ".join(entry["testable_rules"])
        lines.extend(
            [
                "",
                f"Concept: {entry['concept']}",
                f"Hypothesis: {entry['hypothesis']}",
                f"Testable rules: {rules}",
                f"Expected edge: {entry['expected_edge']}",
                (
                    "Knowledge provenance: "
                    f"note_id={entry.get('note_id', 'legacy')}; "
                    f"book_id={entry['book_id']}; "
                    f"title={entry['source_title']}; "
                    f"sha256={entry['source_sha256']}"
                ),
            ]
        )
    return "\n".join(lines)
