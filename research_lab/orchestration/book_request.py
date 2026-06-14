from __future__ import annotations

from typing import Any

from research_lab.orchestration.schemas import utc_timestamp


REQUEST_VERSION = "book_extraction_request_v1"
DECISION_VERSION = "orchestration_decision_v1"
REQUESTED_WORKER = "hermes_book_extraction"

QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "walk_forward_fail": (
        "walk-forward robustness",
        "out-of-sample validation",
        "parameter stability",
        "regime robustness",
        "overfitting control",
    ),
    "drawdown_fail": (
        "drawdown control",
        "risk management",
        "volatility targeting",
        "defensive allocation",
        "circuit breaker",
    ),
    "overfit_risk": (
        "overfitting control",
        "parameter sensitivity",
        "robustness testing",
        "model simplicity",
        "cross-validation",
    ),
    "regime_instability": (
        "market regime",
        "regime transition",
        "volatility regime",
        "trend versus sideways",
        "adaptive allocation",
    ),
    "cost_stress_fail": (
        "transaction costs",
        "turnover control",
        "slippage robustness",
        "trading frequency",
        "cost-aware strategy design",
    ),
}

PRIORITY_BY_BLOCKER = {
    "walk_forward_fail": "high",
    "drawdown_fail": "high",
    "overfit_risk": "normal",
    "regime_instability": "normal",
    "cost_stress_fail": "normal",
}

DENIED_DECISION_SAFETY_FLAGS = (
    "allowed_to_modify_runtime",
    "promotion_allowed",
    "strategy_modification_allowed",
    "service_restart_allowed",
    "network_access_allowed",
    "llm_calls_allowed",
    "pdf_parsing_allowed",
    "backtest_allowed",
    "daily_research_run_allowed",
    "deployment_gate_run_allowed",
    "registry_write_allowed",
    "report_write_allowed",
)

REQUIRED_DECISION_SAFETY_FLAGS = (
    "requires_validation",
    "requires_manual_review_for_promotion",
)


def build_book_extraction_request(decision: Any, created_at: str | None = None) -> dict[str, Any]:
    created = utc_timestamp(created_at)
    validation_errors = _validate_decision(decision)
    if validation_errors:
        source_version = decision.get("version") if isinstance(decision, dict) else None
        return _no_request(created, source_version, validation_errors)

    blocker = decision["selected_blocker"]
    return {
        "version": REQUEST_VERSION,
        "created_at": created,
        "source_decision_version": decision["version"],
        "source_selected_blocker": blocker,
        "requested_worker": REQUESTED_WORKER,
        "request_type": "extract_book_notes_for_blocker",
        "blocker": blocker,
        "priority": PRIORITY_BY_BLOCKER[blocker],
        "query_hints": list(QUERY_HINTS[blocker]),
        "constraints": _locked_constraints(),
        "allowed_outputs": ["proposed_book_notes_jsonl", "book_extraction_audit_json"],
        "safety": _locked_output_safety(),
        "evidence": {
            "source_decision_reason": decision["reason"],
            "source_decision_evidence": dict(decision["evidence"]),
        },
        "no_request_reason": None,
    }


def _validate_decision(decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["decision must be a JSON object"]

    errors: list[str] = []
    if decision.get("version") != DECISION_VERSION:
        errors.append(f"version must be {DECISION_VERSION}")
    if decision.get("selected_worker") != REQUESTED_WORKER:
        errors.append(f"selected_worker must be {REQUESTED_WORKER}")
    if decision.get("worker_status") != "enabled":
        errors.append("worker_status must be enabled")
    if decision.get("next_action") != "create_book_extraction_request":
        errors.append("next_action must be create_book_extraction_request")
    if decision.get("selected_blocker") not in QUERY_HINTS:
        errors.append("selected_blocker is not supported for book extraction")
    if decision.get("no_action_reason") not in (None, ""):
        errors.append("no_action_reason must be null or empty")
    if not isinstance(decision.get("reason"), str):
        errors.append("reason must be a string")
    if not isinstance(decision.get("evidence"), dict):
        errors.append("evidence must be a JSON object")

    safety = decision.get("safety")
    if not isinstance(safety, dict):
        errors.append("safety must be a JSON object")
        return errors

    for flag in DENIED_DECISION_SAFETY_FLAGS:
        if safety.get(flag) is not False:
            errors.append(f"safety.{flag} must be false")
    for flag in REQUIRED_DECISION_SAFETY_FLAGS:
        if safety.get(flag) is not True:
            errors.append(f"safety.{flag} must be true")
    return errors


def _no_request(created_at: str, source_version: Any, validation_errors: list[str]) -> dict[str, Any]:
    return {
        "version": REQUEST_VERSION,
        "created_at": created_at,
        "source_decision_version": source_version,
        "source_selected_blocker": None,
        "requested_worker": None,
        "request_type": "no_request",
        "blocker": None,
        "priority": "none",
        "query_hints": [],
        "constraints": _locked_constraints(),
        "allowed_outputs": [],
        "safety": _locked_output_safety(),
        "evidence": {"validation_errors": list(validation_errors)},
        "no_request_reason": "decision_validation_failed",
    }


def _locked_constraints() -> dict[str, bool]:
    return {
        "must_use_extracted_passages_only": True,
        "must_include_source_provenance": True,
        "must_not_invent_claims": True,
        "must_not_generate_strategy_code": True,
        "must_not_promote_notes": True,
        "must_not_modify_runtime": True,
        "must_not_run_backtests": True,
        "must_not_call_broker": True,
    }


def _locked_output_safety() -> dict[str, bool]:
    return {
        "worker_execution_allowed": False,
        "llm_calls_allowed_in_this_step": False,
        "pdf_parsing_allowed_in_this_step": False,
        "registry_write_allowed": False,
        "promotion_allowed": False,
        "requires_manual_review": True,
    }
