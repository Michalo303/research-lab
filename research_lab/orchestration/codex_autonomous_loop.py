from __future__ import annotations

from dataclasses import dataclass

from research_lab.orchestration.codex_autonomous_contract import (
    CodexExecutorInterface,
    CodexLoopAudit,
    CodexLoopConfig,
    CodexRoundInput,
    CodexRoundResult,
    GitActionInterface,
    GitActionRequest,
    GitActionResult,
    LoopMode,
    LoopStatus,
    ReviewVerdict,
    ReviewerInterface,
    ValidationResult,
    ValidationRunnerInterface,
    new_run_id,
)
from research_lab.orchestration.codex_autonomous_policy import evaluate_round_policy


@dataclass
class CodexAutonomousLoop:
    config: CodexLoopConfig
    codex_executor: CodexExecutorInterface
    reviewer: ReviewerInterface
    validation_runner: ValidationRunnerInterface
    git_action: GitActionInterface
    run_id: str | None = None
    branch: str | None = None
    final_status: LoopStatus = LoopStatus.BLOCKED

    def run(self, task_file: str) -> CodexLoopAudit:
        run_id = self.run_id or new_run_id()
        branch = self.branch or f"codex/{self.config.mode.value}-{run_id.split('-')[-1]}"
        reviewer_verdicts: list[str] = []
        changed_files: list[str] = []
        diff_line_count = 0
        tests_requested: list[str] = []
        tests_passed = True
        no_progress_rounds = 0
        protected_paths_touched: list[str] = []
        forbidden_commands_detected: list[str] = []
        disallowed_paths_touched: list[str] = []
        git_result = GitActionResult(branch=branch, merge_blocked=True)
        reviewer_selected_model: str | None = None
        reviewer_selected_tier: str | None = None
        reviewer_call_count = 0
        reviewer_budget_blocked = False
        reviewer_redaction_notes: list[str] = []
        reviewer_provider_metadata: dict[str, object] = {}
        reviewer_preflight: dict[str, object] = dict(getattr(self.reviewer, "reviewer_preflight", {}) or {})

        for round_number in range(1, self.config.max_rounds + 1):
            round_input = CodexRoundInput(
                run_id=run_id,
                round_number=round_number,
                task_file=task_file,
                mode=self.config.mode,
                branch=branch,
                prior_reviewer_verdicts=list(reviewer_verdicts),
            )
            round_result = self.codex_executor.execute(round_input)
            changed_files = list(round_result.changed_files)
            diff_line_count = round_result.diff_line_count
            if round_result.executor_failed:
                self.final_status = LoopStatus.BLOCKED
                return _build_audit(
                    config=self.config,
                    run_id=run_id,
                    task_file=task_file,
                    branch=branch,
                    final_status=self.final_status,
                    rounds_used=round_number,
                    no_progress_rounds=no_progress_rounds,
                    changed_files=changed_files,
                    diff_line_count=diff_line_count,
                    tests_requested=tests_requested,
                    tests_passed=False,
                    reviewer_verdicts=reviewer_verdicts,
                    protected_paths_touched=protected_paths_touched,
                    forbidden_commands_detected=forbidden_commands_detected,
                    git_result=git_result,
                    reviewer_selected_model=reviewer_selected_model,
                    reviewer_selected_tier=reviewer_selected_tier,
                    reviewer_call_count=reviewer_call_count,
                    reviewer_budget_blocked=reviewer_budget_blocked,
                    reviewer_redaction_notes=reviewer_redaction_notes,
                    reviewer_provider_metadata=reviewer_provider_metadata,
                    reviewer_preflight=reviewer_preflight,
                )

            policy = evaluate_round_policy(
                self.config,
                changed_files=round_result.changed_files,
                diff_line_count=round_result.diff_line_count,
                proposed_commands=round_result.proposed_commands,
                branch=branch,
                human_merge_confirmed=False,
            )
            protected_paths_touched = policy.protected_paths_touched
            disallowed_paths_touched = policy.disallowed_paths_touched
            forbidden_commands_detected = policy.forbidden_commands_detected
            if policy.status == LoopStatus.UNSAFE.value:
                self.final_status = LoopStatus.UNSAFE
                return _build_audit(
                    config=self.config,
                    run_id=run_id,
                    task_file=task_file,
                    branch=branch,
                    final_status=self.final_status,
                    rounds_used=round_number,
                    no_progress_rounds=no_progress_rounds,
                    changed_files=changed_files,
                    diff_line_count=diff_line_count,
                    tests_requested=tests_requested,
                    tests_passed=False,
                    reviewer_verdicts=reviewer_verdicts,
                    protected_paths_touched=protected_paths_touched,
                    forbidden_commands_detected=forbidden_commands_detected,
                    git_result=git_result,
                    reviewer_selected_model=reviewer_selected_model,
                    reviewer_selected_tier=reviewer_selected_tier,
                    reviewer_call_count=reviewer_call_count,
                    reviewer_budget_blocked=reviewer_budget_blocked,
                    reviewer_redaction_notes=reviewer_redaction_notes,
                    reviewer_provider_metadata=reviewer_provider_metadata,
                    reviewer_preflight=reviewer_preflight,
                )

            validation = self.validation_runner.run_validation(round_input, round_result)
            tests_requested = list(validation.tests_requested)
            tests_passed = validation.success
            policy_summary = {
                "status": policy.status,
                "disallowed_paths_touched": list(policy.disallowed_paths_touched),
                "protected_paths_touched": list(policy.protected_paths_touched),
                "forbidden_commands_detected": list(policy.forbidden_commands_detected),
                "changed_file_limit_exceeded": policy.changed_file_limit_exceeded,
                "diff_limit_exceeded": policy.diff_limit_exceeded,
            }
            review = self.reviewer.review(
                round_input,
                round_result,
                validation_result=validation,
                policy_summary=policy_summary,
            )
            reviewer_verdicts.append(review.status.value)
            reviewer_response = getattr(self.reviewer, "last_response", None)
            if reviewer_response is not None:
                reviewer_selected_model = reviewer_response.selected_model
                reviewer_selected_tier = reviewer_response.selected_tier.value
                reviewer_budget_blocked = reviewer_response.budget_blocked
            reviewer_call_count = getattr(self.reviewer, "call_count", reviewer_call_count)
            reviewer_redaction_notes = list(getattr(self.reviewer, "last_redaction_notes", reviewer_redaction_notes))
            reviewer_provider_metadata = dict(getattr(self.reviewer, "last_provider_metadata", reviewer_provider_metadata) or {})
            reviewer_preflight = dict(getattr(self.reviewer, "reviewer_preflight", reviewer_preflight) or {})

            if round_result.meaningful_progress:
                no_progress_rounds = 0
            else:
                no_progress_rounds += 1

            if no_progress_rounds >= self.config.no_progress_round_limit:
                self.final_status = LoopStatus.BLOCKED
                break

            if review.status is LoopStatus.PASS and validation.success:
                self.final_status = LoopStatus.PASS
                if self.config.mode in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO}:
                    git_result = self.git_action.plan_after_pass(
                        GitActionRequest(
                            mode=self.config.mode,
                            branch=branch,
                            changed_files=list(changed_files),
                            diff_line_count=diff_line_count,
                            reviewer_status=review.status,
                            validation_success=validation.success,
                            policy_status=policy.status,
                            protected_paths_touched=list(protected_paths_touched),
                            disallowed_paths_touched=list(disallowed_paths_touched),
                            max_changed_files=self.config.max_changed_files,
                            max_diff_lines=self.config.max_diff_lines,
                        )
                    )
                break

            if review.status is LoopStatus.UNSAFE:
                self.final_status = LoopStatus.UNSAFE
                break

            if review.status is LoopStatus.BLOCKED:
                self.final_status = LoopStatus.BLOCKED
                break

            if review.status is LoopStatus.REVISE:
                self.final_status = LoopStatus.REVISE
                continue

        if self.final_status is LoopStatus.REVISE:
            self.final_status = LoopStatus.BLOCKED

        return _build_audit(
            config=self.config,
            run_id=run_id,
            task_file=task_file,
            branch=branch,
            final_status=self.final_status,
            rounds_used=len(reviewer_verdicts) if reviewer_verdicts else 1,
            no_progress_rounds=no_progress_rounds,
            changed_files=changed_files,
            diff_line_count=diff_line_count,
            tests_requested=tests_requested,
            tests_passed=tests_passed,
            reviewer_verdicts=reviewer_verdicts,
            protected_paths_touched=protected_paths_touched,
            forbidden_commands_detected=forbidden_commands_detected,
            git_result=git_result,
            reviewer_selected_model=reviewer_selected_model,
            reviewer_selected_tier=reviewer_selected_tier,
            reviewer_call_count=reviewer_call_count,
            reviewer_budget_blocked=reviewer_budget_blocked,
            reviewer_redaction_notes=reviewer_redaction_notes,
            reviewer_provider_metadata=reviewer_provider_metadata,
            reviewer_preflight=reviewer_preflight,
        )


class FakeCodexExecutor:
    def __init__(self, rounds: list[CodexRoundResult] | None = None) -> None:
        self._rounds = list(rounds or [CodexRoundResult(changed_files=[], diff_line_count=0)])

    def execute(self, round_input: CodexRoundInput) -> CodexRoundResult:
        index = min(round_input.round_number - 1, len(self._rounds) - 1)
        return self._rounds[index]


class FakeReviewer:
    def __init__(self, verdicts: list[ReviewVerdict] | None = None) -> None:
        self._verdicts = list(verdicts or [ReviewVerdict(status=LoopStatus.PASS)])
        self.call_count = 0
        self.last_response = None
        self.last_redaction_notes: list[str] = []
        self.last_provider_metadata: dict[str, object] = {}
        self.reviewer_preflight: dict[str, object] = {}

    def review(
        self,
        round_input: CodexRoundInput,
        round_result: CodexRoundResult,
        *,
        validation_result: ValidationResult | None = None,
        policy_summary: dict[str, object] | None = None,
    ) -> ReviewVerdict:
        index = min(round_input.round_number - 1, len(self._verdicts) - 1)
        self.call_count += 1
        return self._verdicts[index]


class FakeValidationRunner:
    def __init__(self, results: list[ValidationResult] | None = None) -> None:
        self._results = list(results or [ValidationResult(success=True)])

    def run_validation(self, round_input: CodexRoundInput, round_result: CodexRoundResult) -> ValidationResult:
        index = min(round_input.round_number - 1, len(self._results) - 1)
        result = self._results[index]
        if result.tests_requested:
            return result
        default_test = "python -m pytest tests/test_codex_autonomous_contract.py tests/test_codex_autonomous_policy.py tests/test_codex_autonomous_loop.py -q"
        return ValidationResult(
            success=result.success,
            tests_requested=[default_test],
            tests_passed=[default_test] if result.success else [],
            failures=list(result.failures),
        )


class FakeGitAction:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def plan_after_pass(self, request: GitActionRequest) -> GitActionResult:
        self.calls.append("plan")
        return GitActionResult(
            git_action_provider="fake",
            git_action_live_enabled=False,
            commit_attempted=True,
            commit_created=False,
            push_attempted=request.mode in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO},
            push_completed=False,
            pr_attempted=request.mode in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO},
            pr_created=False,
            pr_url=None,
            merge_attempted=False,
            merge_blocked=True,
            branch=request.branch,
            planned_actions=["commit", "push", "pr"],
        )


def _build_audit(
    *,
    config: CodexLoopConfig,
    run_id: str,
    task_file: str,
    branch: str,
    final_status: LoopStatus,
    rounds_used: int,
    no_progress_rounds: int,
    changed_files: list[str],
    diff_line_count: int,
    tests_requested: list[str],
    tests_passed: bool,
    reviewer_verdicts: list[str],
    protected_paths_touched: list[str],
    forbidden_commands_detected: list[str],
    git_result: GitActionResult,
    reviewer_selected_model: str | None,
    reviewer_selected_tier: str | None,
    reviewer_call_count: int,
    reviewer_budget_blocked: bool,
    reviewer_redaction_notes: list[str],
    reviewer_provider_metadata: dict[str, object],
    reviewer_preflight: dict[str, object],
) -> CodexLoopAudit:
    lowered_commands = " ".join(forbidden_commands_detected).lower()
    return CodexLoopAudit(
        run_id=run_id,
        mode=config.mode,
        task_file=task_file,
        branch=branch,
        final_status=final_status,
        rounds_used=rounds_used,
        max_rounds=config.max_rounds,
        no_progress_rounds=no_progress_rounds,
        changed_files=changed_files,
        diff_line_count=diff_line_count,
        tests_requested=tests_requested,
        tests_passed=tests_passed,
        reviewer_verdicts=reviewer_verdicts,
        protected_paths_touched=protected_paths_touched,
        forbidden_commands_detected=forbidden_commands_detected,
        commit_attempted=git_result.commit_attempted,
        commit_created=git_result.commit_created,
        commit_sha=git_result.commit_sha,
        push_attempted=git_result.push_attempted,
        push_completed=git_result.push_completed,
        pr_attempted=git_result.pr_attempted,
        pr_created=git_result.pr_created,
        pr_number=git_result.pr_number,
        pr_url=git_result.pr_url,
        pr_title=git_result.pr_title,
        pr_base_branch=git_result.pr_base_branch,
        pr_head_branch=git_result.pr_head_branch,
        merge_attempted=git_result.merge_attempted,
        merge_blocked=git_result.merge_blocked,
        deploy_attempted="deploy" in lowered_commands,
        hertzner_sync_attempted="sync_with_preflight" in lowered_commands or "hetzner" in lowered_commands,
        hertzner_sync_completed=False,
        registry_append_attempted="registry append" in lowered_commands,
        dry_run_external_calls=config.dry_run_external_calls,
        final_human_action_required=True,
        git_action_provider=git_result.git_action_provider,
        git_action_live_enabled=git_result.git_action_live_enabled,
        git_action_attempted=git_result.git_action_attempted,
        git_action_blocked_reason=git_result.git_action_blocked_reason,
        staged_files=list(git_result.staged_files),
        reviewer_selected_model=reviewer_selected_model,
        reviewer_selected_tier=reviewer_selected_tier,
        reviewer_call_count=reviewer_call_count,
        reviewer_budget_blocked=reviewer_budget_blocked,
        reviewer_redaction_notes=reviewer_redaction_notes,
        reviewer_provider_metadata=reviewer_provider_metadata,
        reviewer_preflight=reviewer_preflight,
    )
