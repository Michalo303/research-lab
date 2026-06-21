from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
