from __future__ import annotations

from dataclasses import dataclass

from research_lab.orchestration.codex_autonomous_contract import (
    CodexExecutorInterface,
    CodexLoopAudit,
    CodexLoopConfig,
    CodexRoundInput,
    CodexRoundResult,
    GitActionInterface,
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
        git_result = GitActionResult(branch=branch, merge_blocked=True)

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

            policy = evaluate_round_policy(
                self.config,
                changed_files=round_result.changed_files,
                diff_line_count=round_result.diff_line_count,
                proposed_commands=round_result.proposed_commands,
                branch=branch,
                human_merge_confirmed=False,
            )
            protected_paths_touched = policy.protected_paths_touched
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
                )

            review = self.reviewer.review(round_input, round_result)
            reviewer_verdicts.append(review.status.value)

            validation = self.validation_runner.run_validation(round_input, round_result)
            tests_requested = list(validation.tests_requested)
            tests_passed = validation.success

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
                    git_result = self.git_action.plan_after_pass(self.config.mode, branch)
                break

            if review.status is LoopStatus.UNSAFE:
                self.final_status = LoopStatus.UNSAFE
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

    def review(self, round_input: CodexRoundInput, round_result: CodexRoundResult) -> ReviewVerdict:
        index = min(round_input.round_number - 1, len(self._verdicts) - 1)
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

    def plan_after_pass(self, mode: LoopMode, branch: str) -> GitActionResult:
        self.calls.append("plan")
        return GitActionResult(
            commit_attempted=True,
            commit_created=False,
            push_attempted=mode in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO},
            push_completed=False,
            pr_attempted=mode in {LoopMode.AUTO_PR, LoopMode.SUPER_AUTO},
            pr_created=False,
            pr_url=None,
            merge_attempted=False,
            merge_blocked=True,
            branch=branch,
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
        push_attempted=git_result.push_attempted,
        push_completed=git_result.push_completed,
        pr_attempted=git_result.pr_attempted,
        pr_created=git_result.pr_created,
        pr_url=git_result.pr_url,
        merge_attempted=git_result.merge_attempted,
        merge_blocked=git_result.merge_blocked,
        deploy_attempted="deploy" in lowered_commands,
        hertzner_sync_attempted="sync_with_preflight" in lowered_commands or "hetzner" in lowered_commands,
        hertzner_sync_completed=False,
        registry_append_attempted="registry append" in lowered_commands,
        dry_run_external_calls=config.dry_run_external_calls,
        final_human_action_required=True,
    )
