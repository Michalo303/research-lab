from __future__ import annotations

from research_lab.orchestration.codex_autonomous_contract import (
    CodexRoundResult,
    LoopStatus,
    ReviewVerdict,
    ValidationResult,
)
from research_lab.orchestration.codex_review_loop import (
    CodexReviewLoop,
    CodexReviewLoopConfig,
    FakeReviewLoopExecutor,
    FakeReviewLoopReviewer,
    FakeReviewLoopValidationRunner,
    ReviewLoopFinalStatus,
    TrackedTreeProbeResult,
)


def _round(
    *,
    changed_files: list[str] | None = None,
    diff_line_count: int = 10,
    summary: str = "Updated files.",
    executor_details: dict | None = None,
) -> CodexRoundResult:
    return CodexRoundResult(
        changed_files=changed_files or ["research_lab/orchestration/codex_review_loop.py"],
        diff_line_count=diff_line_count,
        proposed_commands=[],
        summary=summary,
        patch_digest=summary,
        meaningful_progress=True,
        executor_details=executor_details or {},
    )


def _validation(
    *,
    success: bool = True,
    tests_requested: list[str] | None = None,
    tests_passed: list[str] | None = None,
    failures: list[str] | None = None,
) -> ValidationResult:
    return ValidationResult(
        success=success,
        tests_requested=tests_requested or ["python -m pytest tests/test_codex_review_loop.py -q"],
        tests_passed=tests_passed or (["python -m pytest tests/test_codex_review_loop.py -q"] if success else []),
        failures=failures or [],
    )


class CountingReviewer(FakeReviewLoopReviewer):
    @property
    def review_call_count(self) -> int:
        return len(self.bundles)


class CountingValidationRunner(FakeReviewLoopValidationRunner):
    @property
    def validation_call_count(self) -> int:
        return len(self.prompts)


def _clean_tracked_tree_checker() -> TrackedTreeProbeResult:
    return TrackedTreeProbeResult(dirty=False, status="")


def _make_loop(
    *,
    config: CodexReviewLoopConfig,
    executor,
    reviewer,
    validation_runner,
    tracked_tree_checker=None,
) -> CodexReviewLoop:
    return CodexReviewLoop(
        config=config,
        executor=executor,
        reviewer=reviewer,
        validation_runner=validation_runner,
        tracked_tree_checker=tracked_tree_checker or _clean_tracked_tree_checker,
    )


def test_pass_on_first_attempt_stops_the_loop():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=3, dry_run_external_calls=True, run_id="review-loop-1"),
        executor=FakeReviewLoopExecutor([_round(summary="First attempt complete.")]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Looks good.")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation()]),
    )

    audit = loop.run("Implement the deterministic review loop.")

    assert audit.final_status is ReviewLoopFinalStatus.PASS
    assert len(audit.attempts) == 1
    assert audit.verdicts == ["PASS"]


def test_revise_then_pass_runs_exactly_two_attempts():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=4, dry_run_external_calls=True, run_id="review-loop-2"),
        executor=FakeReviewLoopExecutor([_round(summary="Attempt one."), _round(summary="Attempt two.")]),
        reviewer=FakeReviewLoopReviewer(
            [
                ReviewVerdict(status=LoopStatus.REVISE, summary="Add more validation.", issues=["Add more validation."]),
                ReviewVerdict(status=LoopStatus.PASS, summary="Approved."),
            ]
        ),
        validation_runner=FakeReviewLoopValidationRunner([_validation(), _validation()]),
    )

    audit = loop.run("Tighten the review loop.")

    assert audit.final_status is ReviewLoopFinalStatus.PASS
    assert len(audit.attempts) == 2
    assert audit.verdicts == ["REVISE", "PASS"]


def test_revise_until_max_attempts_stops_with_needs_review():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=2, dry_run_external_calls=True, run_id="review-loop-3"),
        executor=FakeReviewLoopExecutor([_round(summary="Attempt one."), _round(summary="Attempt two.")]),
        reviewer=FakeReviewLoopReviewer(
            [
                ReviewVerdict(status=LoopStatus.REVISE, summary="Needs more work.", issues=["Issue one"]),
                ReviewVerdict(status=LoopStatus.REVISE, summary="Still needs work.", issues=["Issue two"]),
            ]
        ),
        validation_runner=FakeReviewLoopValidationRunner([_validation(), _validation()]),
    )

    audit = loop.run("Keep revising until max attempts.")

    assert audit.final_status is ReviewLoopFinalStatus.NEEDS_REVIEW
    assert len(audit.attempts) == 2
    assert audit.verdicts == ["REVISE", "REVISE"]


def test_blocked_stops_immediately():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=3, dry_run_external_calls=True, run_id="review-loop-4"),
        executor=FakeReviewLoopExecutor([_round(summary="Blocked attempt.")]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.BLOCKED, summary="Cannot proceed.")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation(success=False, failures=["validation blocked"])]),
    )

    audit = loop.run("Stop immediately when blocked.")

    assert audit.final_status is ReviewLoopFinalStatus.BLOCKED
    assert len(audit.attempts) == 1
    assert audit.verdicts == ["BLOCKED"]


def test_reviewer_bundle_contains_expected_fields_and_prior_feedback():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=2, dry_run_external_calls=True, run_id="review-loop-5"),
        executor=FakeReviewLoopExecutor([_round(summary="Attempt one diff."), _round(summary="Attempt two diff.")]),
        reviewer=FakeReviewLoopReviewer(
            [
                ReviewVerdict(status=LoopStatus.REVISE, summary="Please add a regression test.", issues=["regression test"]),
                ReviewVerdict(status=LoopStatus.PASS, summary="Done."),
            ]
        ),
        validation_runner=FakeReviewLoopValidationRunner([_validation(), _validation()]),
    )

    audit = loop.run("Bundle reviewer context.")

    first_bundle = audit.attempts[0].reviewer_bundle
    second_bundle = audit.attempts[1].reviewer_bundle
    assert first_bundle.initial_task == "Bundle reviewer context."
    assert first_bundle.attempt_number == 1
    assert first_bundle.changed_files == ["research_lab/orchestration/codex_review_loop.py"]
    assert first_bundle.validation_output["success"] is True
    assert first_bundle.diff_summary == "Attempt one diff."
    assert first_bundle.prior_feedback == []
    assert second_bundle.prior_feedback == ["Please add a regression test."]


def test_follow_up_prompt_is_derived_from_reviewer_feedback():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=2, dry_run_external_calls=True, run_id="review-loop-6"),
        executor=FakeReviewLoopExecutor([_round(summary="Attempt one."), _round(summary="Attempt two.")]),
        reviewer=FakeReviewLoopReviewer(
            [
                ReviewVerdict(status=LoopStatus.REVISE, summary="Tighten the tests.", issues=["Add a regression test."]),
                ReviewVerdict(status=LoopStatus.PASS, summary="Looks good."),
            ]
        ),
        validation_runner=FakeReviewLoopValidationRunner([_validation(), _validation()]),
    )

    audit = loop.run("Improve the implementation.")

    assert "Improve the implementation." in audit.attempts[0].follow_up_prompt
    assert "Add a regression test." in audit.attempts[0].follow_up_prompt


def test_audit_captures_all_attempts_and_final_status():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=2, dry_run_external_calls=True, run_id="review-loop-7"),
        executor=FakeReviewLoopExecutor([_round(summary="Attempt one."), _round(summary="Attempt two.")]),
        reviewer=FakeReviewLoopReviewer(
            [
                ReviewVerdict(status=LoopStatus.REVISE, summary="Revise once.", issues=["Issue A"]),
                ReviewVerdict(status=LoopStatus.PASS, summary="Pass."),
            ]
        ),
        validation_runner=FakeReviewLoopValidationRunner([_validation(), _validation()]),
    )

    payload = loop.run("Serialize the audit.").to_dict()

    assert payload["initial_task"] == "Serialize the audit."
    assert payload["final_status"] == "PASS"
    assert len(payload["attempts"]) == 2
    assert payload["git_action_attempted"] is False
    assert payload["live_external_actions_enabled"] is False


def test_default_controller_does_not_attempt_live_actions():
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=1, dry_run_external_calls=True, run_id="review-loop-8"),
        executor=FakeReviewLoopExecutor([_round()]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Safe")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation()]),
    )

    audit = loop.run("Stay dry run.")

    assert audit.git_action_attempted is False
    assert audit.live_external_actions_enabled is False


def test_protected_and_disallowed_path_findings_propagate_to_bundle_and_audit():
    round_result = _round(
        changed_files=["reports/daily/2026-06-21.md", "README.md"],
        summary="Touched protected and disallowed files.",
        executor_details={
            "policy_summary": {
                "protected_paths_touched": ["reports/daily/2026-06-21.md"],
                "disallowed_paths_touched": ["README.md"],
            }
        },
    )
    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=1, dry_run_external_calls=True, run_id="review-loop-9"),
        executor=FakeReviewLoopExecutor([round_result]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.BLOCKED, summary="Unsafe change set.")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation(success=False, failures=["unsafe paths"])]),
    )

    audit = loop.run("Propagate policy findings.")

    assert audit.protected_paths_touched == ["reports/daily/2026-06-21.md"]
    assert audit.disallowed_paths_touched == ["README.md"]
    assert audit.attempts[0].reviewer_bundle.protected_paths_touched == ["reports/daily/2026-06-21.md"]
    assert audit.attempts[0].reviewer_bundle.disallowed_paths_touched == ["README.md"]


def test_pre_run_dirty_tracked_tree_aborts_before_executor_validator_and_reviewer():
    executor = FakeReviewLoopExecutor([_round(summary="Should not run.")])
    reviewer = CountingReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Should not review.")])
    validation_runner = CountingValidationRunner([_validation()])

    def tracked_tree_checker() -> TrackedTreeProbeResult:
        return TrackedTreeProbeResult(dirty=True, status=" M research_lab/orchestration/codex_review_loop.py")

    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=2, dry_run_external_calls=True, run_id="review-loop-dirty-pre"),
        executor=executor,
        reviewer=reviewer,
        validation_runner=validation_runner,
        tracked_tree_checker=tracked_tree_checker,
    )

    audit = loop.run("Abort when tracked tree is dirty.")

    assert audit.final_status is ReviewLoopFinalStatus.BLOCKED
    assert audit.pre_run_tracked_dirty is True
    assert audit.pre_run_tracked_status == " M research_lab/orchestration/codex_review_loop.py"
    assert audit.final_tracked_dirty is True
    assert audit.final_tracked_status == " M research_lab/orchestration/codex_review_loop.py"
    assert audit.tracked_tree_failure_reason is None
    assert audit.attempts == []
    assert executor.prompts == []
    assert validation_runner.validation_call_count == 0
    assert reviewer.review_call_count == 0


def test_clean_tracked_tree_allows_review_loop_to_run():
    checker_calls = 0

    def tracked_tree_checker() -> TrackedTreeProbeResult:
        nonlocal checker_calls
        checker_calls += 1
        return TrackedTreeProbeResult(dirty=False, status="")

    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=1, dry_run_external_calls=True, run_id="review-loop-clean-pre"),
        executor=FakeReviewLoopExecutor([_round(summary="Executor ran.")]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Approved.")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation()]),
        tracked_tree_checker=tracked_tree_checker,
    )

    audit = loop.run("Proceed when tracked tree is clean.")

    assert audit.final_status is ReviewLoopFinalStatus.PASS
    assert audit.pre_run_tracked_dirty is False
    assert audit.pre_run_tracked_status == ""
    assert audit.final_tracked_dirty is False
    assert audit.final_tracked_status == ""
    assert checker_calls == 2
    assert len(audit.attempts) == 1


def test_post_attempt_tracked_dirty_state_is_recorded():
    checker_results = iter(
        [
            TrackedTreeProbeResult(dirty=False, status=""),
            TrackedTreeProbeResult(dirty=True, status=" M tests/test_codex_review_loop.py"),
        ]
    )

    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=1, dry_run_external_calls=True, run_id="review-loop-post-dirty"),
        executor=FakeReviewLoopExecutor([_round(summary="Executor ran once.")]),
        reviewer=FakeReviewLoopReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Approved.")]),
        validation_runner=FakeReviewLoopValidationRunner([_validation()]),
        tracked_tree_checker=lambda: next(checker_results),
    )

    audit = loop.run("Record post-attempt tracked tree state.")

    assert audit.pre_run_tracked_dirty is False
    assert audit.final_tracked_dirty is True
    assert audit.final_tracked_status == " M tests/test_codex_review_loop.py"
    assert audit.attempts[0].post_attempt_tracked_dirty is True
    assert audit.attempts[0].post_attempt_tracked_status == " M tests/test_codex_review_loop.py"


def test_git_status_probe_failure_aborts_safely():
    executor = FakeReviewLoopExecutor([_round(summary="Should not run.")])
    reviewer = CountingReviewer([ReviewVerdict(status=LoopStatus.PASS, summary="Should not review.")])
    validation_runner = CountingValidationRunner([_validation()])

    def tracked_tree_checker() -> TrackedTreeProbeResult:
        raise RuntimeError("git status failed: exit 128")

    loop = _make_loop(
        config=CodexReviewLoopConfig(max_attempts=1, dry_run_external_calls=True, run_id="review-loop-git-fail"),
        executor=executor,
        reviewer=reviewer,
        validation_runner=validation_runner,
        tracked_tree_checker=tracked_tree_checker,
    )

    audit = loop.run("Abort when git status probe fails.")

    assert audit.final_status is ReviewLoopFinalStatus.BLOCKED
    assert audit.pre_run_tracked_dirty is True
    assert audit.pre_run_tracked_status == ""
    assert audit.final_tracked_dirty is True
    assert audit.final_tracked_status == ""
    assert "git status failed" in audit.tracked_tree_failure_reason
    assert audit.attempts == []
    assert executor.prompts == []
    assert validation_runner.validation_call_count == 0
    assert reviewer.review_call_count == 0
