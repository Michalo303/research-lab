from __future__ import annotations

from research_lab.orchestration.schemas import WorkerDefinition


WORKERS: tuple[WorkerDefinition, ...] = (
    WorkerDefinition(
        worker_id="hermes_book_extraction",
        display_name="Hermes Book Extraction Worker",
        handles_blockers=(
            "walk_forward_fail",
            "drawdown_fail",
            "overfit_risk",
            "regime_instability",
            "cost_stress_fail",
        ),
        enabled=True,
        can_modify_runtime=False,
        output_type="book_extraction_request",
        notes="Creates a request for book-derived extracted-note candidates; does not promote notes.",
    ),
    WorkerDefinition(
        worker_id="strategy_hypothesis",
        display_name="Strategy Hypothesis Worker",
        handles_blockers=("walk_forward_fail", "overfit_risk", "drawdown_fail"),
        enabled=False,
        can_modify_runtime=False,
        output_type="strategy_hypothesis_request",
        notes="Future worker. Disabled in v1.",
    ),
    WorkerDefinition(
        worker_id="regime_modeling",
        display_name="Regime Modeling Worker",
        handles_blockers=("regime_instability", "drawdown_fail"),
        enabled=False,
        can_modify_runtime=False,
        output_type="regime_modeling_request",
        notes="Future worker. Disabled in v1.",
    ),
    WorkerDefinition(
        worker_id="sec_f13_extraction",
        display_name="SEC/F13 Extraction Worker",
        handles_blockers=("flow_signal_missing", "institutional_positioning"),
        enabled=False,
        can_modify_runtime=False,
        output_type="sec_f13_extraction_request",
        notes="Future worker. Disabled in v1. Must later enforce point-in-time filing availability and anti-lookahead rules.",
    ),
    WorkerDefinition(
        worker_id="report_agent",
        display_name="Report Worker",
        handles_blockers=(),
        enabled=False,
        can_modify_runtime=False,
        output_type="report_summary",
        notes="Future worker. Disabled in v1.",
    ),
)


def list_workers() -> tuple[WorkerDefinition, ...]:
    return WORKERS


def candidate_worker_for_blocker(blocker: str | None) -> WorkerDefinition | None:
    if blocker is None:
        return None
    for worker in WORKERS:
        if blocker in worker.handles_blockers:
            return worker
    return None


def enabled_worker_for_blocker(blocker: str | None) -> WorkerDefinition | None:
    worker = candidate_worker_for_blocker(blocker)
    if worker is None or not worker.enabled:
        return None
    return worker
