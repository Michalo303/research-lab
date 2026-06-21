from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess
import time
from typing import Callable

from research_lab.orchestration.codex_autonomous_contract import CodexRoundResult
from research_lab.orchestration.codex_review_loop import FakeReviewLoopExecutor
from research_lab.orchestration.codex_review_loop_output_parser import (
    parse_codex_review_loop_output,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class FakeReviewLoopExecutorFactory:
    def build(self, max_attempts: int) -> FakeReviewLoopExecutor:
        results: list[CodexRoundResult] = []
        for attempt_number in range(1, max_attempts + 1):
            changed_files = [
                "research_lab/orchestration/codex_review_loop.py",
                f"tests/fake_review_loop_attempt_{attempt_number}.py",
            ]
            results.append(
                CodexRoundResult(
                    changed_files=changed_files,
                    diff_line_count=10 * attempt_number,
                    proposed_commands=[],
                    summary=f"Fake executor completed attempt {attempt_number}.",
                    patch_digest=f"fake-attempt-{attempt_number}",
                    meaningful_progress=True,
                    executor_details={
                        "executor_type": "fake",
                        "live_codex_enabled": False,
                        "dry_run_external_calls": True,
                        "live_codex_attempted": False,
                        "codex_command": None,
                        "codex_timeout_seconds": None,
                        "codex_exit_code": None,
                        "stdout_summary": "",
                        "stderr_summary": "",
                        "blocked_reason": None,
                    },
                )
            )
        return FakeReviewLoopExecutor(results)


class CodexCliReviewLoopExecutor:
    def __init__(
        self,
        *,
        repo_root: Path,
        codex_command: str,
        timeout_seconds: int,
        live_codex_enabled: bool,
        dry_run_external_calls: bool,
        runner: CommandRunner = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.codex_command = codex_command
        self.timeout_seconds = timeout_seconds
        self.live_codex_enabled = live_codex_enabled
        self.dry_run_external_calls = dry_run_external_calls
        self.runner = runner
        self.clock = clock

    def execute(self, prompt: str, attempt_number: int) -> CodexRoundResult:
        blocked_reason = self._blocked_reason()
        argv = self._build_argv()
        if blocked_reason:
            return CodexRoundResult(
                changed_files=[],
                diff_line_count=0,
                proposed_commands=[],
                summary=f"Codex CLI execution not run for attempt {attempt_number}. {blocked_reason}",
                patch_digest="",
                meaningful_progress=False,
                executor_failed=False,
                executor_details=self._executor_details(
                    live_codex_attempted=False,
                    codex_exit_code=None,
                    stdout_summary="",
                    stderr_summary="",
                    duration_seconds=0.0,
                    blocked_reason=blocked_reason,
                ),
            )

        started = self.clock()
        try:
            completed = self.runner(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(self.repo_root),
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
            duration_seconds = round(self.clock() - started, 3)
            stdout_summary = _summarize_output(completed.stdout or "")
            stderr_summary = _summarize_output(completed.stderr or "")
            parsed_output = parse_codex_review_loop_output(
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                exit_code=completed.returncode,
            )
            return CodexRoundResult(
                changed_files=list(parsed_output.changed_files),
                diff_line_count=int(parsed_output.diff_summary.get("line_count", 0)),
                proposed_commands=[],
                summary=parsed_output.summary,
                patch_digest=stdout_summary or parsed_output.summary,
                meaningful_progress=bool(parsed_output.changed_files or stdout_summary.strip()),
                executor_failed=completed.returncode != 0 or parsed_output.status == "failed",
                executor_details=self._executor_details(
                    live_codex_attempted=True,
                    codex_exit_code=completed.returncode,
                    stdout_summary=stdout_summary,
                    stderr_summary=stderr_summary,
                    duration_seconds=duration_seconds,
                    blocked_reason=parsed_output.blocked_reason,
                    parsed_output=parsed_output.to_dict(),
                ),
            )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = round(self.clock() - started, 3)
            stdout_summary = _summarize_output(_timeout_text(exc.stdout))
            stderr_summary = _summarize_output(_timeout_text(exc.stderr))
            return CodexRoundResult(
                changed_files=[],
                diff_line_count=0,
                proposed_commands=[],
                summary=f"Codex CLI timed out after {self.timeout_seconds} seconds.",
                patch_digest=stdout_summary,
                meaningful_progress=False,
                executor_failed=True,
                executor_details=self._executor_details(
                    live_codex_attempted=True,
                    codex_exit_code=None,
                    stdout_summary=stdout_summary,
                    stderr_summary=stderr_summary,
                    duration_seconds=duration_seconds,
                    blocked_reason=None,
                ),
            )

    def _build_argv(self) -> list[str]:
        return [*shlex.split(self.codex_command, posix=False), "exec", "--json", "-"]

    def _blocked_reason(self) -> str | None:
        if not self.live_codex_enabled:
            return "Live Codex execution is disabled. Set --enable-live-codex true to allow subprocess execution."
        if self.dry_run_external_calls:
            return "Dry-run external calls are enabled. Set --dry-run-external-calls false to allow subprocess execution."
        return None

    def _executor_details(
        self,
        *,
        live_codex_attempted: bool,
        codex_exit_code: int | None,
        stdout_summary: str,
        stderr_summary: str,
        duration_seconds: float,
        blocked_reason: str | None,
        parsed_output: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "executor_type": "codex_cli",
            "live_codex_enabled": self.live_codex_enabled,
            "dry_run_external_calls": self.dry_run_external_calls,
            "live_codex_attempted": live_codex_attempted,
            "codex_command": self.codex_command,
            "codex_timeout_seconds": self.timeout_seconds,
            "codex_exit_code": codex_exit_code,
            "stdout_summary": stdout_summary,
            "stderr_summary": stderr_summary,
            "duration_seconds": duration_seconds,
            "blocked_reason": blocked_reason,
            "parsed_output": dict(parsed_output or {}),
        }


def _summarize_output(text: str, limit: int = 400) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _completed_summary(returncode: int, stdout_summary: str, stderr_summary: str) -> str:
    if returncode == 0:
        return stdout_summary or "Codex CLI completed successfully."
    suffix = stderr_summary or stdout_summary or "No output captured."
    return f"Codex CLI exit code {returncode}. {suffix}"


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
