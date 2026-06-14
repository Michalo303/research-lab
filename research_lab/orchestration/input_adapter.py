from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
from typing import Any

from research_lab.orchestration.schemas import canonical_blockers


ACCEPTED_TIERS = {"a", "b"}


def build_orchestration_input(
    root: Path,
    max_experiments: int = 50,
    max_gate_rows: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    root = Path(root)
    daily_results, recent_failures = _build_from_experiments(root, max_experiments=max_experiments)
    deployment_gate_rows = _build_from_deployment_gate(root, max_gate_rows=max_gate_rows)
    return {
        "recent_failures": recent_failures,
        "daily_results": daily_results,
        "deployment_gate_rows": deployment_gate_rows,
    }


def _build_from_experiments(root: Path, max_experiments: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = root / "registry" / "experiments.jsonl"
    if not path.exists():
        return [], []

    rows = _load_recent_jsonl_rows(path, max_rows=max_experiments)
    daily_results: list[dict[str, Any]] = []
    recent_failures: list[dict[str, Any]] = []
    allowed_blockers = canonical_blockers()

    for item in rows:
        tier = str(item.get("tier", "") or "")
        tier_reason = str(item.get("tier_reason", "") or "")
        if not _looks_like_failure_candidate(tier, tier_reason):
            continue

        daily_results.append(
            {
                "strategy_id": str(item.get("strategy_id", "") or ""),
                "tier": tier,
                "tier_reason": tier_reason,
                "data_source": _experiment_data_source(item),
                "history_length": _experiment_history_length(item),
            }
        )

        blockers = item.get("blockers")
        if isinstance(blockers, list):
            filtered = [str(blocker) for blocker in blockers if str(blocker) in allowed_blockers]
            if filtered:
                recent_failures.append(
                    {
                        "experiment_id": str(item.get("strategy_id", "") or ""),
                        "blockers": filtered,
                    }
                )

    return daily_results, recent_failures


def _build_from_deployment_gate(root: Path, max_gate_rows: int) -> list[dict[str, Any]]:
    weekly_dir = root / "reports" / "weekly"
    if not weekly_dir.exists():
        return []

    csv_files = sorted(weekly_dir.glob("*_deployment_gate.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not csv_files:
        return []

    rows: list[dict[str, Any]] = []
    with csv_files[0].open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            if len(rows) >= max_gate_rows:
                break
            if not isinstance(raw_row, dict):
                continue
            original_gate_verdict = str(raw_row.get("gate_verdict", "") or "").strip()
            gate_verdict = original_gate_verdict.lower()
            paper_eligible = _as_bool(raw_row.get("paper_eligible"))
            if gate_verdict != "fail" and paper_eligible is not False:
                continue
            row = {
                "strategy_id": str(raw_row.get("strategy_id", "") or ""),
                # The decision core currently consumes failing gate rows via gate_verdict == "fail".
                # Preserve the original CSV value separately when it differs for auditability.
                "gate_verdict": "fail",
                "reasons": _parse_reasons(raw_row.get("reasons")),
            }
            if original_gate_verdict and gate_verdict != "fail":
                row["original_gate_verdict"] = original_gate_verdict
            rows.append(row)
    return rows


def _load_recent_jsonl_rows(path: Path, max_rows: int) -> list[dict[str, Any]]:
    valid_rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            valid_rows.append(item)
    return valid_rows[-max(int(max_rows), 0) :] if max_rows is not None else valid_rows


def _looks_like_failure_candidate(tier: str, tier_reason: str) -> bool:
    normalized_tier = tier.strip().lower()
    if "rejected" in normalized_tier:
        return True
    if tier_reason.strip() and normalized_tier not in ACCEPTED_TIERS:
        return True
    return False


def _experiment_data_source(item: dict[str, Any]) -> str:
    value = item.get("data_source")
    if value:
        return str(value)
    manifest = item.get("data_manifest")
    if isinstance(manifest, dict) and manifest.get("source"):
        return str(manifest.get("source"))
    return ""


def _experiment_history_length(item: dict[str, Any]) -> float:
    value = _safe_float(item.get("history_length"))
    if value is not None:
        return value
    manifest = item.get("data_manifest")
    if isinstance(manifest, dict):
        value = _safe_float(manifest.get("years"))
        if value is not None:
            return value
    return 0.0


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _parse_reasons(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []

    parsed_json = _parse_json_reason_list(text)
    if parsed_json is not None:
        return parsed_json

    parsed_literal = _parse_literal_reason_list(text)
    if parsed_literal is not None:
        return parsed_literal

    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]

    return [text]


def _parse_json_reason_list(text: str) -> list[str] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _parse_literal_reason_list(text: str) -> list[str] | None:
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None
