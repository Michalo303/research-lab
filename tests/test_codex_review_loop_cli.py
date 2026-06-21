from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import subprocess
import sys
from pathlib import Path

import scripts.run_codex_review_loop as cli_script
from research_lab.orchestration.codex_autonomous_contract import CodexRoundResult
from research_lab.orchestration.codex_review_loop import FakeReviewLoopExecutor, TrackedTreeProbeResult


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_codex_review_loop.py"


def _run_cli(
    tmp_path: Path,
    *extra_args: str,
    build_loop=None,
    build_executor=None,
) -> subprocess.CompletedProcess[str]:
    output_dir = tmp_path / "review-loop-output"
    argv = [
        "--task",
        "Implement a fake review loop CLI.",
        "--output-dir",
        str(output_dir),
        *extra_args,
    ]
    original_build_loop = cli_script._build_loop
    original_build_executor = cli_script._build_executor

    def _clean_build_loop(args, dry_run_external_calls):
        loop = original_build_loop(args, dry_run_external_calls)
        loop.tracked_tree_checker = lambda: TrackedTreeProbeResult(dirty=False, status="")
        return loop

    cli_script._build_loop = build_loop or _clean_build_loop
    if build_executor is not None:
        cli_script._build_executor = build_executor

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                returncode = cli_script.main(argv)
            except SystemExit as exc:
                returncode = exc.code if isinstance(exc.code, int) else 1
    finally:
        cli_script._build_loop = original_build_loop
        cli_script._build_executor = original_build_executor

    return subprocess.CompletedProcess(
        args=[sys.executable, str(SCRIPT), *argv],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _load_artifacts(tmp_path: Path) -> tuple[dict, str, Path]:
    output_dir = tmp_path / "review-loop-output"
    audit_path = output_dir / "audit.json"
    report_path = output_dir / "final_report.md"
    return json.loads(audit_path.read_text(encoding="utf-8")), report_path.read_text(encoding="utf-8"), output_dir


class StubAudit:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


def test_cli_pass_writes_audit_and_report(tmp_path: Path):
    result = _run_cli(tmp_path, "--fake-reviewer-verdicts", "PASS")

    assert result.returncode == 0
    audit, report, output_dir = _load_artifacts(tmp_path)
    assert audit["initial_task"] == "Implement a fake review loop CLI."
    assert audit["max_attempts"] == 1
    assert audit["reviewer_verdicts"] == ["PASS"]
    assert audit["final_status"] == "PASS"
    assert audit["git_action_attempted"] is False
    assert audit["live_external_actions_enabled"] is False
    assert "Final status: PASS" in result.stdout
    assert str(output_dir / "audit.json") in result.stdout
    assert str(output_dir / "final_report.md") in result.stdout
    assert "fake/non-live" in report


def test_cli_revise_then_pass_writes_follow_up_prompt(tmp_path: Path):
    result = _run_cli(tmp_path, "--max-attempts", "2", "--fake-reviewer-verdicts", "REVISE,PASS")

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["max_attempts"] == 2
    assert audit["reviewer_verdicts"] == ["REVISE", "PASS"]
    assert len(audit["attempts"]) == 2
    assert audit["attempts"][0]["follow_up_prompt"]
    assert "Reviewer requested another attempt." in audit["attempts"][0]["follow_up_prompt"]
    assert "Follow-up prompt" in report
    assert "REVISE" in report
    assert "PASS" in report


def test_cli_revise_until_max_attempts_exits_needs_review(tmp_path: Path):
    result = _run_cli(tmp_path, "--max-attempts", "2", "--fake-reviewer-verdicts", "REVISE,REVISE")

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["final_status"] == "NEEDS_REVIEW"
    assert len(audit["attempts"]) == 2
    assert "Final status: NEEDS_REVIEW" in result.stdout
    assert "Number of attempts: 2" in report


def test_cli_blocked_stops_immediately(tmp_path: Path):
    result = _run_cli(tmp_path, "--max-attempts", "3", "--fake-reviewer-verdicts", "BLOCKED")

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["final_status"] == "BLOCKED"
    assert len(audit["attempts"]) == 1
    assert audit["reviewer_verdicts"] == ["BLOCKED"]
    assert "Final status: BLOCKED" in report


def test_invalid_verdict_sequence_fails_cleanly(tmp_path: Path):
    result = _run_cli(tmp_path, "--fake-reviewer-verdicts", "PASS,MAYBE")

    assert result.returncode != 0
    assert "Invalid fake reviewer verdict" in result.stderr
    assert not (tmp_path / "review-loop-output" / "audit.json").exists()


def test_default_run_is_fake_non_live_and_uses_tmp_output_only(tmp_path: Path):
    result = _run_cli(tmp_path)

    assert result.returncode == 0
    audit, report, output_dir = _load_artifacts(tmp_path)
    assert audit["final_status"] == "PASS"
    assert audit["reviewer_verdicts"] == ["PASS"]
    assert audit["git_action_attempted"] is False
    assert audit["live_external_actions_enabled"] is False
    assert audit["executor_type"] == "fake"
    assert audit["live_codex_attempted"] is False
    assert output_dir.parent == tmp_path
    assert audit["reviewer_mode"] == "replay"
    assert audit["provider_calls_allowed"] is False
    assert audit["max_reviewer_calls"] == 0
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert "No live Codex CLI executed: yes" in report
    assert "No live OpenAI/GPT reviewer call: yes" in report
    assert "No live git/GitHub action: yes" in report


def test_environment_variables_cannot_silently_enable_live_reviewer_mode(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("REVIEWER_MODE", "live-openai")
    monkeypatch.setenv("CODEX_REVIEWER_MODE", "live-openai")
    monkeypatch.setenv("REVIEW_LOOP_REVIEWER_MODE", "live-openai")
    monkeypatch.setenv("ALLOW_PROVIDER_CALLS", "true")
    monkeypatch.setenv("CODEX_ALLOW_PROVIDER_CALLS", "true")
    monkeypatch.setenv("MAX_REVIEWER_CALLS", "999")
    monkeypatch.setenv("CODEX_MAX_REVIEWER_CALLS", "999")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    result = _run_cli(tmp_path)

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["reviewer_mode"] == "replay"
    assert audit["provider_calls_allowed"] is False
    assert audit["max_reviewer_calls"] == 0
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert audit["attempts"]
    assert audit["final_status"] == "PASS"
    assert "disabled" not in (audit["blocked_reason"] or "").lower()
    assert "Provider gate blocked the run before executor start." not in report
    assert "No live OpenAI/GPT reviewer call: yes" in report


def test_live_reviewer_mode_without_allow_provider_calls_fails_closed_before_attempt_one(tmp_path: Path):
    build_loop_calls = 0

    def build_loop_spy(args, dry_run_external_calls):
        nonlocal build_loop_calls
        build_loop_calls += 1
        raise AssertionError("_build_loop must not be called when provider gate blocks.")

    result = _run_cli(
        tmp_path,
        "--reviewer-mode",
        "live-openai",
        "--max-reviewer-calls",
        "1",
        build_loop=build_loop_spy,
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert build_loop_calls == 0
    assert audit["reviewer_mode"] == "live-openai"
    assert audit["provider_calls_allowed"] is False
    assert audit["max_reviewer_calls"] == 1
    assert audit["provider_gate_passed"] is False
    assert audit["provider_gate_blocked"] is True
    assert "allow-provider-calls" in audit["blocked_reason"]
    assert audit["attempts"] == []
    assert "provider gate blocked" in report.lower()


def test_live_reviewer_mode_without_positive_call_budget_fails_closed_before_attempt_one(tmp_path: Path):
    build_loop_calls = 0

    def build_loop_spy(args, dry_run_external_calls):
        nonlocal build_loop_calls
        build_loop_calls += 1
        raise AssertionError("_build_loop must not be called when provider gate blocks.")

    result = _run_cli(
        tmp_path,
        "--reviewer-mode",
        "live-openai",
        "--allow-provider-calls",
        "true",
        "--max-reviewer-calls",
        "0",
        build_loop=build_loop_spy,
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert build_loop_calls == 0
    assert audit["reviewer_mode"] == "live-openai"
    assert audit["provider_calls_allowed"] is True
    assert audit["max_reviewer_calls"] == 0
    assert audit["provider_gate_passed"] is False
    assert audit["provider_gate_blocked"] is True
    assert "max-reviewer-calls" in audit["blocked_reason"]
    assert audit["attempts"] == []
    assert "provider gate blocked" in report.lower()


def test_live_reviewer_mode_with_budget_but_without_allow_provider_calls_fails_closed_before_attempt_one(tmp_path: Path):
    build_loop_calls = 0

    def build_loop_spy(args, dry_run_external_calls):
        nonlocal build_loop_calls
        build_loop_calls += 1
        raise AssertionError("_build_loop must not be called when provider gate blocks.")

    result = _run_cli(
        tmp_path,
        "--reviewer-mode",
        "live-openai",
        "--max-reviewer-calls",
        "5",
        build_loop=build_loop_spy,
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert build_loop_calls == 0
    assert audit["reviewer_mode"] == "live-openai"
    assert audit["provider_calls_allowed"] is False
    assert audit["max_reviewer_calls"] == 5
    assert audit["provider_gate_passed"] is False
    assert audit["provider_gate_blocked"] is True
    assert "allow-provider-calls" in audit["blocked_reason"]
    assert audit["attempts"] == []
    assert "provider gate blocked" in report.lower()


def test_live_reviewer_mode_with_both_gates_reaches_only_disabled_stub(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--reviewer-mode",
        "live-openai",
        "--allow-provider-calls",
        "true",
        "--max-reviewer-calls",
        "1",
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["reviewer_mode"] == "live-openai"
    assert audit["provider_calls_allowed"] is True
    assert audit["max_reviewer_calls"] == 1
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert audit["attempts"] == []
    assert "disabled" in (audit["blocked_reason"] or "").lower()
    assert "Provider gate blocked the run before executor start." not in report


def test_allow_provider_calls_alone_does_not_change_reviewer_mode(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--allow-provider-calls",
        "true",
        "--max-reviewer-calls",
        "3",
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["reviewer_mode"] == "replay"
    assert audit["provider_calls_allowed"] is True
    assert audit["max_reviewer_calls"] == 3
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert audit["final_status"] == "PASS"
    assert "fake/non-live" in report


def test_cli_operator_smoke_dry_run_writes_clean_tracked_tree_metadata(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--task",
        "Operator smoke: fake review loop.",
        "--executor",
        "fake",
        "--enable-live-codex",
        "false",
        "--dry-run-external-calls",
        "true",
        "--fake-reviewer-verdicts",
        "PASS",
    )

    output_dir = tmp_path / "review-loop-output"
    audit_path = output_dir / "audit.json"
    report_path = output_dir / "final_report.md"

    assert result.returncode == 0
    assert audit_path.exists()
    assert report_path.exists()

    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["pre_run_tracked_dirty"] is False
    assert audit["pre_run_tracked_status"] == ""
    assert audit["final_tracked_dirty"] is False
    assert audit["final_tracked_status"] == ""
    assert audit["attempts"][0]["post_attempt_tracked_dirty"] is False
    assert audit["attempts"][0]["post_attempt_tracked_status"] == ""
    assert audit["executor_type"] == "fake"
    assert audit["live_codex_enabled"] is False
    assert audit["dry_run_external_calls"] is True
    assert audit["live_codex_attempted"] is False
    assert audit["reviewer_mode"] == "replay"
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert audit["blocked_reason"] is None
    assert "Pre-run tracked tree dirty: False" in report
    assert "Pre-run tracked status: (clean)" in report
    assert "Final tracked tree dirty: False" in report
    assert "Final tracked status: (clean)" in report
    assert "Attempt 1 tracked tree dirty: False" in report
    assert "Attempt 1 tracked status: (clean)" in report


def test_output_files_are_written_only_inside_requested_tmp_path(tmp_path: Path):
    result = _run_cli(tmp_path, "--fake-reviewer-verdicts", "PASS")

    assert result.returncode == 0
    output_dir = tmp_path / "review-loop-output"
    assert (output_dir / "audit.json").exists()
    assert (output_dir / "final_report.md").exists()
    assert not (ROOT / "codex_runs" / "audit.json").exists()


def test_codex_cli_with_live_disabled_reports_blocked_non_live_reason(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--executor",
        "codex_cli",
        "--enable-live-codex",
        "false",
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["executor_type"] == "codex_cli"
    assert audit["live_codex_enabled"] is False
    assert audit["live_codex_attempted"] is False
    assert "blocked_reason" in audit
    assert "disabled" in audit["blocked_reason"].lower()
    assert "No live Codex CLI executed: yes" in report


def test_codex_cli_with_live_flag_true_but_dry_run_enabled_still_does_not_run(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--executor",
        "codex_cli",
        "--enable-live-codex",
        "true",
        "--dry-run-external-calls",
        "true",
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["executor_type"] == "codex_cli"
    assert audit["live_codex_enabled"] is True
    assert audit["live_codex_attempted"] is False
    assert "dry-run" in audit["blocked_reason"].lower()
    assert "blocked reason" in report.lower()


def test_cli_audit_and_report_include_parsed_executor_fields(tmp_path: Path):
    round_result = CodexRoundResult(
        changed_files=[
            "research_lab/orchestration/codex_review_loop.py",
            "tests/test_codex_review_loop_output_parser.py",
        ],
        diff_line_count=42,
        proposed_commands=[],
        summary="Updated parser and review-loop wiring.",
        patch_digest="Updated parser and review-loop wiring.",
        meaningful_progress=True,
        executor_details={
            "executor_type": "codex_cli",
            "live_codex_enabled": True,
            "live_codex_attempted": True,
            "codex_command": "codex",
            "codex_timeout_seconds": 300,
            "codex_exit_code": 0,
            "stdout_summary": "structured contract emitted",
            "stderr_summary": "",
            "blocked_reason": None,
            "parsed_output": {
                "status": "completed",
                "summary": "Updated parser and review-loop wiring.",
                "changed_files": [
                    "research_lab/orchestration/codex_review_loop.py",
                    "tests/test_codex_review_loop_output_parser.py",
                ],
                "diff_summary": {
                    "files_changed": 2,
                    "insertions": 10,
                    "deletions": 3,
                    "line_count": 42,
                },
                "validation": {
                    "commands": [
                        {
                            "command": "python -m pytest tests/test_codex_review_loop_output_parser.py -q",
                            "exit_code": 0,
                            "stdout": "7 passed",
                            "stderr": "",
                        }
                    ],
                    "overall_status": "passed",
                },
                "blocked_reason": None,
                "raw_notes": "",
                "parser_warning": None,
                "parse_error": None,
                "source_format": "whole_stdout_json",
                "exit_code": 0,
            },
        },
    )

    result = _run_cli(
        tmp_path,
        build_executor=lambda args, dry_run_external_calls: FakeReviewLoopExecutor([round_result]),
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    parsed_output = audit["attempts"][0]["executor_result"]["executor_details"]["parsed_output"]
    assert parsed_output["changed_files"] == [
        "research_lab/orchestration/codex_review_loop.py",
        "tests/test_codex_review_loop_output_parser.py",
    ]
    assert parsed_output["diff_summary"]["line_count"] == 42
    assert parsed_output["validation"]["overall_status"] == "passed"
    assert "Updated parser and review-loop wiring." in report
    assert "tests/test_codex_review_loop_output_parser.py" in report


def test_cli_dirty_tree_abort_writes_audit_and_report_metadata(tmp_path: Path):
    def build_stub_loop(args, dry_run_external_calls):
        class _Loop:
            def run(self, task: str) -> StubAudit:
                return StubAudit(
                    {
                        "run_id": "review-loop-cli-dirty",
                        "initial_task": task,
                        "attempts": [],
                        "verdicts": [],
                        "changed_files_per_attempt": [],
                        "validation_outputs": [],
                        "reviewer_feedback": [],
                        "final_status": "BLOCKED",
                        "git_action_attempted": False,
                        "live_external_actions_enabled": False,
                        "protected_paths_touched": [],
                        "disallowed_paths_touched": [],
                        "pre_run_tracked_dirty": True,
                        "pre_run_tracked_status": " M research_lab/orchestration/codex_review_loop.py",
                        "final_tracked_dirty": True,
                        "final_tracked_status": " M research_lab/orchestration/codex_review_loop.py",
                        "tracked_tree_failure_reason": None,
                    }
                )

        return _Loop()

    result = _run_cli(tmp_path, "--task", "Abort when tracked tree is dirty.", build_loop=build_stub_loop)

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["pre_run_tracked_dirty"] is True
    assert audit["pre_run_tracked_status"] == " M research_lab/orchestration/codex_review_loop.py"
    assert audit["final_tracked_dirty"] is True
    assert audit["final_tracked_status"] == " M research_lab/orchestration/codex_review_loop.py"
    assert audit["tracked_tree_failure_reason"] is None
    assert "Pre-run tracked tree dirty: True" in report
    assert "Final tracked tree dirty: True" in report
    assert "review loop aborted before executor start" in report.lower()


def test_cli_audit_records_provider_gate_metadata(tmp_path: Path):
    result = _run_cli(
        tmp_path,
        "--allow-provider-calls",
        "true",
        "--max-reviewer-calls",
        "4",
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["reviewer_mode"] == "replay"
    assert audit["provider_calls_allowed"] is True
    assert audit["max_reviewer_calls"] == 4
    assert audit["provider_gate_passed"] is True
    assert audit["provider_gate_blocked"] is False
    assert "Reviewer mode: replay" in report
    assert "Provider calls allowed: True" in report


def test_cli_audit_includes_post_attempt_tracked_tree_metadata(tmp_path: Path):
    round_result = CodexRoundResult(
        changed_files=["research_lab/orchestration/codex_review_loop.py"],
        diff_line_count=12,
        proposed_commands=[],
        summary="Recorded tracked-tree audit metadata.",
        patch_digest="Recorded tracked-tree audit metadata.",
        meaningful_progress=True,
        executor_details={
            "executor_type": "codex_cli",
            "live_codex_enabled": True,
            "dry_run_external_calls": True,
            "live_codex_attempted": False,
            "codex_command": "codex",
            "codex_timeout_seconds": 300,
            "codex_exit_code": None,
            "stdout_summary": "",
            "stderr_summary": "",
            "blocked_reason": "Dry-run external calls are enabled.",
            "parsed_output": {},
        },
    )

    def build_stub_loop(args, dry_run_external_calls):
        class _Loop:
            def run(self, task: str) -> StubAudit:
                return StubAudit(
                    {
                        "run_id": "review-loop-cli-post-dirty",
                        "initial_task": task,
                        "attempts": [
                            {
                                "attempt_number": 1,
                                "prompt_used": task,
                                "executor_result": round_result.to_dict(),
                                "validation_result": {
                                    "success": True,
                                    "tests_requested": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                    "tests_passed": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                    "failures": [],
                                },
                                "reviewer_bundle": {
                                    "initial_task": task,
                                    "current_prompt": task,
                                    "attempt_number": 1,
                                    "changed_files": ["research_lab/orchestration/codex_review_loop.py"],
                                    "validation_output": {
                                        "success": True,
                                        "tests_requested": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                        "tests_passed": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                        "failures": [],
                                    },
                                    "diff_summary": "Recorded tracked-tree audit metadata.",
                                    "protected_paths_touched": [],
                                    "disallowed_paths_touched": [],
                                    "prior_feedback": [],
                                },
                                "reviewer_verdict": {"status": "PASS", "summary": "Approved.", "issues": []},
                                "reviewer_feedback": "Approved.",
                                "follow_up_prompt": None,
                                "post_attempt_tracked_dirty": True,
                                "post_attempt_tracked_status": " M tests/test_codex_review_loop_cli.py",
                            }
                        ],
                        "verdicts": ["PASS"],
                        "changed_files_per_attempt": [["research_lab/orchestration/codex_review_loop.py"]],
                        "validation_outputs": [
                            {
                                "success": True,
                                "tests_requested": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                "tests_passed": ["python -m pytest tests/test_codex_review_loop.py -q"],
                                "failures": [],
                            }
                        ],
                        "reviewer_feedback": ["Approved."],
                        "final_status": "PASS",
                        "git_action_attempted": False,
                        "live_external_actions_enabled": False,
                        "protected_paths_touched": [],
                        "disallowed_paths_touched": [],
                        "pre_run_tracked_dirty": False,
                        "pre_run_tracked_status": "",
                        "final_tracked_dirty": True,
                        "final_tracked_status": " M tests/test_codex_review_loop_cli.py",
                        "tracked_tree_failure_reason": None,
                    }
                )

        return _Loop()

    result = _run_cli(
        tmp_path,
        "--task",
        "Record post-attempt tracked tree state.",
        build_loop=build_stub_loop,
    )

    assert result.returncode == 0
    audit, report, _ = _load_artifacts(tmp_path)
    assert audit["dry_run_external_calls"] is True
    assert audit["attempts"][0]["post_attempt_tracked_dirty"] is True
    assert audit["attempts"][0]["post_attempt_tracked_status"] == " M tests/test_codex_review_loop_cli.py"
    assert audit["final_tracked_dirty"] is True
    assert audit["final_tracked_status"] == " M tests/test_codex_review_loop_cli.py"
    assert audit["live_codex_attempted"] is False
    assert "Attempt 1 tracked tree dirty: True" in report
    assert "Dry-run external calls: True" in report
