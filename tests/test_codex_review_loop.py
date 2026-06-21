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


def test_pass_on_first_attempt_stops_the_loop():
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
    loop = CodexReviewLoop(
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
