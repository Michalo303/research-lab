from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_lab.orchestration.schemas import canonical_blockers


# This local mapping mirrors known stable strings emitted by the paper gate CSV rows.
DEPLOYMENT_GATE_REASON_MAP = {
    "insufficient_history": "data_quality_fail",
    "rolling_walk_forward_not_passed": "walk_forward_fail",
    "drawdown_below_threshold": "drawdown_fail",
    "parameter_verdict_not_passed": "overfit_risk",
}


@dataclass(frozen=True)
class NormalizedFailureSignals:
    blockers: tuple[str, ...]
    ignored_blockers: tuple[str, ...]
    unmapped_reasons: tuple[str, ...]
    recent_failure_count: int
    deployment_gate_row_count: int
    daily_result_count: int


def normalize_failure_signals(input_data: dict[str, Any] | None) -> NormalizedFailureSignals:
    payload = input_data if isinstance(input_data, dict) else {}
    allowed = canonical_blockers()
    blockers: list[str] = []
    ignored_blockers: list[str] = []
    unmapped_reasons: list[str] = []

    recent_failures = payload.get("recent_failures")
    if not isinstance(recent_failures, list):
        recent_failures = []
    for item in recent_failures:
        if not isinstance(item, dict):
            continue
        item_blockers = item.get("blockers")
        if not isinstance(item_blockers, list):
            continue
        for blocker in item_blockers:
            name = str(blocker or "").strip()
            if not name:
                continue
            if name in allowed:
                blockers.append(name)
            else:
                ignored_blockers.append(name)

    deployment_gate_rows = payload.get("deployment_gate_rows")
    if not isinstance(deployment_gate_rows, list):
        deployment_gate_rows = []
    for row in deployment_gate_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("gate_verdict", "")).strip().lower() != "fail":
            continue
        reasons = row.get("reasons")
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            text = str(reason or "").strip()
            if not text:
                continue
            mapped = DEPLOYMENT_GATE_REASON_MAP.get(text)
            if mapped:
                blockers.append(mapped)
            else:
                unmapped_reasons.append(text)

    daily_results = payload.get("daily_results")
    if not isinstance(daily_results, list):
        daily_results = []
    for row in daily_results:
        if not isinstance(row, dict):
            continue
        if str(row.get("tier", "")).strip().lower() != "rejected":
            continue
        tier_reason = " ".join(str(row.get("tier_reason", "")).lower().split())
        if not tier_reason:
            continue
        if "walk-forward" in tier_reason or "walk forward" in tier_reason or "rolling oos" in tier_reason:
            blockers.append("walk_forward_fail")
            continue
        if "drawdown" in tier_reason:
            blockers.append("drawdown_fail")
            continue
        if "parameter" in tier_reason or "stability" in tier_reason or "robustness" in tier_reason:
            blockers.append("overfit_risk")
            continue
        if "cost" in tier_reason or "stress" in tier_reason or "slippage" in tier_reason:
            blockers.append("cost_stress_fail")
            continue
        if "data" in tier_reason or "history" in tier_reason or "synthetic" in tier_reason or "insufficient" in tier_reason:
            blockers.append("data_quality_fail")
            continue
        unmapped_reasons.append(str(row.get("tier_reason", "")))

    return NormalizedFailureSignals(
        blockers=tuple(blockers),
        ignored_blockers=tuple(ignored_blockers),
        unmapped_reasons=tuple(unmapped_reasons),
        recent_failure_count=len(recent_failures),
        deployment_gate_row_count=len(deployment_gate_rows),
        daily_result_count=len(daily_results),
    )
