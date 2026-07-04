from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Iterator


def iter_jsonl(
    path: Path,
    *,
    malformed_count: dict[str, int] | None = None,
    malformed_key: str = "invalid",
) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                _increment(malformed_count, malformed_key)
                continue
            if not isinstance(item, dict):
                _increment(malformed_count, malformed_key)
                continue
            yield item


def tail_jsonl(path: Path, max_rows: int) -> list[dict[str, Any]]:
    if max_rows <= 0 or not path.exists():
        return []
    tail: deque[str] = deque(maxlen=max_rows)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            tail.append(line)
    rows = []
    for line in tail:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _increment(counts: dict[str, int] | None, key: str) -> None:
    if counts is not None:
        counts[key] = counts.get(key, 0) + 1
