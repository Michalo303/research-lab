from __future__ import annotations

from research_lab.orchestration.codex_autonomous_contract import (
    AUDIT_REQUIRED_KEYS,
    CodexLoopAudit,
    CodexLoopConfig,
    DEFAULT_ALLOWED_PATHS,
    DEFAULT_FORBIDDEN_COMMAND_FRAGMENTS,
    DEFAULT_PROTECTED_PATHS,
    GitActionResult,
    LoopMode,
    LoopStatus,
    ReviewVerdict,
    ValidationResult,
)


def test_super_auto_defaults_are_high_autonomy_but_still_dry_run_safe():
    config = CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO)

    assert config.mode is LoopMode.SUPER_AUTO
    assert config.max_rounds == 20
    assert config.max_runtime_minutes == 120
    assert config.max_changed_files == 20
    assert config.max_diff_lines == 3000
    assert config.max_test_retries == 10
    assert config.no_progress_round_limit == 3
    assert config.dry_run_external_calls is True


def test_dry_run_defaults_are_bounded():
    config = CodexLoopConfig.for_mode(LoopMode.DRY_RUN)

    assert config.max_rounds == 3
    assert config.max_runtime_minutes > 0
    assert ".env" in config.protected_paths
    assert "git reset --hard" in config.forbidden_command_fragments


def test_minimum_protected_paths_and_forbidden_fragments_are_present():
    assert DEFAULT_ALLOWED_PATHS == [
        ".gitignore",
        "research_lab/",
        "scripts/",
        "tests/",
        "tasks/",
        "codex_runs/",
    ]
    assert DEFAULT_PROTECTED_PATHS == [
        ".env",
        "secrets/",
        "registry/",
        "reports/",
        "leaderboard/",
        "cache/",
        "data/",
        "logs/",
    ]
    assert DEFAULT_FORBIDDEN_COMMAND_FRAGMENTS == [
        "git reset --hard",
        "git clean",
        "rm -rf",
        "systemctl",
        "service restart",
        "deploy",
        "daily research",
        "run_daily",
        "registry append",
        "push origin main",
        "merge main",
        "scripts/run_safe_sync_with_preflight.sh",
    ]


def test_audit_to_dict_contains_all_required_keys():
    audit = CodexLoopAudit(
        run_id="run-1",
        mode=LoopMode.DRY_RUN,
        task_file="tasks/inbox/task.md",
        branch="codex/dry-run",
        final_status=LoopStatus.PASS,
        rounds_used=1,
        max_rounds=3,
        no_progress_rounds=0,
        changed_files=["research_lab/orchestration/example.py"],
        diff_line_count=10,
        tests_requested=["pytest tests/test_example.py -q"],
        tests_passed=True,
        reviewer_verdicts=[LoopStatus.PASS.value],
        protected_paths_touched=[],
        forbidden_commands_detected=[],
        commit_attempted=False,
        commit_created=False,
        push_attempted=False,
        push_completed=False,
        pr_attempted=False,
        pr_created=False,
        pr_url=None,
        merge_attempted=False,
        merge_blocked=True,
        deploy_attempted=False,
        hertzner_sync_attempted=False,
        hertzner_sync_completed=False,
        registry_append_attempted=False,
        dry_run_external_calls=True,
        final_human_action_required=True,
    )

    payload = audit.to_dict()

    assert set(AUDIT_REQUIRED_KEYS).issubset(payload.keys())
    assert payload["mode"] == "dry_run"
    assert payload["final_status"] == "PASS"


def test_result_types_expose_json_friendly_fields():
    review = ReviewVerdict(status=LoopStatus.REVISE, summary="needs one more pass", issues=["fix test"])
    validation = ValidationResult(success=True, tests_requested=["pytest -q"], tests_passed=["pytest -q"], failures=[])
    git_action = GitActionResult(
        commit_attempted=True,
        commit_created=False,
        push_attempted=True,
        push_completed=False,
        pr_attempted=True,
        pr_created=False,
        pr_url=None,
        merge_attempted=False,
        merge_blocked=True,
        branch="codex/test",
        planned_actions=["commit", "push", "pr"],
    )

    assert review.to_dict()["status"] == "REVISE"
    assert validation.to_dict()["success"] is True
    assert git_action.to_dict()["planned_actions"] == ["commit", "push", "pr"]
