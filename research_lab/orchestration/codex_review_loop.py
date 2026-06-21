from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from research_lab.orchestration.codex_autonomous_contract import (
    CodexRoundResult,
    LoopStatus,
    ReviewVerdict,
    ValidationResult,
    new_run_id,
)


class ReviewLoopFinalStatus(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class CodexReviewLoopConfig:
    max_attempts: int
    dry_run_external_calls: bool = True
    run_id: str | None = None


@dataclass
class ReviewerBundle:
    initial_task: str
    current_prompt: str
    attempt_number: int
    changed_files: list[str]
    validation_output: dict[str, Any]
    diff_summary: str
    protected_paths_touched: list[str] = field(default_factory=list)
    disallowed_paths_touched: list[str] = field(default_factory=list)
    prior_feedback: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewLoopAttempt:
    attempt_number: int
    prompt_used: str
    executor_result: CodexRoundResult
    validation_result: ValidationResult
    reviewer_bundle: ReviewerBundle
    reviewer_verdict: ReviewVerdict
    reviewer_feedback: str
    follow_up_prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_number": self.attempt_number,
            "prompt_used": self.prompt_used,
            "executor_result": self.executor_result.to_dict(),
            "validation_result": self.validation_result.to_dict(),
            "reviewer_bundle": self.reviewer_bundle.to_dict(),
            "reviewer_verdict": self.reviewer_verdict.to_dict(),
            "reviewer_feedback": self.reviewer_feedback,
            "follow_up_prompt": self.follow_up_prompt,
        }


@dataclass
class CodexReviewLoopAudit:
    run_id: str
    initial_task: str
    attempts: list[ReviewLoopAttempt]
    verdicts: list[str]
    changed_files_per_attempt: list[list[str]]
    validation_outputs: list[dict[str, Any]]
    reviewer_feedback: list[str]
    final_status: ReviewLoopFinalStatus
    git_action_attempted: bool
    live_external_actions_enabled: bool
    protected_paths_touched: list[str] = field(default_factory=list)
    disallowed_paths_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "initial_task": self.initial_task,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "verdicts": list(self.verdicts),
            "changed_files_per_attempt": [list(files) for files in self.changed_files_per_attempt],
            "validation_outputs": list(self.validation_outputs),
            "reviewer_feedback": list(self.reviewer_feedback),
            "final_status": self.final_status.value,
            "git_action_attempted": self.git_action_attempted,
            "live_external_actions_enabled": self.live_external_actions_enabled,
            "protected_paths_touched": list(self.protected_paths_touched),
            "disallowed_paths_touched": list(self.disallowed_paths_touched),
        }


class ReviewLoopExecutorInterface(Protocol):
    def execute(self, prompt: str, attempt_number: int) -> CodexRoundResult:
        ...


class ReviewLoopReviewerInterface(Protocol):
    def review(self, bundle: ReviewerBundle) -> ReviewVerdict:
        ...


class ReviewLoopValidationRunnerInterface(Protocol):
    def run_validation(self, prompt: str, executor_result: CodexRoundResult, attempt_number: int) -> ValidationResult:
        ...


@dataclass
class CodexReviewLoop:
    config: CodexReviewLoopConfig
    executor: ReviewLoopExecutorInterface
    reviewer: ReviewLoopReviewerInterface
    validation_runner: ReviewLoopValidationRunnerInterface

    def run(self, initial_task: str) -> CodexReviewLoopAudit:
        run_id = self.config.run_id or new_run_id()
        attempts: list[ReviewLoopAttempt] = []
        verdicts: list[str] = []
        changed_files_per_attempt: list[list[str]] = []
        validation_outputs: list[dict[str, Any]] = []
        reviewer_feedback_items: list[str] = []
        protected_paths: list[str] = []
        disallowed_paths: list[str] = []
        prompt = initial_task
        prior_feedback: list[str] = []
        final_status = ReviewLoopFinalStatus.NEEDS_REVIEW

        for attempt_number in range(1, self.config.max_attempts + 1):
            executor_result = self.executor.execute(prompt, attempt_number)
            validation_result = self.validation_runner.run_validation(prompt, executor_result, attempt_number)
            policy_summary = dict(executor_result.executor_details.get("policy_summary", {}) or {})
            protected_paths = list(policy_summary.get("protected_paths_touched", protected_paths))
            disallowed_paths = list(policy_summary.get("disallowed_paths_touched", disallowed_paths))
            reviewer_bundle = ReviewerBundle(
                initial_task=initial_task,
                current_prompt=prompt,
                attempt_number=attempt_number,
                changed_files=list(executor_result.changed_files),
                validation_output=validation_result.to_dict(),
                diff_summary=_build_diff_summary(executor_result),
                protected_paths_touched=list(protected_paths),
                disallowed_paths_touched=list(disallowed_paths),
                prior_feedback=list(prior_feedback),
            )
            reviewer_verdict = self.reviewer.review(reviewer_bundle)
            feedback = _reviewer_feedback_text(reviewer_verdict)
            follow_up_prompt: str | None = None

            if reviewer_verdict.status is LoopStatus.REVISE and attempt_number < self.config.max_attempts:
                follow_up_prompt = _build_follow_up_prompt(initial_task, reviewer_verdict)
                prompt = follow_up_prompt
                prior_feedback.append(reviewer_verdict.summary.strip() or feedback)
            elif reviewer_verdict.status is LoopStatus.PASS:
                final_status = ReviewLoopFinalStatus.PASS
            elif reviewer_verdict.status is LoopStatus.BLOCKED:
                final_status = ReviewLoopFinalStatus.BLOCKED
            elif reviewer_verdict.status is LoopStatus.REVISE:
                final_status = ReviewLoopFinalStatus.NEEDS_REVIEW
                follow_up_prompt = _build_follow_up_prompt(initial_task, reviewer_verdict)

            attempt = ReviewLoopAttempt(
                attempt_number=attempt_number,
                prompt_used=reviewer_bundle.current_prompt,
                executor_result=executor_result,
                validation_result=validation_result,
                reviewer_bundle=reviewer_bundle,
                reviewer_verdict=reviewer_verdict,
                reviewer_feedback=feedback,
                follow_up_prompt=follow_up_prompt,
            )
            attempts.append(attempt)
            verdicts.append(reviewer_verdict.status.value)
            changed_files_per_attempt.append(list(executor_result.changed_files))
            validation_outputs.append(validation_result.to_dict())
            reviewer_feedback_items.append(feedback)

            if final_status in {ReviewLoopFinalStatus.PASS, ReviewLoopFinalStatus.BLOCKED}:
                break

        if verdicts and verdicts[-1] == LoopStatus.REVISE.value and final_status is not ReviewLoopFinalStatus.PASS:
            final_status = ReviewLoopFinalStatus.NEEDS_REVIEW

        return CodexReviewLoopAudit(
            run_id=run_id,
            initial_task=initial_task,
            attempts=attempts,
            verdicts=verdicts,
            changed_files_per_attempt=changed_files_per_attempt,
            validation_outputs=validation_outputs,
            reviewer_feedback=reviewer_feedback_items,
            final_status=final_status,
            git_action_attempted=False,
            live_external_actions_enabled=not self.config.dry_run_external_calls,
            protected_paths_touched=list(protected_paths),
            disallowed_paths_touched=list(disallowed_paths),
        )


class FakeReviewLoopExecutor:
    def __init__(self, results: list[CodexRoundResult] | None = None) -> None:
        self._results = list(results or [CodexRoundResult(changed_files=[], diff_line_count=0)])
        self.prompts: list[str] = []

    def execute(self, prompt: str, attempt_number: int) -> CodexRoundResult:
        self.prompts.append(prompt)
        index = min(attempt_number - 1, len(self._results) - 1)
        return self._results[index]


class FakeReviewLoopReviewer:
    def __init__(self, verdicts: list[ReviewVerdict] | None = None) -> None:
        self._verdicts = list(verdicts or [ReviewVerdict(status=LoopStatus.PASS)])
        self.bundles: list[ReviewerBundle] = []

    def review(self, bundle: ReviewerBundle) -> ReviewVerdict:
        self.bundles.append(bundle)
        index = min(bundle.attempt_number - 1, len(self._verdicts) - 1)
        return self._verdicts[index]


class FakeReviewLoopValidationRunner:
    def __init__(self, results: list[ValidationResult] | None = None) -> None:
        self._results = list(results or [ValidationResult(success=True)])
        self.prompts: list[str] = []

    def run_validation(self, prompt: str, executor_result: CodexRoundResult, attempt_number: int) -> ValidationResult:
        self.prompts.append(prompt)
        index = min(attempt_number - 1, len(self._results) - 1)
        return self._results[index]


def _build_diff_summary(result: CodexRoundResult) -> str:
    if result.summary:
        return result.summary
    if result.patch_digest:
        return result.patch_digest
    return f"Changed {len(result.changed_files)} files with {result.diff_line_count} diff lines."


def _reviewer_feedback_text(verdict: ReviewVerdict) -> str:
    feedback_parts = [verdict.summary.strip()] if verdict.summary.strip() else []
    feedback_parts.extend(issue.strip() for issue in verdict.issues if issue.strip())
    return "\n".join(feedback_parts).strip() or verdict.status.value


def _build_follow_up_prompt(initial_task: str, verdict: ReviewVerdict) -> str:
    feedback = _reviewer_feedback_text(verdict)
    return (
        f"{initial_task}\n\n"
        "Reviewer requested another attempt. Address the following feedback before finishing:\n"
        f"{feedback}"
    )
