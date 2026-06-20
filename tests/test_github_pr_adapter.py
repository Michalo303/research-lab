from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research_lab.orchestration.codex_autonomous_contract import (
    GitActionRequest,
    LoopMode,
    LoopStatus,
)
from research_lab.orchestration.github_pr_adapter import GitHubPrAdapter, GitHubPrConfig
from scripts.run_codex_auto_loop import _build_git_action, parse_args


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACKED_STATUS = (
    " M research_lab/orchestration/gpt_reviewer_adapter.py\n"
    " M tests/test_gpt_reviewer_adapter.py\n"
)
CURRENT_BRANCH = "codex/example\n"


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


def _request(**overrides) -> GitActionRequest:
    request = GitActionRequest(
        mode=LoopMode.AUTO_PR,
        branch="codex/example",
        changed_files=["research_lab/orchestration/gpt_reviewer_adapter.py", "tests/test_gpt_reviewer_adapter.py"],
        diff_line_count=20,
        reviewer_status=LoopStatus.PASS,
        validation_success=True,
        policy_status="PASS",
        max_changed_files=10,
        max_diff_lines=1000,
    )
    for key, value in overrides.items():
        setattr(request, key, value)
    return request


def test_default_git_action_remains_fake():
    args = parse_args([])

    assert args.git_action == "fake"
    assert args.enable_live_git_action == "false"


def test_github_pr_action_blocks_unless_live_enabled_true():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=False), runner=StubRunner([]), repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert result.git_action_blocked_reason
    assert result.git_action_attempted is False


def test_github_pr_action_blocks_in_dry_run():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)

    result = adapter.plan_after_pass(_request(mode=LoopMode.DRY_RUN))

    assert "dry_run" in result.git_action_blocked_reason


def test_github_pr_action_blocks_in_safe_local():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)

    result = adapter.plan_after_pass(_request(mode=LoopMode.SAFE_LOCAL))

    assert "safe_local" in result.git_action_blocked_reason


@pytest.mark.parametrize("mode", [LoopMode.AUTO_PR, LoopMode.SUPER_AUTO])
def test_github_pr_action_allows_live_flow_only_after_pass_validation_and_policy(mode):
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="[codex/example abc123] msg", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request(mode=mode))

    assert result.commit_created is True
    assert result.push_completed is True
    assert result.pr_created is True


def test_blocks_if_local_branch_is_main():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(branch="main"))
    assert "main" in result.git_action_blocked_reason


def test_blocks_if_branch_does_not_start_with_codex():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(branch="feature/example"))
    assert "codex/" in result.git_action_blocked_reason


def test_blocks_if_protected_path_touched():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(protected_paths_touched=["reports/daily/x.md"]))
    assert "protected" in result.git_action_blocked_reason


def test_blocks_if_disallowed_path_touched():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(disallowed_paths_touched=["README.md"]))
    assert "disallowed" in result.git_action_blocked_reason


def test_blocks_if_changed_files_exceed_limit():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(changed_files=["a.py", "b.py"], max_changed_files=1))
    assert "max_changed_files" in result.git_action_blocked_reason


def test_blocks_if_diff_lines_exceed_limit():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(diff_line_count=2000, max_diff_lines=1000))
    assert "max_diff_lines" in result.git_action_blocked_reason


def test_stages_only_exact_allowed_files_and_never_uses_git_add_dot():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request())

    add_call = runner.calls[2]["argv"]
    assert add_call[:2] == ["git", "add"]
    assert "." not in add_call
    assert result.staged_files == ["research_lab/orchestration/gpt_reviewer_adapter.py", "tests/test_gpt_reviewer_adapter.py"]


def test_never_force_pushes_or_pushes_main():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    adapter.plan_after_pass(_request())

    push_call = runner.calls[5]["argv"]
    assert "--force" not in push_call
    assert push_call == ["git", "push", "origin", "codex/example"]


def test_never_merges():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(mode=LoopMode.DRY_RUN))
    assert result.merge_blocked is True


def test_creates_commit_with_configured_message():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit msg"),
        runner=runner,
        repo_root=ROOT,
    )

    adapter.plan_after_pass(_request())

    assert runner.calls[3]["argv"] == ["git", "commit", "-m", "Commit msg"]


def test_creates_pr_against_main_with_configured_title_body():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="PR Title", pr_body="PR Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request())

    pr_call = runner.calls[6]["argv"]
    assert pr_call == ["gh", "pr", "create", "--base", "main", "--head", "codex/example", "--title", "PR Title", "--body", "PR Body"]
    assert result.pr_url == "https://github.com/example/repo/pull/12"
    assert result.pr_number == 12


def test_no_runtime_or_diagnostic_files_staged():
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=StubRunner([]), repo_root=ROOT)
    result = adapter.plan_after_pass(_request(changed_files=["codex_runs/audit.json"]))
    assert "forbidden staging path" in result.git_action_blocked_reason


def test_missing_gh_command_maps_to_blocked():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            FileNotFoundError("gh not found"),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )
    result = adapter.plan_after_pass(_request())
    assert "gh" in result.git_action_blocked_reason.lower()


def test_command_failure_maps_to_blocked():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=1, stdout="", stderr="failed"),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )
    result = adapter.plan_after_pass(_request())
    assert "failed" in result.git_action_blocked_reason.lower()


def test_blocks_when_requested_changed_file_is_missing_from_tracked_status():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout=" M research_lab/orchestration/gpt_reviewer_adapter.py\n",
                stderr="",
            ),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert "not present in tracked status" in result.git_action_blocked_reason
    assert "tests/test_gpt_reviewer_adapter.py" in result.git_action_blocked_reason


def test_blocks_when_tracked_status_contains_extra_file_not_in_request():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout=DEFAULT_TRACKED_STATUS + " M scripts/run_codex_auto_loop.py\n",
                stderr="",
            ),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert "unexpected paths" in result.git_action_blocked_reason
    assert "scripts/run_codex_auto_loop.py" in result.git_action_blocked_reason


def test_allows_exact_same_set_with_different_ordering():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout=" M tests/test_gpt_reviewer_adapter.py\n M research_lab/orchestration/gpt_reviewer_adapter.py\n",
                stderr="",
            ),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request())

    assert result.pr_created is True
    assert runner.calls[2]["argv"] == [
        "git",
        "add",
        "--",
        "research_lab/orchestration/gpt_reviewer_adapter.py",
        "tests/test_gpt_reviewer_adapter.py",
    ]


def test_deduplicates_duplicate_request_changed_files_before_staging():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(
        _request(
            changed_files=[
                "research_lab/orchestration/gpt_reviewer_adapter.py",
                "research_lab/orchestration/gpt_reviewer_adapter.py",
                "tests/test_gpt_reviewer_adapter.py",
            ]
        )
    )

    assert result.staged_files == [
        "research_lab/orchestration/gpt_reviewer_adapter.py",
        "tests/test_gpt_reviewer_adapter.py",
    ]
    assert runner.calls[2]["argv"] == [
        "git",
        "add",
        "--",
        "research_lab/orchestration/gpt_reviewer_adapter.py",
        "tests/test_gpt_reviewer_adapter.py",
    ]


def test_empty_changed_files_with_allow_empty_commit_false_still_blocks():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout="", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, allow_empty_commit=False),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request(changed_files=[]))

    assert "allow_empty_commit is false" in result.git_action_blocked_reason


def test_empty_changed_files_with_allow_empty_commit_true_skips_git_add_and_commits_allow_empty():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(
            live_enabled=True,
            allow_empty_commit=True,
            pr_title="Title",
            pr_body="Body",
            commit_message="Commit",
        ),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request(changed_files=[]))

    assert result.commit_created is True
    assert len(runner.calls) == 6
    assert runner.calls[2]["argv"] == ["git", "commit", "--allow-empty", "-m", "Commit"]
    assert all(call["argv"][:2] != ["git", "add"] for call in runner.calls)


def test_blocks_when_current_branch_differs_from_request_branch():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout="codex/other-branch\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert "current branch does not match request.branch" in result.git_action_blocked_reason
    assert all(call["argv"][:2] != ["git", "add"] for call in runner.calls)


def test_blocks_when_current_branch_is_main_even_if_request_branch_is_codex():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout="main\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request(branch="codex/example"))

    assert "current branch does not match request.branch" in result.git_action_blocked_reason
    assert all(call["argv"][:2] != ["git", "add"] for call in runner.calls)


def test_branch_check_happens_before_git_add_and_commit():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout="codex/other-branch\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    adapter.plan_after_pass(_request())

    assert [call["argv"][0:3] for call in runner.calls] == [["git", "branch", "--show-current"]]


def test_allows_flow_when_current_branch_equals_request_branch():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request())

    assert result.pr_created is True


def test_blocks_when_untracked_file_exists():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["git", "branch"],
                returncode=0,
                stdout=CURRENT_BRANCH,
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout="?? notes.tmp\n",
                stderr="",
            ),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request(changed_files=[]))

    assert "untracked files are present" in result.git_action_blocked_reason


def test_blocks_when_untracked_helper_exists_alongside_tracked_modified_file():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout=DEFAULT_TRACKED_STATUS + "?? helper.tmp\n",
                stderr="",
            ),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert "untracked files are present" in result.git_action_blocked_reason
    assert "helper.tmp" in result.git_action_blocked_reason


def test_does_not_stage_untracked_runtime_or_diagnostic_files():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout=DEFAULT_TRACKED_STATUS + "?? codex_runs/codex-auto-123/audit.json\n",
                stderr="",
            ),
        ]
    )
    adapter = GitHubPrAdapter(config=GitHubPrConfig(live_enabled=True), runner=runner, repo_root=ROOT)

    result = adapter.plan_after_pass(_request())

    assert "untracked files are present" in result.git_action_blocked_reason
    assert all(call["argv"][:2] != ["git", "add"] for call in runner.calls)


def test_clean_exact_tracked_set_still_proceeds():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(args=["git", "branch"], returncode=0, stdout=CURRENT_BRANCH, stderr=""),
            subprocess.CompletedProcess(args=["git", "status"], returncode=0, stdout=DEFAULT_TRACKED_STATUS, stderr=""),
            subprocess.CompletedProcess(args=["git", "add"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "commit"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "rev-parse"], returncode=0, stdout="abc123\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "push"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["gh", "pr", "create"], returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr=""),
        ]
    )
    adapter = GitHubPrAdapter(
        config=GitHubPrConfig(live_enabled=True, pr_title="Title", pr_body="Body", commit_message="Commit"),
        runner=runner,
        repo_root=ROOT,
    )

    result = adapter.plan_after_pass(_request())

    assert result.pr_created is True


def test_cli_accepts_git_action_flags():
    args = parse_args(
        [
            "--git-action",
            "github_pr",
            "--enable-live-git-action",
            "true",
            "--github-base-branch",
            "main",
            "--github-remote",
            "origin",
            "--github-pr-title",
            "Title",
            "--github-pr-body",
            "Body",
            "--commit-message",
            "Commit",
            "--allow-empty-commit",
            "true",
        ]
    )
    assert args.git_action == "github_pr"
    assert args.enable_live_git_action == "true"
    assert args.github_base_branch == "main"
    assert args.github_remote == "origin"
    assert args.github_pr_title == "Title"
    assert args.github_pr_body == "Body"
    assert args.commit_message == "Commit"
    assert args.allow_empty_commit == "true"


def test_build_git_action_defaults_to_fake():
    args = parse_args([])
    git_action = _build_git_action(args, ROOT)
    assert git_action.__class__.__name__ == "FakeGitAction"
