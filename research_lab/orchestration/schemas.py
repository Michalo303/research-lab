from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CanonicalBlocker(str, Enum):
    WALK_FORWARD_FAIL = "walk_forward_fail"
    DRAWDOWN_FAIL = "drawdown_fail"
    OVERFIT_RISK = "overfit_risk"
    COST_STRESS_FAIL = "cost_stress_fail"
    REGIME_INSTABILITY = "regime_instability"
    DATA_QUALITY_FAIL = "data_quality_fail"
    FLOW_SIGNAL_MISSING = "flow_signal_missing"
    INSTITUTIONAL_POSITIONING = "institutional_positioning"


BLOCKER_PRIORITY: tuple[str, ...] = (
    CanonicalBlocker.WALK_FORWARD_FAIL.value,
    CanonicalBlocker.DRAWDOWN_FAIL.value,
    CanonicalBlocker.OVERFIT_RISK.value,
    CanonicalBlocker.REGIME_INSTABILITY.value,
    CanonicalBlocker.COST_STRESS_FAIL.value,
    CanonicalBlocker.DATA_QUALITY_FAIL.value,
    CanonicalBlocker.FLOW_SIGNAL_MISSING.value,
    CanonicalBlocker.INSTITUTIONAL_POSITIONING.value,
)


WORKER_STATUS_VALUES: tuple[str, ...] = ("enabled", "disabled", "not_applicable", "not_found")
NEXT_ACTION_VALUES: tuple[str, ...] = ("create_book_extraction_request", "safe_deferred_action", "no_action")


@dataclass(frozen=True)
class WorkerDefinition:
    worker_id: str
    display_name: str
    handles_blockers: tuple[str, ...]
    enabled: bool
    can_modify_runtime: bool
    output_type: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "display_name": self.display_name,
            "handles_blockers": list(self.handles_blockers),
            "enabled": self.enabled,
            "can_modify_runtime": self.can_modify_runtime,
            "output_type": self.output_type,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class OrchestrationDecision:
    version: str
    created_at: str
    selected_blocker: str | None
    selected_worker: str | None
    worker_status: str
    next_action: str
    reason: str
    evidence: dict[str, Any]
    safety: dict[str, Any]
    no_action_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "selected_blocker": self.selected_blocker,
            "selected_worker": self.selected_worker,
            "worker_status": self.worker_status,
            "next_action": self.next_action,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "safety": dict(self.safety),
            "no_action_reason": self.no_action_reason,
        }


def canonical_blockers() -> set[str]:
    return set(BLOCKER_PRIORITY)


def utc_timestamp(created_at: str | None = None) -> str:
    if created_at:
        return created_at
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
