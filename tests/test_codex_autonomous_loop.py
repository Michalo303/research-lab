from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from research_lab.orchestration.codex_autonomous_contract import (
    CodexLoopConfig,
    CodexRoundResult,
    LoopMode,
    LoopStatus,
    ReviewerResponse,
    ReviewerModelTier,
    ReviewVerdict,
    ValidationResult,
)
from research_lab.orchestration.codex_autonomous_loop import (
    CodexAutonomousLoop,
    FakeCodexExecutor,
    FakeGitAction,
    FakeReviewer,
    FakeValidationRunner,
)
from scripts.run_codex_auto_loop import _build_report


ROOT = Path(__file__).resolve().parents[1]


def _round(
    *,
    changed_files: list[str] | None = None,
    diff_line_count: int = 10,
    proposed_commands: list[str] | None = None,
    summary: str = "updated skeleton",
) -> CodexRoundResult:
    return CodexRoundResult(
        changed_files=changed_files or ["research_lab/orchestration/codex_autonomous_loop.py"],
        diff_line_count=diff_line_count,
        proposed_commands=proposed_commands or [],
        summary=summary,
        patch_digest=summary,
        meaningful_progress=True,
    )


def _run_loop(
    config: CodexLoopConfig,
    *,
    codex_rounds: list[CodexRoundResult],
    reviewer_verdicts: list[ReviewVerdict],
    validation_results: list[ValidationResult] | None = None,
) -> tuple[LoopStatus, dict, FakeGitAction]:
    git_action = FakeGitAction()
    loop = CodexAutonomousLoop(
        config=config,
        codex_executor=FakeCodexExecutor(codex_rounds),
        reviewer=FakeReviewer(reviewer_verdicts),
        validation_runner=FakeValidationRunner(validation_results or [ValidationResult(success=True)]),
        git_action=git_action,
    )
    audit = loop.run(task_file="tasks/inbox/example.md")
    return loop.final_status, audit.to_dict(), git_action


def test_dry_run_pass():
    status, audit, _ = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.PASS
    assert audit["commit_attempted"] is False
    assert audit["push_attempted"] is False
    assert audit["pr_attempted"] is False


def test_safe_local_pass_without_real_commit_push_or_pr():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.SAFE_LOCAL),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.PASS
    assert git_action.calls == []
    assert audit["commit_created"] is False
    assert audit["push_completed"] is False
    assert audit["pr_created"] is False


def test_auto_pr_pass_plans_commit_push_and_pr_only_after_pass():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.AUTO_PR),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.PASS
    assert git_action.calls == ["plan"]
    assert audit["commit_attempted"] is True
    assert audit["commit_created"] is False
    assert audit["push_attempted"] is True
    assert audit["push_completed"] is False
    assert audit["pr_attempted"] is True
    assert audit["pr_created"] is False
    assert audit["merge_attempted"] is False


def test_super_auto_pass_plans_commit_push_and_pr_only():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.PASS
    assert git_action.calls == ["plan"]
    assert audit["commit_attempted"] is True
    assert audit["commit_created"] is False
    assert audit["push_attempted"] is True
    assert audit["push_completed"] is False
    assert audit["pr_attempted"] is True
    assert audit["pr_created"] is False
    assert audit["merge_attempted"] is False


def test_super_auto_never_merges():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.PASS
    assert git_action.calls == ["plan"]
    assert audit["merge_attempted"] is False
    assert audit["merge_blocked"] is True


def test_revise_does_not_trigger_git_action():
    config = CodexLoopConfig.for_mode(LoopMode.AUTO_PR)
    config.max_rounds = 1

    status, _, git_action = _run_loop(
        config,
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.REVISE)],
    )

    assert status is LoopStatus.BLOCKED
    assert git_action.calls == []


def test_blocked_does_not_trigger_git_action():
    status, _, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.AUTO_PR),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.BLOCKED)],
    )

    assert status is LoopStatus.BLOCKED
    assert git_action.calls == []


def test_unsafe_does_not_trigger_git_action():
    status, _, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.AUTO_PR),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.UNSAFE)],
    )

    assert status is LoopStatus.UNSAFE
    assert git_action.calls == []


def test_validation_failure_does_not_trigger_git_action():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.AUTO_PR),
        codex_rounds=[_round()],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
        validation_results=[ValidationResult(success=False, failures=["pytest failed"])],
    )

    assert status is LoopStatus.BLOCKED
    assert audit["tests_passed"] is False
    assert git_action.calls == []


def test_policy_failure_does_not_trigger_git_action():
    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.AUTO_PR),
        codex_rounds=[_round(changed_files=["reports/daily/2026-06-05.md"])],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.UNSAFE
    assert audit["protected_paths_touched"] == ["reports/daily/2026-06-05.md"]
    assert git_action.calls == []


def test_revise_then_pass():
    status, audit, _ = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
        codex_rounds=[_round(summary="first"), _round(summary="second")],
        reviewer_verdicts=[
            ReviewVerdict(status=LoopStatus.REVISE, issues=["tighten audit"]),
            ReviewVerdict(status=LoopStatus.PASS),
        ],
    )

    assert status is LoopStatus.PASS
    assert audit["rounds_used"] == 2
    assert audit["reviewer_verdicts"] == ["REVISE", "PASS"]


def test_max_rounds_produces_blocked():
    config = CodexLoopConfig.for_mode(LoopMode.DRY_RUN)
    config.max_rounds = 2

    status, audit, _ = _run_loop(
        config,
        codex_rounds=[_round(summary="first"), _round(summary="second")],
        reviewer_verdicts=[
            ReviewVerdict(status=LoopStatus.REVISE),
            ReviewVerdict(status=LoopStatus.REVISE),
        ],
    )

    assert status is LoopStatus.BLOCKED
    assert audit["rounds_used"] == 2


def test_no_progress_limit_produces_blocked():
    config = CodexLoopConfig.for_mode(LoopMode.DRY_RUN)
    config.no_progress_round_limit = 2

    no_progress_round = CodexRoundResult(
        changed_files=["research_lab/orchestration/codex_autonomous_loop.py"],
        diff_line_count=0,
        proposed_commands=[],
        summary="no progress",
        patch_digest="same",
        meaningful_progress=False,
    )

    status, audit, _ = _run_loop(
        config,
        codex_rounds=[no_progress_round, no_progress_round],
        reviewer_verdicts=[
            ReviewVerdict(status=LoopStatus.REVISE),
            ReviewVerdict(status=LoopStatus.REVISE),
        ],
    )

    assert status is LoopStatus.BLOCKED
    assert audit["no_progress_rounds"] == 2


def test_protected_path_change_produces_unsafe():
    status, audit, _ = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
        codex_rounds=[_round(changed_files=["reports/daily/2026-06-05.md"])],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.UNSAFE
    assert audit["protected_paths_touched"] == ["reports/daily/2026-06-05.md"]


def test_forbidden_command_produces_unsafe():
    status, audit, _ = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
        codex_rounds=[_round(proposed_commands=["rm -rf codex_runs/tmp"])],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.UNSAFE
    assert "rm -rf" in audit["forbidden_commands_detected"]


def test_deploy_service_restart_hetzner_sync_registry_append_push_main_and_merge_attempts_are_unsafe():
    cases = [
        ["deploy prod"],
        ["service restart codex-auto"],
        ["scripts/run_safe_sync_with_preflight.sh"],
        ["registry append candidate"],
        ["git push origin main"],
        ["git merge main"],
    ]

    for commands in cases:
        status, audit, _ = _run_loop(
            CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
            codex_rounds=[_round(proposed_commands=commands)],
            reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
        )
        assert status is LoopStatus.UNSAFE
        assert audit["final_status"] == "UNSAFE"


def test_runtime_outputs_are_gitignored_but_gitkeep_files_remain_trackable():
    ignored = ROOT / "codex_runs" / "example-run" / "audit.json"
    tracked = ROOT / "codex_runs" / ".gitkeep"
    task_gitkeeps = [
        ROOT / "tasks" / "inbox" / ".gitkeep",
        ROOT / "tasks" / "done" / ".gitkeep",
        ROOT / "tasks" / "blocked" / ".gitkeep",
    ]

    ignored_result = subprocess.run(
        ["git", "check-ignore", str(ignored)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked_result = subprocess.run(
        ["git", "check-ignore", str(tracked)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert ignored_result.returncode == 0
    assert tracked_result.returncode == 1

    for task_gitkeep in task_gitkeeps:
        result = subprocess.run(
            ["git", "check-ignore", str(task_gitkeep)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1


def test_cli_report_marks_outputs_as_runtime_artifacts_only():
    report = _build_report(
        {
            "final_status": "PASS",
            "mode": "super_auto",
            "task_file": "tasks/inbox/example.md",
            "branch": "codex/super_auto-test",
            "rounds_used": 1,
            "max_rounds": 20,
            "tests_requested": ["pytest -q"],
            "final_human_action_required": True,
        }
    )

    assert "dry-run only" in report
    assert "Runtime artifact only" in report


def test_executor_failure_produces_blocked_without_git_actions():
    failed_round = CodexRoundResult(
        changed_files=[],
        diff_line_count=0,
        proposed_commands=[],
        summary="executor failed",
        patch_digest="",
        meaningful_progress=False,
        executor_failed=True,
    )

    status, audit, git_action = _run_loop(
        CodexLoopConfig.for_mode(LoopMode.DRY_RUN),
        codex_rounds=[failed_round],
        reviewer_verdicts=[ReviewVerdict(status=LoopStatus.PASS)],
    )

    assert status is LoopStatus.BLOCKED
    assert git_action.calls == []
    assert audit["rounds_used"] == 1


def test_loop_audit_includes_gpt_reviewer_details_when_opted_in():
    reviewer = FakeReviewer([ReviewVerdict(status=LoopStatus.PASS)])
    reviewer.last_response = ReviewerResponse(
        verdict=LoopStatus.PASS,
        confidence=0.95,
        reason="Looks good.",
        required_changes=[],
        safety_notes=[],
        escalation_recommended=False,
        selected_model="gpt-reviewer-high",
        selected_tier=ReviewerModelTier.HIGH,
        budget_blocked=False,
        raw_response_redacted='{"verdict":"PASS"}',
    )
    reviewer.call_count = 0
    reviewer.last_redaction_notes = ["Truncated long codex summary before provider call."]

    loop = CodexAutonomousLoop(
        config=CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO),
        codex_executor=FakeCodexExecutor([_round()]),
        reviewer=reviewer,
        validation_runner=FakeValidationRunner([ValidationResult(success=True)]),
        git_action=FakeGitAction(),
    )

    audit = loop.run(task_file="tasks/inbox/example.md").to_dict()

    assert audit["reviewer_selected_model"] == "gpt-reviewer-high"
    assert audit["reviewer_selected_tier"] == "high"
    assert audit["reviewer_call_count"] == 1
    assert audit["reviewer_budget_blocked"] is False
    assert audit["reviewer_redaction_notes"] == ["Truncated long codex summary before provider call."]
