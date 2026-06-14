from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from research_lab.orchestration.schemas import BLOCKER_PRIORITY, canonical_blockers


@dataclass(frozen=True)
class BlockerSelection:
    selected_blocker: str | None
    blocker_counts: dict[str, int]
    selected_reason: str


def select_blocker(blockers: list[str] | tuple[str, ...]) -> BlockerSelection:
    allowed = canonical_blockers()
    counts = Counter(blocker for blocker in blockers if blocker in allowed)
    ordered_counts = {blocker: counts[blocker] for blocker in BLOCKER_PRIORITY if counts.get(blocker, 0) > 0}
    if not ordered_counts:
        return BlockerSelection(selected_blocker=None, blocker_counts=ordered_counts, selected_reason="no_valid_blockers")

    highest_count = max(ordered_counts.values())
    tied = [blocker for blocker, count in ordered_counts.items() if count == highest_count]
    if len(tied) == 1:
        reason = "most_frequent_blocker"
        selected = tied[0]
    else:
        selected = next(blocker for blocker in BLOCKER_PRIORITY if blocker in tied)
        reason = "priority_tie_break"
    return BlockerSelection(selected_blocker=selected, blocker_counts=ordered_counts, selected_reason=reason)
