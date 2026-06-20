from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from pathlib import Path
from typing import Callable

from research_lab.orchestration.codex_autonomous_contract import (
    GitActionRequest,
    GitActionResult,
    LoopMode,
    LoopStatus,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

FORBIDDEN_STAGING_PREFIXES = (
    "codex_runs/",
    "reports/",
    "registry/",
    "leaderboard/",
    "cache/",
    "data/",
    "logs/",
    "local-drift-backups/",
    "secrets/",
    "tests/fixtures/ohlcv_real/",
)
FORBIDDEN_STAGING_EXACT = (".env",)
FORBIDDEN_STAGING_PATTERNS = (
    re.compile(r"^pr67_"),
    re.compile(r"^pr68_"),
)


@dataclass
class GitHubPrConfig:
    live_enabled: bool = False
    base_branch: str = "main"
    remote: str = "origin"
    pr_title: str | None = None
    pr_body: str | None = None
    commit_message: str = "Codex autonomous supervisor update"
    allow_empty_commit: bool = False
    command_timeout_seconds: int = 60


class GitHubPrAdapter:
    def __init__(
        self,
        *,
        config: GitHubPrConfig,
        repo_root: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config = config
        self.repo_root = Path(repo_root)
        self.runner = runner or self._default_runner

    def plan_after_pass(self, request: GitActionRequest) -> GitActionResult:
        result = GitActionResult(
            git_action_provider="github_pr",
            git_action_live_enabled=self.config.live_enabled,
            branch=request.branch,
            merge_blocked=True,
            deploy_blocked=True,
            registry_append_blocked=True,
            pr_base_branch=self.config.base_branch,
            pr_head_branch=request.branch,
            pr_title=self.config.pr_title,
            planned_actions=["commit", "push", "pr"],
        )

        blocked_reason = self._preflight_block_reason(request)
        if blocked_reason:
            result.git_action_blocked_reason = blocked_reason
            return result

        normalized_files = [_normalize_path(path) for path in request.changed_files]
        forbidden_staged = [path for path in normalized_files if _is_forbidden_staging_path(path)]
        if forbidden_staged:
            result.git_action_blocked_reason = f"forbidden staging path: {forbidden_staged[0]}"
            return result

        tracked_status = self._run(["git", "status", "--short", "--untracked-files=no"])
        if tracked_status.returncode != 0:
            result.git_action_blocked_reason = _command_failure_reason("git status", tracked_status)
            return result

        tracked_paths = _parse_status_paths(tracked_status.stdout)
        unexpected_tracked = [path for path in tracked_paths if path not in normalized_files]
        if unexpected_tracked:
            result.git_action_blocked_reason = (
                "tracked working tree contains unexpected paths: " + ", ".join(unexpected_tracked)
            )
            return result

        result.git_action_attempted = True
        result.staged_files = list(normalized_files)

        if normalized_files:
            add_command = ["git", "add", "--", *normalized_files]
            add_result = self._run(add_command)
            if add_result.returncode != 0:
                result.git_action_blocked_reason = _command_failure_reason("git add", add_result)
                return result

        result.commit_attempted = True
        commit_command = ["git", "commit", "-m", self.config.commit_message]
        if self.config.allow_empty_commit:
            commit_command.insert(2, "--allow-empty")
        commit_result = self._run(commit_command)
        if commit_result.returncode != 0:
            result.git_action_blocked_reason = _command_failure_reason("git commit", commit_result)
            return result
        result.commit_created = True

        sha_result = self._run(["git", "rev-parse", "HEAD"])
        if sha_result.returncode != 0:
            result.git_action_blocked_reason = _command_failure_reason("git rev-parse HEAD", sha_result)
            return result
        result.commit_sha = sha_result.stdout.strip() or None

        result.push_attempted = True
        push_result = self._run(["git", "push", self.config.remote, request.branch])
        if push_result.returncode != 0:
            result.git_action_blocked_reason = _command_failure_reason("git push", push_result)
            return result
        result.push_completed = True

        result.pr_attempted = True
        pr_title = self.config.pr_title or f"Codex update for {request.branch}"
        pr_body = self.config.pr_body or "Automated PR created by the local Codex supervisor."
        result.pr_title = pr_title
        pr_result = self._run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                self.config.base_branch,
                "--head",
                request.branch,
                "--title",
                pr_title,
                "--body",
                pr_body,
            ]
        )
        if pr_result.returncode != 0:
            result.git_action_blocked_reason = _command_failure_reason("gh pr create", pr_result)
            return result
        result.pr_created = True
        result.pr_url = _extract_pr_url(pr_result.stdout)
        result.pr_number = _extract_pr_number(result.pr_url)
        return result

    def _preflight_block_reason(self, request: GitActionRequest) -> str | None:
        if not self.config.live_enabled:
            return "live git action is disabled"
        if request.mode not in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO}:
            return f"mode {request.mode.value} does not allow live git actions"
        if request.reviewer_status is not LoopStatus.PASS:
            return f"reviewer status {request.reviewer_status.value} is not PASS"
        if not request.validation_success:
            return "validation did not succeed"
        if request.policy_status != LoopStatus.PASS.value:
            return f"policy status {request.policy_status} is not PASS"
        branch = request.branch.strip()
        if branch.lower() in {"main", "origin/main"}:
            return "refusing to act on main"
        if not branch.startswith("codex/"):
            return "branch must start with codex/"
        if request.protected_paths_touched:
            return "protected paths were touched"
        if request.disallowed_paths_touched:
            return "disallowed paths were touched"
        if request.max_changed_files and len(request.changed_files) > request.max_changed_files:
            return "max_changed_files exceeded"
        if request.max_diff_lines and request.diff_line_count > request.max_diff_lines:
            return "max_diff_lines exceeded"
        if not request.changed_files and not self.config.allow_empty_commit:
            return "changed files are empty and allow_empty_commit is false"
        return None

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                argv,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(argv, returncode=127, stdout="", stderr=str(exc))

    @staticmethod
    def _default_runner(argv, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, **kwargs)


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def _is_forbidden_staging_path(path: str) -> bool:
    normalized = _normalize_path(path)
    if normalized in FORBIDDEN_STAGING_EXACT:
        return True
    if any(normalized.startswith(prefix) for prefix in FORBIDDEN_STAGING_PREFIXES):
        return True
    return any(pattern.match(normalized) for pattern in FORBIDDEN_STAGING_PATTERNS)


def _parse_status_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        if raw_path:
            paths.append(_normalize_path(raw_path))
    return paths


def _extract_pr_url(stdout: str) -> str | None:
    for line in stdout.splitlines():
        candidate = line.strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
    return None


def _extract_pr_number(pr_url: str | None) -> int | None:
    if not pr_url:
        return None
    match = re.search(r"/pull/(\d+)$", pr_url)
    if not match:
        return None
    return int(match.group(1))


def _command_failure_reason(command_name: str, result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or f"return code {result.returncode}"
    return f"{command_name} failed: {detail}"
