from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_lab.orchestration.blocker_selection import select_blocker
from research_lab.orchestration.failure_normalization import normalize_failure_signals
from research_lab.orchestration.policy import enforce_v1_safety
from research_lab.orchestration.schemas import OrchestrationDecision, utc_timestamp
from research_lab.orchestration.worker_registry import candidate_worker_for_blocker, enabled_worker_for_blocker


DECISION_VERSION = "orchestration_decision_v1"


def orchestrate_research_step(input_data: dict[str, Any], created_at: str | None = None) -> OrchestrationDecision:
    normalized = normalize_failure_signals(input_data)
    selection = select_blocker(normalized.blockers)
    candidate_worker = candidate_worker_for_blocker(selection.selected_blocker)
    enabled_worker = enabled_worker_for_blocker(selection.selected_blocker)

    evidence = {
        "blocker_counts": dict(selection.blocker_counts),
        "ignored_blockers": list(normalized.ignored_blockers),
        "unmapped_reasons": list(normalized.unmapped_reasons),
        "recent_failure_count": normalized.recent_failure_count,
        "deployment_gate_row_count": normalized.deployment_gate_row_count,
        "daily_result_count": normalized.daily_result_count,
        "selected_reason": selection.selected_reason,
    }
    if candidate_worker is not None:
        evidence["candidate_worker"] = candidate_worker.worker_id

    safety = enforce_v1_safety(worker_can_modify_runtime=bool(candidate_worker and candidate_worker.can_modify_runtime))
    created = utc_timestamp(created_at)

    if selection.selected_blocker is None:
        return OrchestrationDecision(
            version=DECISION_VERSION,
            created_at=created,
            selected_blocker=None,
            selected_worker=None,
            worker_status="not_applicable",
            next_action="no_action",
            reason="No canonical blocker was selected from the provided structured inputs.",
            evidence=evidence,
            safety=safety,
            no_action_reason="no_valid_blockers",
        )

    if selection.selected_blocker == "data_quality_fail":
        return OrchestrationDecision(
            version=DECISION_VERSION,
            created_at=created,
            selected_blocker=selection.selected_blocker,
            selected_worker=None,
            worker_status="not_applicable",
            next_action="no_action",
            reason="Data-quality problems do not route to book extraction in v1.",
            evidence=evidence,
            safety=safety,
            no_action_reason="data_quality_requires_non_hermes_follow_up",
        )

    if enabled_worker is not None:
        return OrchestrationDecision(
            version=DECISION_VERSION,
            created_at=created,
            selected_blocker=selection.selected_blocker,
            selected_worker=enabled_worker.worker_id,
            worker_status="enabled",
            next_action="create_book_extraction_request",
            reason=f"Selected blocker {selection.selected_blocker} routes to the enabled {enabled_worker.worker_id} worker in v1.",
            evidence=evidence,
            safety=safety,
            no_action_reason=None,
        )

    if candidate_worker is not None:
        return OrchestrationDecision(
            version=DECISION_VERSION,
            created_at=created,
            selected_blocker=selection.selected_blocker,
            selected_worker=None,
            worker_status="disabled",
            next_action="safe_deferred_action",
            reason=f"Selected blocker {selection.selected_blocker} maps to {candidate_worker.worker_id}, but that worker is disabled in v1.",
            evidence=evidence,
            safety=safety,
            no_action_reason="candidate_worker_disabled_in_v1",
        )

    return OrchestrationDecision(
        version=DECISION_VERSION,
        created_at=created,
        selected_blocker=selection.selected_blocker,
        selected_worker=None,
        worker_status="not_found",
        next_action="no_action",
        reason=f"Selected blocker {selection.selected_blocker} has no registered worker.",
        evidence=evidence,
        safety=safety,
        no_action_reason="no_registered_worker",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic research orchestrator core.")
    parser.add_argument("--input", required=True, help="Path to structured input JSON.")
    parser.add_argument("--output", required=True, help="Path to output decision JSON.")
    args = parser.parse_args(argv)

    try:
        input_payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read input JSON: {exc}\n")

    decision = orchestrate_research_step(input_payload if isinstance(input_payload, dict) else {})
    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(decision.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
