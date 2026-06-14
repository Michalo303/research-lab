from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.orchestration.book_request import build_book_extraction_request


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_book_extraction_request.py"
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "book_request.py"
CREATED_AT = "2026-06-14T12:00:00Z"

LOCKED_DECISION_SAFETY = {
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

LOCKED_OUTPUT_SAFETY = {
    "worker_execution_allowed": False,
    "llm_calls_allowed_in_this_step": False,
    "pdf_parsing_allowed_in_this_step": False,
    "registry_write_allowed": False,
    "promotion_allowed": False,
    "requires_manual_review": True,
}

LOCKED_CONSTRAINTS = {
    "must_use_extracted_passages_only": True,
    "must_include_source_provenance": True,
    "must_not_invent_claims": True,
    "must_not_generate_strategy_code": True,
    "must_not_promote_notes": True,
    "must_not_modify_runtime": True,
    "must_not_run_backtests": True,
    "must_not_call_broker": True,
}

QUERY_CASES = (
    (
        "walk_forward_fail",
        "high",
        [
            "walk-forward robustness",
            "out-of-sample validation",
            "parameter stability",
            "regime robustness",
            "overfitting control",
        ],
    ),
    (
        "drawdown_fail",
        "high",
        [
            "drawdown control",
            "risk management",
            "volatility targeting",
            "defensive allocation",
            "circuit breaker",
        ],
    ),
    (
        "overfit_risk",
        "normal",
        [
            "overfitting control",
            "parameter sensitivity",
            "robustness testing",
            "model simplicity",
            "cross-validation",
        ],
    ),
    (
        "regime_instability",
        "normal",
        [
            "market regime",
            "regime transition",
            "volatility regime",
            "trend versus sideways",
            "adaptive allocation",
        ],
    ),
    (
        "cost_stress_fail",
        "normal",
        [
            "transaction costs",
            "turnover control",
            "slippage robustness",
            "trading frequency",
            "cost-aware strategy design",
        ],
    ),
)


def _decision(blocker: str = "drawdown_fail") -> dict:
    return {
        "version": "orchestration_decision_v1",
        "selected_blocker": blocker,
        "selected_worker": "hermes_book_extraction",
        "worker_status": "enabled",
        "next_action": "create_book_extraction_request",
        "reason": f"Selected blocker {blocker} routes to book extraction.",
        "evidence": {"selected_reason": blocker, "blocker_counts": {blocker: 2}},
        "safety": dict(LOCKED_DECISION_SAFETY),
        "no_action_reason": None,
    }


def _is_forbidden_import(import_name: str, forbidden_root: str) -> bool:
    return import_name == forbidden_root or import_name.startswith(forbidden_root + ".")


@pytest.mark.parametrize(("blocker", "priority", "query_hints"), QUERY_CASES)
def test_supported_blockers_create_deterministic_requests(blocker, priority, query_hints):
    request = build_book_extraction_request(_decision(blocker), created_at=CREATED_AT)

    assert request["version"] == "book_extraction_request_v1"
    assert request["created_at"] == CREATED_AT
    assert request["source_decision_version"] == "orchestration_decision_v1"
    assert request["source_selected_blocker"] == blocker
    assert request["requested_worker"] == "hermes_book_extraction"
    assert request["request_type"] == "extract_book_notes_for_blocker"
    assert request["blocker"] == blocker
    assert request["priority"] == priority
    assert request["query_hints"] == query_hints
    assert request["allowed_outputs"] == ["proposed_book_notes_jsonl", "book_extraction_audit_json"]
    assert request["no_request_reason"] is None


def test_request_preserves_source_reason_and_evidence():
    decision = _decision()

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["evidence"] == {
        "source_decision_reason": decision["reason"],
        "source_decision_evidence": decision["evidence"],
    }


def test_request_is_json_serializable_and_stable():
    decision = _decision()

    first = build_book_extraction_request(decision, created_at=CREATED_AT)
    second = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert first == second
    assert json.loads(json.dumps(first)) == first


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "orchestration_decision_v0"),
        ("selected_worker", "strategy_hypothesis"),
        ("worker_status", "disabled"),
        ("next_action", "no_action"),
        ("selected_blocker", "data_quality_fail"),
    ),
)
def test_non_applicable_decisions_return_no_request(field, value):
    decision = _decision()
    decision[field] = value

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert request["source_selected_blocker"] is None
    assert request["requested_worker"] is None
    assert request["blocker"] is None
    assert request["priority"] == "none"
    assert request["query_hints"] == []
    assert request["allowed_outputs"] == []
    assert request["evidence"]["validation_errors"]
    assert request["no_request_reason"] == "decision_validation_failed"


def test_no_action_reason_returns_no_request():
    decision = _decision()
    decision["no_action_reason"] = "worker_deferred"

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert "no_action_reason" in " ".join(request["evidence"]["validation_errors"])


@pytest.mark.parametrize("safety", (None, [], {}, {"llm_calls_allowed": False}))
def test_malformed_safety_returns_no_request(safety):
    decision = _decision()
    decision["safety"] = safety

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert request["evidence"]["validation_errors"]


@pytest.mark.parametrize(
    "flag",
    (
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
    ),
)
def test_any_permissive_safety_flag_returns_no_request(flag):
    decision = _decision()
    decision["safety"][flag] = True

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert flag in " ".join(request["evidence"]["validation_errors"])


@pytest.mark.parametrize("flag", ("requires_validation", "requires_manual_review_for_promotion"))
def test_missing_required_review_guards_returns_no_request(flag):
    decision = _decision()
    decision["safety"][flag] = False

    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert flag in " ".join(request["evidence"]["validation_errors"])


@pytest.mark.parametrize("decision", (None, [], "invalid"))
def test_malformed_decision_returns_no_request(decision):
    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["request_type"] == "no_request"
    assert request["source_decision_version"] is None
    assert request["evidence"]["validation_errors"] == ["decision must be a JSON object"]


@pytest.mark.parametrize("decision", (_decision(), {"version": "wrong"}))
def test_output_safety_and_constraints_are_always_locked(decision):
    request = build_book_extraction_request(decision, created_at=CREATED_AT)

    assert request["safety"] == LOCKED_OUTPUT_SAFETY
    assert request["constraints"] == LOCKED_CONSTRAINTS


def test_cli_writes_valid_request_and_only_requested_output(tmp_path):
    input_path = tmp_path / "decision.json"
    output_path = tmp_path / "requested" / "nested" / "book_request.json"
    input_path.write_text(json.dumps(_decision()), encoding="utf-8")

    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--decision", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["request_type"] == "extract_book_notes_for_blocker"
    assert payload["blocker"] == "drawdown_fail"
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "decision.json",
        "requested/nested/book_request.json",
    ]


def test_cli_writes_no_request_for_non_applicable_decision(tmp_path):
    input_path = tmp_path / "decision.json"
    output_path = tmp_path / "book_request.json"
    decision = _decision("data_quality_fail")
    input_path.write_text(json.dumps(decision), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--decision", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["request_type"] == "no_request"


def test_cli_rejects_invalid_json_without_writing_output(tmp_path):
    input_path = tmp_path / "invalid.json"
    output_path = tmp_path / "nested" / "book_request.json"
    input_path.write_text("{invalid", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--decision", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_is_forbidden_import_matches_root_and_submodules():
    assert _is_forbidden_import("urllib", "urllib")
    assert _is_forbidden_import("urllib.request", "urllib")
    assert _is_forbidden_import("research_lab.hermes.providers", "research_lab.hermes")
    assert _is_forbidden_import("ibapi.client", "ibapi")
    assert _is_forbidden_import("ib_insync.client", "ib_insync")
    assert not _is_forbidden_import("urllib3", "urllib")
    assert not _is_forbidden_import("research_lab.hermesx", "research_lab.hermes")


def test_builder_and_cli_avoid_forbidden_import_targets():
    forbidden_import_roots = (
        "hermes_knowledge",
        "research_lab.hermes",
        "research_lab.hermes_knowledge",
        "research_lab.runner",
        "research_lab.deployment_gate",
        "research_lab.backtest",
        "research_lab.walk_forward",
        "research_lab.strategies",
        "research_lab.reports",
        "research_lab.data",
        "research_lab.registry",
        "research_lab.paper",
        "research_lab.ibkr",
        "ibapi",
        "ib_insync",
        "urllib",
        "http",
        "requests",
        "aiohttp",
        "socket",
        "ftplib",
        "smtplib",
        "subprocess",
        "deployment",
        "systemd",
    )

    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for import_name in imports:
            for forbidden_root in forbidden_import_roots:
                assert not _is_forbidden_import(import_name, forbidden_root)
