from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEMANTIC_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "family": ("family",),
    "asset_class": ("asset_class", "asset", "asset_type"),
    "timeframe": ("timeframe",),
    "template": ("template", "template_id", "strategy_template", "builder", "short_name"),
    "title": ("title", "hypothesis"),
    "symbol": ("symbol", "ticker"),
    "universe": ("universe", "symbols", "tickers", "assets"),
    "parameters": ("parameters", "params"),
    "filters": ("filters", "filter"),
    "risk_controls": ("risk_controls", "risk_control", "risk"),
    "rules": ("rules", "rule", "entry_rules", "exit_rules"),
    "data_provider": ("data_provider", "data_source", "provider_requirements", "data_requirements"),
}

DEFINITION_FIELDS = {
    "title",
    "symbol",
    "universe",
    "template",
    "parameters",
    "filters",
    "risk_controls",
    "rules",
}


@dataclass
class QueueDedupeResult:
    input_count: int = 0
    retained_count: int = 0
    duplicate_count: int = 0
    malformed_count: int = 0
    fingerprints_generated: int = 0
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    applied: bool = False
    retained_records: list[dict[str, Any]] = field(default_factory=list)
    duplicate_records: list[dict[str, Any]] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_count": self.input_count,
            "retained_count": self.retained_count,
            "duplicate_count": self.duplicate_count,
            "malformed_count": self.malformed_count,
            "fingerprints_generated": self.fingerprints_generated,
            "fingerprints": list(self.fingerprints),
            "duplicate_groups": list(self.duplicate_groups),
            "reasons": dict(self.reasons),
            "warnings": list(self.warnings),
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "applied": self.applied,
        }


@dataclass
class _QueueLine:
    raw_line: str
    line_number: int
    record: dict[str, Any] | None
    malformed: bool = False
    warning: str | None = None


def candidate_fingerprint(item: dict[str, Any]) -> str:
    payload = _semantic_payload(item)
    if payload is None:
        raise ValueError("record does not contain enough semantic fields for conservative dedupe")
    canonical = _canonical_json(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"qd:v1:{digest}"


def dedupe_candidates(items: list[dict[str, Any]]) -> QueueDedupeResult:
    lines = [
        _QueueLine(raw_line=json.dumps(item, sort_keys=True), line_number=index + 1, record=item)
        for index, item in enumerate(items)
    ]
    return _dedupe_lines(lines)


def audit_queue_file(path: Path, write: bool = False, backup_stamp: str | None = None) -> QueueDedupeResult:
    if not path.exists():
        return QueueDedupeResult(applied=False)

    original_text = path.read_text(encoding="utf-8")
    lines = _parse_jsonl(original_text)
    result = _dedupe_lines(lines)

    if write:
        backup_path = path.with_name(f"{path.stem}.{backup_stamp or _archive_stamp()}.before_dedupe{path.suffix}")
        backup_path.write_text(original_text, encoding="utf-8")
        retained_lines = [line.raw_line for line in _retained_lines(lines)]
        temp_path = path.with_name(f".{path.name}.{backup_stamp or _archive_stamp()}.tmp")
        temp_path.write_text(("\n".join(retained_lines) + "\n") if retained_lines else "", encoding="utf-8")
        os.replace(temp_path, path)
        result.applied = True
        result.backup_path = backup_path

    return result


def _parse_jsonl(text: str) -> list[_QueueLine]:
    parsed = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            parsed.append(
                _QueueLine(
                    raw_line=raw_line,
                    line_number=index,
                    record=None,
                    malformed=True,
                    warning=f"line {index}: malformed JSON retained ({exc.msg})",
                )
            )
            continue
        if not isinstance(item, dict):
            parsed.append(
                _QueueLine(
                    raw_line=raw_line,
                    line_number=index,
                    record=None,
                    malformed=True,
                    warning=f"line {index}: non-object JSON record retained",
                )
            )
            continue
        parsed.append(_QueueLine(raw_line=raw_line, line_number=index, record=item))
    return parsed


def _dedupe_lines(lines: list[_QueueLine]) -> QueueDedupeResult:
    result = QueueDedupeResult(input_count=len(lines))
    seen: dict[str, tuple[int, dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    retained_line_numbers: set[int] = set()

    for index, line in enumerate(lines):
        if line.malformed or line.record is None:
            result.malformed_count += 1
            result.warnings.append(line.warning or f"line {line.line_number}: malformed record retained")
            _increment(result.reasons, "malformed")
            retained_line_numbers.add(line.line_number)
            continue

        try:
            fingerprint = candidate_fingerprint(line.record)
        except ValueError as exc:
            result.warnings.append(f"line {line.line_number}: {exc}; record retained")
            _increment(result.reasons, "unknown_schema")
            retained_line_numbers.add(line.line_number)
            result.retained_records.append(line.record)
            continue

        result.fingerprints_generated += 1
        result.fingerprints.append(fingerprint)
        if fingerprint in seen:
            retained_index, _retained_record = seen[fingerprint]
            group = groups.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "retained_index": retained_index,
                    "duplicate_indices": [],
                    "reason": "semantic_duplicate",
                },
            )
            group["duplicate_indices"].append(index)
            result.duplicate_records.append(line.record)
            _increment(result.reasons, "duplicate")
            continue

        seen[fingerprint] = (index, line.record)
        retained_line_numbers.add(line.line_number)
        result.retained_records.append(line.record)

    result.duplicate_groups = list(groups.values())
    result.duplicate_count = len(result.duplicate_records)
    result.retained_count = len(retained_line_numbers)
    return result


def _retained_lines(lines: list[_QueueLine]) -> list[_QueueLine]:
    seen: set[str] = set()
    retained = []
    for line in lines:
        if line.malformed or line.record is None:
            retained.append(line)
            continue
        try:
            fingerprint = candidate_fingerprint(line.record)
        except ValueError:
            retained.append(line)
            continue
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        retained.append(line)
    return retained


def _semantic_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    payload = {}
    for canonical_key, aliases in SEMANTIC_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in item and item[alias] not in (None, "", [], {}):
                payload[canonical_key] = _normalize(item[alias])
                break

    if not any(key in payload for key in DEFINITION_FIELDS):
        return None
    return payload


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key).strip().lower(): _normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, list):
        items = [_normalize(item) for item in value]
        if all(_sortable_scalar(item) for item in items):
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return items
    if isinstance(value, tuple):
        return _normalize(list(value))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return int(value)
        return float(format(value, ".12g"))
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    return value


def _sortable_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
