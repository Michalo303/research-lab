from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import scripts.run_codex_review_loop as cli_script
from research_lab.orchestration.codex_autonomous_contract import CodexRoundResult
from research_lab.orchestration.codex_review_loop import FakeReviewLoopExecutor


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_codex_review_loop.py"


def _run_cli(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    output_dir = tmp_path / "review-loop-output"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task",
            "Implement a fake review loop CLI.",
            "--output-dir",
            str(output_dir),
            *extra_args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_artifacts(tmp_path: Path) -> tuple[dict, str, Path]:
    output_dir = tmp_path / "review-loop-output"
    audit_path = output_dir / "audit.json"
    report_path = output_dir / "final_report.md"
    return json.loads(audit_path.read_text(encoding="utf-8")), report_path.read_text(encoding="utf-8"), output_dir


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
    assert "No live Codex CLI executed: yes" in report
    assert "No live OpenAI/GPT reviewer call: yes" in report
    assert "No live git/GitHub action: yes" in report


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


def test_cli_audit_and_report_include_parsed_executor_fields(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "review-loop-output"
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

    monkeypatch.setattr(
        cli_script,
        "_build_executor",
        lambda args, dry_run_external_calls: FakeReviewLoopExecutor([round_result]),
    )

    exit_code = cli_script.main(
        [
            "--task",
            "Implement a fake review loop CLI.",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    report = (output_dir / "final_report.md").read_text(encoding="utf-8")
    parsed_output = audit["attempts"][0]["executor_result"]["executor_details"]["parsed_output"]
    assert parsed_output["changed_files"] == [
        "research_lab/orchestration/codex_review_loop.py",
        "tests/test_codex_review_loop_output_parser.py",
    ]
    assert parsed_output["diff_summary"]["line_count"] == 42
    assert parsed_output["validation"]["overall_status"] == "passed"
    assert "Updated parser and review-loop wiring." in report
    assert "tests/test_codex_review_loop_output_parser.py" in report
