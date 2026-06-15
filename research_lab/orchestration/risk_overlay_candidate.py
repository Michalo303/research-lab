from __future__ import annotations

import hashlib
from typing import Any


DRAFT_VERSION = "candidate_experiment_draft_v1"
TARGET_BLOCKER = "drawdown_fail"
SOURCE_NOTE_VERSION = "extracted_book_note_v1"
HYPOTHESIS = (
    "Fixed-fractional risk sizing plus a portfolio drawdown circuit breaker reduces "
    "drawdown severity and recovery time while preserving existing signal logic."
)


def build_risk_overlay_candidate_draft(notes: Any) -> dict[str, Any]:
    source_notes = _source_notes(notes)
    if not source_notes:
        raise ValueError("at least one drawdown_fail extracted_book_note_v1 note is required")

    return {
        "version": DRAFT_VERSION,
        "source": {
            "blocker": TARGET_BLOCKER,
            "source_notes": source_notes,
        },
        "hypothesis": HYPOTHESIS,
        "target_failure_mode": TARGET_BLOCKER,
        "base_strategy_selection": {
            "mode": "near_miss_drawdown",
            "allowed_to_modify_signals": False,
            "allowed_to_modify_entries": False,
            "allowed_to_modify_exits": False,
        },
        "risk_overlay": {
            "position_sizing": {
                "type": "fixed_fractional",
                "risk_per_trade_pct_candidates": [0.25, 0.5, 0.75, 1.0],
            },
            "portfolio_drawdown_circuit_breaker": {
                "type": "staged_derisking",
                "thresholds": [
                    {"drawdown_pct": 5, "gross_exposure_multiplier": 0.75},
                    {"drawdown_pct": 8, "gross_exposure_multiplier": 0.5},
                    {"drawdown_pct": 10, "gross_exposure_multiplier": 0.0},
                ],
                "reentry_rule": {
                    "type": "equity_recovery",
                    "recovery_from_peak_pct": 2,
                    "cooldown_days": 10,
                },
            },
            "loser_addition_rule": {
                "add_to_losers_allowed": False,
            },
        },
        "validation_plan": {
            "primary_metrics": [
                "max_drawdown",
                "drawdown_duration",
                "recovery_time",
                "survival_rate",
            ],
            "secondary_metrics": [
                "CAGR",
                "Sharpe",
                "turnover",
                "cost_stress",
            ],
            "comparison": "same signals with and without risk overlay",
            "required_gates": [
                "walk_forward",
                "drawdown",
                "cost_stress",
                "stability",
            ],
        },
        "safety": {
            "promotion_allowed": False,
            "registry_write_allowed": False,
            "backtest_allowed_in_this_step": False,
            "strategy_code_modification_allowed": False,
            "requires_manual_review": True,
        },
    }


def _source_notes(notes: Any) -> list[dict[str, Any]]:
    if not isinstance(notes, list):
        raise ValueError("notes must be a list of extracted note objects")

    retained: list[dict[str, Any]] = []
    for item in notes:
        if not isinstance(item, dict):
            raise ValueError("each note must be a JSON object")
        if item.get("version") != SOURCE_NOTE_VERSION:
            continue
        if str(item.get("blocker") or "").strip() != TARGET_BLOCKER:
            continue
        retained.append(
            {
                "note_id": _source_note_id(item),
                "book_id": _text(item.get("book_id")),
                "book_title": _text(item.get("book_title")),
                "page_start": _int_or_none(item.get("page_start")),
                "page_end": _int_or_none(item.get("page_end")),
                "confidence": _text(item.get("confidence")),
                "promotion_status": _text(item.get("promotion_status")),
                "extracted_claim": _text(item.get("extracted_claim")),
                "why_relevant_to_blocker": _text(item.get("why_relevant_to_blocker")),
                "risk_controls": _string_list(item.get("risk_controls")),
            }
        )
    return retained


def _source_note_id(item: dict[str, Any]) -> str:
    note_id = _text(item.get("note_id"))
    if note_id:
        return note_id

    fallback_id = _text(item.get("id"))
    if fallback_id:
        return fallback_id

    payload = "|".join(
        [
            TARGET_BLOCKER,
            _text(item.get("book_id")),
            str(_int_or_none(item.get("page_start"))),
            str(_int_or_none(item.get("page_end"))),
            _text(item.get("extracted_claim")),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"note-{digest[:16]}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
