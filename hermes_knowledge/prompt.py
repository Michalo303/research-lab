"""Render curated hypothesis seeds, never source-book text, into Hermes prompts."""

from __future__ import annotations

from typing import Any, Iterable

from hermes_knowledge.retriever import retrieve_for_blocker


def build_hermes_knowledge_prompt(
    entries: Iterable[dict[str, Any]], dominant_blocker: str, limit: int = 5
) -> str:
    selected = retrieve_for_blocker(entries, blocker=dominant_blocker, limit=limit)
    lines = [
        "Hermes curated book-inspired hypothesis seeds",
        f"Dominant blocker: {dominant_blocker}",
        "Use only the entries below. Treat them as hypotheses to test, not facts.",
        "Preserve knowledge provenance in every proposed experiment.",
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
                    f"book_id={entry['book_id']}; "
                    f"title={entry['source_title']}; "
                    f"sha256={entry['source_sha256']}"
                ),
            ]
        )
    return "\n".join(lines)
