from __future__ import annotations

import hashlib
from typing import Any


INPUT_VERSION = "candidate_experiment_draft_v1"
OUTPUT_VERSION = "hypothesis_queue_entry_candidate_v1"
TARGET_BLOCKER = "drawdown_fail"


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

    return {
        "version": OUTPUT_VERSION,
        "compatible": False,
        "hypothesis_id": _hypothesis_id(draft, note_ids),
        "source_draft": source_draft,
        "target_failure_mode": target_failure_mode,
        "source_note_ids": note_ids,
        "queue_row": None,
        "reason": (
            "The existing hypothesis queue schema and _spec_from_hypothesis() loader only map "
            "hard-coded families/builders and do not natively carry fixed-fractional sizing "
            "candidates, staged drawdown circuit-breaker thresholds, reentry rule, "
            "loser-addition prohibition, or full validation plan without dropping critical meaning."
        ),
        "required_schema_extension": {
            "base_strategy_selection": {
                "type": "object",
                "required": ["mode", "allowed_to_modify_signals", "allowed_to_modify_entries", "allowed_to_modify_exits"],
            },
            "risk_overlay.position_sizing": {
                "type": "object",
                "required": ["type", "risk_per_trade_pct_candidates"],
            },
            "risk_overlay.portfolio_drawdown_circuit_breaker": {
                "type": "object",
                "required": ["type", "thresholds", "reentry_rule"],
            },
            "risk_overlay.loser_addition_rule": {
                "type": "object",
                "required": ["add_to_losers_allowed"],
            },
            "validation_plan": {
                "type": "object",
                "required": ["primary_metrics", "secondary_metrics", "comparison", "required_gates"],
            },
            "source_note_ids": {
                "type": "array",
                "required": True,
                "description": "Stable provenance link to extracted source notes used by the draft.",
            },
        },
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


def _locked_safety() -> dict[str, bool]:
    return {
        "registry_append_allowed": False,
        "backtest_allowed_in_this_step": False,
        "promotion_allowed": False,
        "requires_manual_review": True,
    }
