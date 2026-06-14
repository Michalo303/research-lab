from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.orchestration.orchestrator import orchestrate_research_step


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCRIPT_PATH = ROOT / "scripts" / "run_research_orchestrator.py"
DECISION_SCRIPT_PATH = ROOT / "scripts" / "run_orchestration_decision.py"
RUNNER_PATH = ROOT / "research_lab" / "runner.py"
DEPLOYMENT_GATE_PATH = ROOT / "research_lab" / "deployment_gate.py"
FORBIDDEN_IMPORT_TARGETS = (
    "research_lab.runner",
    "research_lab.backtest",
    "research_lab.walk_forward",
    "research_lab.deployment_gate",
    "research_lab.strategies",
    "research_lab.reports",
    "deploy",
    "ops.systemd",
)


def test_existing_orchestrator_script_is_present():
    assert LEGACY_SCRIPT_PATH.exists()


def test_new_decision_script_is_present():
    assert DECISION_SCRIPT_PATH.exists()


def test_legacy_orchestrator_script_is_not_the_decision_only_wrapper():
    text = LEGACY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run_daily_research" in text
    assert "run_source_scan" in text
    assert "run_self_improvement" in text
    assert "orchestration.orchestrator import main" not in text


def test_protected_files_are_not_modified_in_worktree():
    runner_diff = subprocess.run(
        ["git", "diff", "--name-only", "--", str(RUNNER_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    gate_diff = subprocess.run(
        ["git", "diff", "--name-only", "--", str(DEPLOYMENT_GATE_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert runner_diff.stdout.strip() == ""
    assert gate_diff.stdout.strip() == ""


def test_core_package_avoids_forbidden_import_targets():
    package = ROOT / "research_lab" / "orchestration"
    assert package.exists()
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for forbidden in FORBIDDEN_IMPORT_TARGETS:
            assert forbidden not in imports


def test_explicit_generic_blockers_are_accepted():
    decision = orchestrate_research_step(
        {
            "recent_failures": [
                {"experiment_id": "exp-1", "blockers": ["walk_forward_fail", "drawdown_fail"]},
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "walk_forward_fail"
    assert decision.selected_worker == "hermes_book_extraction"
    assert decision.next_action == "create_book_extraction_request"


def test_unknown_generic_blockers_are_ignored_and_recorded():
    decision = orchestrate_research_step(
        {
            "recent_failures": [
                {"experiment_id": "exp-1", "blockers": ["unknown_blocker", "drawdown_fail"]},
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "drawdown_fail"
    assert decision.evidence["ignored_blockers"] == ["unknown_blocker"]


def test_deployment_gate_reasons_map_to_canonical_blockers():
    decision = orchestrate_research_step(
        {
            "deployment_gate_rows": [
                {
                    "strategy_id": "exp-1",
                    "gate_verdict": "fail",
                    "reasons": [
                        "insufficient_history",
                        "rolling_walk_forward_not_passed",
                        "drawdown_below_threshold",
                        "parameter_verdict_not_passed",
                    ],
                }
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.evidence["blocker_counts"] == {
        "data_quality_fail": 1,
        "drawdown_fail": 1,
        "overfit_risk": 1,
        "walk_forward_fail": 1,
    }
    assert decision.selected_blocker == "walk_forward_fail"


def test_daily_tier_reason_walk_forward_maps_conservatively():
    decision = orchestrate_research_step(
        {
            "daily_results": [
                {
                    "strategy_id": "exp-1",
                    "tier": "Rejected",
                    "tier_reason": "Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                    "data_source": "eodhd",
                    "history_length": 12.3,
                }
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "walk_forward_fail"


def test_daily_tier_reason_drawdown_maps_conservatively():
    decision = orchestrate_research_step(
        {
            "daily_results": [
                {
                    "strategy_id": "exp-1",
                    "tier": "Rejected",
                    "tier_reason": "Unseen max drawdown exceeds 15%.",
                    "data_source": "eodhd",
                    "history_length": 12.3,
                }
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "drawdown_fail"


def test_unmapped_reasons_are_recorded_without_crashing():
    decision = orchestrate_research_step(
        {
            "deployment_gate_rows": [
                {"strategy_id": "exp-1", "gate_verdict": "fail", "reasons": ["mystery_reason"]},
            ],
            "daily_results": [
                {
                    "strategy_id": "exp-2",
                    "tier": "Rejected",
                    "tier_reason": "something opaque happened",
                    "data_source": "eodhd",
                    "history_length": 12.3,
                }
            ],
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker is None
    assert decision.next_action == "no_action"
    assert decision.evidence["unmapped_reasons"] == ["mystery_reason", "something opaque happened"]


def test_most_frequent_whitelisted_blocker_is_selected():
    decision = orchestrate_research_step(
        {
            "recent_failures": [
                {"experiment_id": "exp-1", "blockers": ["drawdown_fail"]},
                {"experiment_id": "exp-2", "blockers": ["drawdown_fail"]},
                {"experiment_id": "exp-3", "blockers": ["walk_forward_fail"]},
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "drawdown_fail"


def test_tie_breaking_uses_priority_order():
    decision = orchestrate_research_step(
        {
            "recent_failures": [
                {"experiment_id": "exp-1", "blockers": ["drawdown_fail"]},
                {"experiment_id": "exp-2", "blockers": ["walk_forward_fail"]},
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker == "walk_forward_fail"


def test_no_valid_blocker_returns_no_action():
    decision = orchestrate_research_step(
        {"recent_failures": [{"experiment_id": "exp-1", "blockers": ["unknown_blocker"]}]},
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_blocker is None
    assert decision.selected_worker is None
    assert decision.next_action == "no_action"


def test_empty_input_returns_no_action():
    decision = orchestrate_research_step({}, created_at="2026-06-14T12:00:00Z")

    assert decision.selected_blocker is None
    assert decision.selected_worker is None
    assert decision.next_action == "no_action"


def test_repeated_blockers_count_deterministically():
    decision = orchestrate_research_step(
        {
            "recent_failures": [
                {"experiment_id": "exp-1", "blockers": ["walk_forward_fail", "walk_forward_fail"]},
                {"experiment_id": "exp-2", "blockers": ["walk_forward_fail"]},
            ]
        },
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.evidence["blocker_counts"]["walk_forward_fail"] == 3


def test_enabled_worker_is_selected_for_supported_blockers():
    for blocker in ("walk_forward_fail", "drawdown_fail", "overfit_risk"):
        decision = orchestrate_research_step(
            {"recent_failures": [{"experiment_id": blocker, "blockers": [blocker]}]},
            created_at="2026-06-14T12:00:00Z",
        )
        assert decision.selected_worker == "hermes_book_extraction"
        assert decision.worker_status == "enabled"


def test_data_quality_fail_does_not_route_to_hermes():
    decision = orchestrate_research_step(
        {"recent_failures": [{"experiment_id": "exp-1", "blockers": ["data_quality_fail"]}]},
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_worker is None
    assert decision.next_action == "no_action"


@pytest.mark.parametrize("blocker", ["flow_signal_missing", "institutional_positioning"])
def test_disabled_sec_f13_candidate_is_never_selected(blocker: str):
    decision = orchestrate_research_step(
        {"recent_failures": [{"experiment_id": blocker, "blockers": [blocker]}]},
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.selected_worker is None
    assert decision.next_action == "safe_deferred_action"
    assert decision.evidence["candidate_worker"] == "sec_f13_extraction"


def test_policy_flags_are_always_locked_down():
    decision = orchestrate_research_step(
        {"recent_failures": [{"experiment_id": "exp-1", "blockers": ["walk_forward_fail"]}]},
        created_at="2026-06-14T12:00:00Z",
    )

    assert decision.safety == {
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


def test_decision_json_is_serializable_and_stable():
    input_data = {
        "recent_failures": [
            {"experiment_id": "exp-1", "blockers": ["walk_forward_fail", "drawdown_fail"]},
        ]
    }

    first = orchestrate_research_step(input_data, created_at="2026-06-14T12:00:00Z").to_dict()
    second = orchestrate_research_step(input_data, created_at="2026-06-14T12:00:00Z").to_dict()

    assert first == second
    assert json.loads(json.dumps(first)) == first


def test_required_fields_are_present():
    decision = orchestrate_research_step({}, created_at="2026-06-14T12:00:00Z").to_dict()

    assert set(
        (
            "version",
            "created_at",
            "selected_blocker",
            "selected_worker",
            "worker_status",
            "next_action",
            "reason",
            "evidence",
            "safety",
            "no_action_reason",
        )
    ).issubset(decision)
    assert decision["selected_worker"] is None
    assert decision["selected_blocker"] is None
    assert set(("blocker_counts", "ignored_blockers", "unmapped_reasons")).issubset(decision["evidence"])


def test_cli_writes_valid_decision_json(tmp_path):
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "nested" / "decision.json"
    input_path.write_text(
        json.dumps({"recent_failures": [{"experiment_id": "exp-1", "blockers": ["walk_forward_fail"]}]}),
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(DECISION_SCRIPT_PATH), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selected_worker"] == "hermes_book_extraction"
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "input.json",
        "nested/decision.json",
    ]


def test_cli_handles_empty_input_safely(tmp_path):
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "decision.json"
    input_path.write_text("{}", encoding="utf-8")

    subprocess.run(
        [sys.executable, str(DECISION_SCRIPT_PATH), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["next_action"] == "no_action"
