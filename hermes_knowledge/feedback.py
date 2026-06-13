"""Deterministic priority overlays from later experiment outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import tempfile
import os
from typing import Any, Iterable, Mapping

from hermes_knowledge.note_store import _atomic_jsonl, _ensure_private_path
from hermes_knowledge.schema import load_knowledge_jsonl


WF_WEIGHT = 40.0
DRAWDOWN_WEIGHT = 30.0
GATE_WEIGHT = 5.0
MAX_EVENT_DELTA = 20.0
MAX_PRIORITY_OVERLAY = 50.0


@dataclass(frozen=True)
class FeedbackSummary:
    accepted: int
    rejected: int
    duplicates: int


def _metric(event: Mapping[str, Any], key: str) -> float | None:
    value = event.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"invalid metric: {key}")
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1.0:
        raise ValueError(f"invalid metric: {key}")
    return number


def _validate_event(event: Mapping[str, Any]) -> tuple[str, list[str]]:
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
        raise ValueError("invalid event_id")
    note_ids = event.get("used_note_ids")
    if (
        not isinstance(note_ids, list)
        or not 1 <= len(note_ids) <= 5
        or any(
            not isinstance(note_id, str)
            or not re.fullmatch(r"note-[0-9a-fA-F]{16}", note_id)
            for note_id in note_ids
        )
    ):
        raise ValueError("invalid used_note_ids")
    gate = event.get("gate_passed")
    if gate is not None and not isinstance(gate, bool):
        raise ValueError("invalid gate_passed")
    for key in (
        "baseline_wf_pass_rate",
        "wf_pass_rate",
        "baseline_max_drawdown",
        "max_drawdown",
    ):
        _metric(event, key)
    return event_id.strip(), list(dict.fromkeys(note_ids))


def feedback_delta(event: Mapping[str, Any]) -> float:
    _validate_event(event)
    baseline_wf = _metric(event, "baseline_wf_pass_rate")
    resulting_wf = _metric(event, "wf_pass_rate")
    baseline_dd = _metric(event, "baseline_max_drawdown")
    resulting_dd = _metric(event, "max_drawdown")
    delta = 0.0
    if baseline_wf is not None and resulting_wf is not None:
        delta += (resulting_wf - baseline_wf) * WF_WEIGHT
    if baseline_dd is not None and resulting_dd is not None:
        delta += (abs(baseline_dd) - abs(resulting_dd)) * DRAWDOWN_WEIGHT
    if event.get("gate_passed") is True:
        delta += GATE_WEIGHT
    elif event.get("gate_passed") is False:
        delta -= GATE_WEIGHT
    return round(max(-MAX_EVENT_DELTA, min(MAX_EVENT_DELTA, delta)), 4)


def load_priority_overlays(path: str | Path) -> dict[str, dict[str, float]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"notes": {}, "books": {}}
    result: dict[str, dict[str, float]] = {"notes": {}, "books": {}}
    for group in result:
        values = payload.get(group, {}) if isinstance(payload, dict) else {}
        if isinstance(values, dict):
            result[group] = {
                str(key): float(value)
                for key, value in values.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
    return result


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = _ensure_private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _existing_events(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.exists():
        return [], set()
    rows: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
            if isinstance(row.get("event_id"), str):
                event_ids.add(row["event_id"])
    return rows, event_ids


def apply_feedback(
    events: Iterable[Mapping[str, Any]],
    *,
    note_to_book: Mapping[str, str],
    event_path: str | Path,
    priorities_path: str | Path,
) -> FeedbackSummary:
    event_destination = Path(event_path)
    priority_destination = Path(priorities_path)
    existing_events, seen_event_ids = _existing_events(event_destination)
    overlays = load_priority_overlays(priority_destination)
    notes = dict(overlays["notes"])
    accepted_rows: list[dict[str, Any]] = []
    rejected = 0
    duplicates = 0
    for raw in events:
        try:
            event_id, note_ids = _validate_event(raw)
            delta = feedback_delta(raw)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if event_id in seen_event_ids:
            duplicates += 1
            continue
        seen_event_ids.add(event_id)
        for note_id in note_ids:
            current = float(notes.get(note_id, 0.0))
            notes[note_id] = round(
                max(
                    -MAX_PRIORITY_OVERLAY,
                    min(MAX_PRIORITY_OVERLAY, current + delta),
                ),
                4,
            )
        accepted_rows.append({**dict(raw), "priority_delta": delta})
    if accepted_rows:
        by_book: dict[str, list[float]] = {}
        for note_id, value in notes.items():
            book_id = note_to_book.get(note_id)
            if book_id:
                by_book.setdefault(book_id, []).append(value)
        books = {
            book_id: round(sum(values) / len(values), 4)
            for book_id, values in sorted(by_book.items())
        }
        _atomic_jsonl(event_destination, [*existing_events, *accepted_rows])
        _atomic_json(
            priority_destination,
            {"schema_version": 1, "notes": notes, "books": books},
        )
    return FeedbackSummary(len(accepted_rows), rejected, duplicates)


def note_book_map(extracted_dir: str | Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in sorted(Path(extracted_dir).glob("*.jsonl")):
        for entry in load_knowledge_jsonl(path):
            if entry.get("note_id"):
                mapping[str(entry["note_id"])] = str(entry["book_id"])
    return mapping
