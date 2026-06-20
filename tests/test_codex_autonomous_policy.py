from __future__ import annotations

from research_lab.orchestration.codex_autonomous_contract import CodexLoopConfig, LoopMode
from research_lab.orchestration.codex_autonomous_policy import evaluate_round_policy


def _config() -> CodexLoopConfig:
    return CodexLoopConfig.for_mode(LoopMode.DRY_RUN)


def test_protected_path_change_is_unsafe():
    result = evaluate_round_policy(
        _config(),
        changed_files=["reports/daily/2026-06-05.md"],
        diff_line_count=1,
        proposed_commands=[],
        branch="codex/safe",
        human_merge_confirmed=False,
    )

    assert result.status == "UNSAFE"
    assert result.protected_paths_touched == ["reports/daily/2026-06-05.md"]
    assert result.disallowed_paths_touched == []


def test_allowed_paths_are_permitted_for_expected_prefixes_and_exact_paths():
    for path in (
        "research_lab/orchestration/codex_autonomous_policy.py",
        "tests/test_codex_autonomous_policy.py",
        "scripts/run_codex_auto_loop.py",
        "tasks/inbox/.gitkeep",
        "codex_runs/.gitkeep",
        ".gitignore",
    ):
        result = evaluate_round_policy(
            _config(),
            changed_files=[path],
            diff_line_count=1,
            proposed_commands=[],
            branch="codex/safe",
            human_merge_confirmed=False,
        )
        assert result.status == "PASS"
        assert result.disallowed_paths_touched == []


def test_file_outside_allowed_paths_is_unsafe():
    result = evaluate_round_policy(
        _config(),
        changed_files=["docs/out_of_scope.md"],
        diff_line_count=1,
        proposed_commands=[],
        branch="codex/safe",
        human_merge_confirmed=False,
    )

    assert result.status == "UNSAFE"
    assert result.disallowed_paths_touched == ["docs/out_of_scope.md"]


def test_forbidden_command_is_unsafe():
    result = evaluate_round_policy(
        _config(),
        changed_files=["research_lab/orchestration/codex_autonomous_loop.py"],
        diff_line_count=10,
        proposed_commands=["git reset --hard HEAD"],
        branch="codex/safe",
        human_merge_confirmed=False,
    )

    assert result.status == "UNSAFE"
    assert "git reset --hard" in result.forbidden_commands_detected


def test_deploy_and_service_restart_are_unsafe():
    deploy_result = evaluate_round_policy(
        _config(),
        changed_files=["scripts/run_codex_auto_loop.py"],
        diff_line_count=10,
        proposed_commands=["deploy staging"],
        branch="codex/safe",
        human_merge_confirmed=False,
    )
    restart_result = evaluate_round_policy(
        _config(),
        changed_files=["scripts/run_codex_auto_loop.py"],
        diff_line_count=10,
        proposed_commands=["systemctl restart codex-auto.service"],
        branch="codex/safe",
        human_merge_confirmed=False,
    )

    assert deploy_result.status == "UNSAFE"
    assert restart_result.status == "UNSAFE"


def test_hetzner_sync_registry_append_push_main_and_merge_are_unsafe():
    for commands, branch in (
        (["scripts/run_safe_sync_with_preflight.sh"], "codex/safe"),
        (["registry append candidate"], "codex/safe"),
        (["git push origin main"], "main"),
        (["git merge main"], "codex/safe"),
    ):
        result = evaluate_round_policy(
            _config(),
            changed_files=["scripts/run_codex_auto_loop.py"],
            diff_line_count=10,
            proposed_commands=commands,
            branch=branch,
            human_merge_confirmed=False,
        )
        assert result.status == "UNSAFE"


def test_diff_and_file_count_limits_are_enforced():
    config = CodexLoopConfig.for_mode(LoopMode.DRY_RUN)
    config.max_changed_files = 1
    config.max_diff_lines = 5

    result = evaluate_round_policy(
        config,
        changed_files=["a.py", "b.py"],
        diff_line_count=10,
        proposed_commands=[],
        branch="codex/safe",
        human_merge_confirmed=False,
    )

    assert result.status == "UNSAFE"
    assert result.diff_limit_exceeded is True
    assert result.changed_file_limit_exceeded is True
