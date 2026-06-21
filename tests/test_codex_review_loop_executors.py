from __future__ import annotations

import json
import subprocess
from pathlib import Path

from research_lab.orchestration.codex_review_loop_executors import (
    CodexCliReviewLoopExecutor,
    FakeReviewLoopExecutorFactory,
)


ROOT = Path(__file__).resolve().parents[1]


def _contract_stdout(**overrides) -> str:
    payload = {
        "status": "completed",
        "summary": "Updated parser and tests.",
        "changed_files": [
            "research_lab/orchestration/codex_review_loop.py",
            "tests/test_codex_review_loop.py",
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
                    "command": "python -m pytest tests/test_codex_review_loop.py -q",
                    "exit_code": 0,
                    "stdout": "1 passed",
                    "stderr": "",
                }
            ],
            "overall_status": "passed",
        },
        "blocked_reason": None,
        "raw_notes": "optional",
    }
    payload.update(overrides)
    return json.dumps(payload)


class StubRunner:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_fake_executor_factory_remains_deterministic():
    executor = FakeReviewLoopExecutorFactory().build(max_attempts=2)

    first = executor.execute("task one", 1)
    second = executor.execute("task two", 2)

    assert first.executor_details["executor_type"] == "fake"
    assert first.executor_details["live_codex_attempted"] is False
    assert second.summary == "Fake executor completed attempt 2."


def test_codex_cli_live_disabled_does_not_call_runner():
    runner = StubRunner([])
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=30,
        live_codex_enabled=False,
        dry_run_external_calls=False,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert runner.calls == []
    assert result.executor_details["executor_type"] == "codex_cli"
    assert result.executor_details["live_codex_attempted"] is False
    assert "disabled" in result.executor_details["blocked_reason"].lower()


def test_codex_cli_dry_run_blocks_runner_even_when_live_flag_true():
    runner = StubRunner([])
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=30,
        live_codex_enabled=True,
        dry_run_external_calls=True,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert runner.calls == []
    assert result.executor_details["live_codex_attempted"] is False
    assert "dry-run" in result.executor_details["blocked_reason"].lower()


def test_codex_cli_live_enabled_calls_runner_with_args_list_and_timeout():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "--help"],
                returncode=0,
                stdout="applied safe local changes",
                stderr="",
            )
        ]
    )
    timestamps = iter([10.0, 12.5])
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=45,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
        clock=lambda: next(timestamps),
    )

    result = executor.execute("Implement safely.", 1)

    assert runner.calls[0]["argv"] == ["codex", "exec", "--json", "-"]
    assert runner.calls[0]["shell"] is False
    assert runner.calls[0]["timeout"] == 45
    assert runner.calls[0]["cwd"] == str(ROOT)
    assert result.executor_details["live_codex_attempted"] is True
    assert result.executor_details["codex_exit_code"] == 0
    assert result.executor_details["stdout_summary"] == "applied safe local changes"
    assert result.executor_details["stderr_summary"] == ""
    assert result.executor_details["duration_seconds"] == 2.5


def test_codex_cli_valid_json_populates_changed_files_summary_and_diff_count():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout=_contract_stdout(),
                stderr="",
            )
        ]
    )
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=45,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert result.changed_files == [
        "research_lab/orchestration/codex_review_loop.py",
        "tests/test_codex_review_loop.py",
    ]
    assert result.summary == "Updated parser and tests."
    assert result.diff_line_count == 42
    assert result.executor_details["parsed_output"]["diff_summary"]["files_changed"] == 2
    assert result.executor_details["parsed_output"]["validation"]["overall_status"] == "passed"


def test_codex_cli_non_zero_exit_code_is_captured_and_surfaced():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=17,
                stdout="partial output",
                stderr="failure happened",
            )
        ]
    )
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=45,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert result.executor_failed is True
    assert result.executor_details["codex_exit_code"] == 17
    assert result.executor_details["stdout_summary"] == "partial output"
    assert result.executor_details["stderr_summary"] == "failure happened"
    assert "exit code 17" in result.summary.lower()


def test_codex_cli_non_json_output_sets_parser_warning_without_crashing():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="not valid json but still a useful text summary",
                stderr="",
            )
        ]
    )
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=45,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert result.executor_failed is False
    assert result.changed_files == []
    assert result.summary == "not valid json but still a useful text summary"
    assert result.executor_details["parsed_output"]["parser_warning"] is not None


def test_codex_cli_failed_status_from_parser_is_surfaced():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=17,
                stdout=_contract_stdout(
                    status="failed",
                    summary="Pytest failed.",
                    validation={
                        "commands": [
                            {
                                "command": "python -m pytest tests/test_codex_review_loop.py -q",
                                "exit_code": 1,
                                "stdout": "",
                                "stderr": "AssertionError",
                            }
                        ],
                        "overall_status": "failed",
                    },
                ),
                stderr="AssertionError",
            )
        ]
    )
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=45,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
    )

    result = executor.execute("Implement safely.", 1)

    assert result.executor_failed is True
    assert result.summary == "Pytest failed."
    assert result.executor_details["parsed_output"]["status"] == "failed"
    assert result.executor_details["parsed_output"]["validation"]["overall_status"] == "failed"
    assert result.executor_details["codex_exit_code"] == 17


def test_codex_cli_timeout_is_captured_without_real_subprocess():
    runner = StubRunner([subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=9, output="slow", stderr="late")])
    timestamps = iter([20.0, 29.0])
    executor = CodexCliReviewLoopExecutor(
        repo_root=ROOT,
        codex_command="codex",
        timeout_seconds=9,
        live_codex_enabled=True,
        dry_run_external_calls=False,
        runner=runner,
        clock=lambda: next(timestamps),
    )

    result = executor.execute("Implement safely.", 1)

    assert result.executor_failed is True
    assert result.executor_details["live_codex_attempted"] is True
    assert result.executor_details["codex_exit_code"] is None
    assert result.executor_details["stdout_summary"] == "slow"
    assert result.executor_details["stderr_summary"] == "late"
    assert result.executor_details["duration_seconds"] == 9.0
    assert "timed out" in result.summary.lower()
