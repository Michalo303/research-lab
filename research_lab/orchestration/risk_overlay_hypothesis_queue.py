from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any


INPUT_VERSION = "candidate_experiment_draft_v1"
OUTPUT_VERSION = "hypothesis_queue_entry_candidate_v1"
TARGET_BLOCKER = "drawdown_fail"
QUEUE_ROW_VERSION = "risk_overlay_hypothesis_queue_row_v1"
QUEUE_FAMILY = "RISK_OVERLAY"


def build_risk_overlay_hypothesis_queue_entry(
    draft: Any,
    *,
    source_draft: str,
) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise ValueError("draft must be a JSON object")
    if draft.get("version") != INPUT_VERSION:
        raise ValueError(f"draft version must be {INPUT_VERSION}")

    target_failure_mode = str(draft.get("target_failure_mode") or "").strip()
    if target_failure_mode != TARGET_BLOCKER:
        raise ValueError(f"target_failure_mode must be {TARGET_BLOCKER}")

    source = draft.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must be a JSON object")
    if str(source.get("blocker") or "").strip() != TARGET_BLOCKER:
        raise ValueError(f"source.blocker must be {TARGET_BLOCKER}")

    note_ids = _source_note_ids(source.get("source_notes"))
    if not note_ids:
        raise ValueError("source.source_notes must contain at least one note_id")

    queue_row = _queue_row(draft, source_draft=source_draft, source_note_ids=note_ids)

    return {
        "version": OUTPUT_VERSION,
        "compatible": False,
        "hypothesis_id": _hypothesis_id(draft, note_ids),
        "source_draft": source_draft,
        "target_failure_mode": target_failure_mode,
        "source_note_ids": note_ids,
        "queue_row": queue_row,
        "reason": _runtime_unsupported_reason(),
        "required_runtime_hook": _required_runtime_hook(),
        "safety": _locked_safety(),
    }


def _source_note_ids(source_notes: Any) -> list[str]:
    if not isinstance(source_notes, list):
        raise ValueError("source.source_notes must be a list")
    note_ids: list[str] = []
    for item in source_notes:
        if not isinstance(item, dict):
            raise ValueError("each source note must be a JSON object")
        note_id = str(item.get("note_id") or "").strip()
        if note_id:
            note_ids.append(note_id)
    return note_ids


def _hypothesis_id(draft: dict[str, Any], source_note_ids: list[str]) -> str:
    payload = "|".join(
        [
            TARGET_BLOCKER,
            str(draft.get("target_failure_mode") or "").strip(),
            str(draft.get("base_strategy_selection", {}).get("mode") if isinstance(draft.get("base_strategy_selection"), dict) else ""),
            str(draft.get("hypothesis") or "").strip(),
            ",".join(source_note_ids),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"RISK_OVERLAY_{digest[:16].upper()}"


def _queue_row(
    draft: dict[str, Any],
    *,
    source_draft: str,
    source_note_ids: list[str],
) -> dict[str, Any]:
    row = {
        "queue_row_version": QUEUE_ROW_VERSION,
        "hypothesis_id": _hypothesis_id(draft, source_note_ids),
        "family": QUEUE_FAMILY,
        "title": "Risk overlay hypothesis candidate",
        "rationale": str(draft.get("hypothesis") or "").strip(),
        "source_title": "risk_overlay_candidate_draft",
        "source_draft": source_draft,
        "target_failure_mode": TARGET_BLOCKER,
        "source_note_ids": list(source_note_ids),
        "base_strategy_selection": deepcopy(draft.get("base_strategy_selection") or {}),
        "risk_overlay": deepcopy(draft.get("risk_overlay") or {}),
        "validation_plan": deepcopy(draft.get("validation_plan") or {}),
        "safety": _locked_safety(),
    }
    base_strategy = draft.get("base_strategy")
    if isinstance(base_strategy, dict):
        row["base_strategy"] = deepcopy(base_strategy)
    return row


def _runtime_unsupported_reason() -> str:
    return (
        "RISK_OVERLAY queue rows can now preserve the full hypothesis payload, but the current runtime "
        "does not have a safe overlay execution hook for fixed-fractional position sizing, portfolio "
        "drawdown circuit breaker thresholds with reentry rule enforcement, loser-addition rule "
        "enforcement, or base strategy binding without dropping meaning."
    )


def _required_runtime_hook() -> dict[str, Any]:
    return {
        "type": "risk_overlay_execution_adapter_v1",
        "requires_base_strategy_binding": True,
        "preserve_base_signals_entries_exits": True,
        "required_fields": [
            "source_note_ids",
            "base_strategy_selection",
            "risk_overlay.position_sizing",
            "risk_overlay.portfolio_drawdown_circuit_breaker",
            "risk_overlay.loser_addition_rule",
            "validation_plan",
        ],
        "missing_capabilities": [
            "fixed_fractional_position_sizing",
            "portfolio_drawdown_circuit_breaker",
            "reentry_rule_enforcement",
            "loser_addition_rule_enforcement",
            "validation_plan_passthrough",
        ],
    }


def _locked_safety() -> dict[str, bool]:
    return {
        "registry_append_allowed": False,
        "backtest_allowed_in_this_step": False,
        "promotion_allowed": False,
        "requires_manual_review": True,
    }
