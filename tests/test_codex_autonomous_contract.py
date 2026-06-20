from __future__ import annotations

from research_lab.orchestration.codex_autonomous_contract import (
    AUDIT_REQUIRED_KEYS,
    CodexBudgetConfig,
    CodexExecutionTier,
    CodexLoopAudit,
    CodexLoopConfig,
    CodexTierDecision,
    DEFAULT_ALLOWED_PATHS,
    DEFAULT_FORBIDDEN_COMMAND_FRAGMENTS,
    DEFAULT_PROTECTED_PATHS,
    GitActionResult,
    LoopMode,
    LoopStatus,
    ReviewerBudgetConfig,
    ReviewerModelTier,
    ReviewerRequest,
    ReviewerResponse,
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
        commit_sha=None,
        push_attempted=False,
        push_completed=False,
        pr_attempted=False,
        pr_created=False,
        pr_number=None,
        pr_url=None,
        pr_title=None,
        pr_base_branch=None,
        pr_head_branch=None,
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
        pr_number=None,
        pr_url=None,
        merge_attempted=False,
        merge_blocked=True,
        branch="codex/test",
        planned_actions=["commit", "push", "pr"],
    )

    assert review.to_dict()["status"] == "REVISE"
    assert validation.to_dict()["success"] is True
    assert git_action.to_dict()["planned_actions"] == ["commit", "push", "pr"]
    assert git_action.to_dict()["git_action_provider"] == "fake"


def test_codex_budget_defaults_and_tier_decision_shape():
    budget = CodexBudgetConfig()
    decision = CodexTierDecision(
        requested_tier=CodexExecutionTier.AUTO,
        selected_tier=CodexExecutionTier.STANDARD,
        codex_model="codex-default",
        codex_reasoning="medium",
        escalation_reason="",
        high_rounds_used=0,
        very_high_rounds_used=0,
        max_high_rounds=budget.max_high_rounds_per_run,
        max_very_high_rounds=budget.max_very_high_rounds_per_run,
        budget_blocked=False,
    )

    assert budget.default_tier is CodexExecutionTier.STANDARD
    assert budget.default_model == "codex-default"
    assert budget.high_model == "codex-high"
    assert budget.very_high_model == "codex-very-high"
    assert budget.max_codex_calls_per_run == 20
    assert budget.max_high_rounds_per_run == 6
    assert budget.max_very_high_rounds_per_run == 1
    assert budget.allow_very_high is False
    assert decision.to_dict()["requested_tier"] == "auto"
    assert decision.to_dict()["selected_tier"] == "standard"


def test_reviewer_budget_defaults_and_request_response_shapes():
    budget = ReviewerBudgetConfig()
    request = ReviewerRequest(
        run_id="run-1",
        round_number=1,
        mode=LoopMode.SUPER_AUTO,
        task_text="Review the Codex output safely.",
        changed_files=["research_lab/orchestration/codex_autonomous_loop.py"],
        diff_line_count=42,
        validation_summary={"success": True, "tests_requested": ["pytest -q"]},
        policy_summary={"status": "PASS"},
        codex_summary="Updated loop audit fields.",
        codex_executor_details={"returncode": 0},
        previous_reviewer_verdicts=["REVISE"],
    )
    response = ReviewerResponse(
        verdict=LoopStatus.PASS,
        confidence=0.92,
        reason="The change is coherent and validated.",
        required_changes=[],
        safety_notes=["No unsafe behavior recommended."],
        escalation_recommended=False,
        selected_model="gpt-reviewer-high",
        selected_tier=ReviewerModelTier.HIGH,
        budget_blocked=False,
        raw_response_redacted='{"verdict":"PASS"}',
    )

    assert budget.max_reviewer_calls_per_run == 20
    assert budget.max_very_high_calls_per_run == 1
    assert budget.default_model == "gpt-reviewer-high"
    assert budget.high_model == "gpt-reviewer-high"
    assert budget.very_high_model == "gpt-reviewer-very-high"
    assert budget.allow_very_high is False
    assert request.to_dict()["mode"] == "super_auto"
    assert response.to_dict()["verdict"] == "PASS"
    assert response.to_dict()["selected_tier"] == "high"
