from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_knowledge.blocker_taxonomy import canonicalize_blocker_id


MAX_DIAGNOSTIC_CHARS = 20_000


@dataclass(frozen=True)
class DiagnosticInput:
    path: Path | None
    text: str
    blocker: str


def read_diagnostic_input(root: Path) -> DiagnosticInput:
    immutable = sorted((root / "reports" / "runs").glob("*/*/daily_report.md"))
    daily = sorted((root / "reports" / "daily").glob("*.md"))
    path = immutable[-1] if immutable else (daily[-1] if daily else None)
    text = path.read_text(encoding="utf-8")[-MAX_DIAGNOSTIC_CHARS:] if path else ""
    return DiagnosticInput(path=path, text=text, blocker=dominant_blocker(text))


def dominant_blocker(report_text: str) -> str:
    for line in report_text.splitlines():
        match = re.match(r"\s*-\s*biggest risk discovered:\s*(.+)", line, flags=re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
            return canonicalize_blocker_id(raw) or raw
    signals = ("drawdown", "negative unseen", "walk-forward", "provider", "too few trades", "insufficient")
    for line in report_text.splitlines():
        clean = line.strip().removeprefix("-").strip()
        if clean and any(signal in clean.lower() for signal in signals):
            raw = clean.split(":", 1)[-1].strip()
            return canonicalize_blocker_id(raw) or raw
    return "no explicit blocker found"


def write_run_artifact(
    root: Path,
    artifact: dict[str, Any],
    *,
    timestamp: datetime | None = None,
    suffix: str | None = None,
) -> Path:
    timestamp_utc = _utc(timestamp)
    run_id = str(artifact.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("Hermes artifact requires run_id")
    path = run_artifact_path(root, run_id, timestamp_utc, suffix=suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    return path


def run_artifact_path(root: Path, run_id: str, timestamp: datetime, *, suffix: str | None = None) -> Path:
    filename = f"{run_id}.{suffix}.json" if suffix else f"{run_id}.json"
    return root / "reports" / "hermes" / "runs" / _utc(timestamp).date().isoformat() / filename


def latest_hermes_artifact(root: Path, *, before: datetime | None = None) -> dict[str, Any] | None:
    cutoff = _utc(before) if before is not None else None
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in (root / "reports" / "hermes" / "runs").glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            timestamp = _parse_timestamp(payload.get("timestamp_utc"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if cutoff is not None and timestamp > cutoff:
            continue
        candidates.append((timestamp, path, payload))
    if not candidates:
        return None
    _timestamp, path, payload = max(
        candidates,
        key=lambda item: (item[0], _artifact_phase_rank(item[2]), item[1].as_posix()),
    )
    result = dict(payload)
    result["artifact_path"] = _relative(root, path)
    return result


def _parse_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


def _artifact_phase_rank(payload: dict[str, Any]) -> int:
    return 0 if payload.get("artifact_phase") == "artifact_written" else 1


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
