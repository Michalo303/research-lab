from __future__ import annotations

from typing import Any


def build_v1_safety_policy() -> dict[str, Any]:
    return {
        "allowed_to_modify_runtime": False,
        "promotion_allowed": False,
        "strategy_modification_allowed": False,
        "service_restart_allowed": False,
        "network_access_allowed": False,
        "llm_calls_allowed": False,
        "pdf_parsing_allowed": False,
        "backtest_allowed": False,
        "daily_research_run_allowed": False,
        "deployment_gate_run_allowed": False,
        "registry_write_allowed": False,
        "report_write_allowed": False,
        "requires_validation": True,
        "requires_manual_review_for_promotion": True,
    }


def enforce_v1_safety(worker_can_modify_runtime: bool = False) -> dict[str, Any]:
    policy = build_v1_safety_policy()
    if worker_can_modify_runtime:
        policy["allowed_to_modify_runtime"] = False
    return policy
