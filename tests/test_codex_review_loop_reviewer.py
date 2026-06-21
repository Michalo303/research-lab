from __future__ import annotations

import json

import pytest

from research_lab.orchestration.codex_review_loop import ReviewerBundle
from research_lab.orchestration.codex_review_loop_reviewer import (
    LiveReviewerAdapterStub,
    ReplayReviewLoopReviewer,
    ReviewerDecision,
    ReviewerDecisionError,
    ReviewerDecisionVerdict,
    parse_reviewer_decision,
)


def _bundle() -> ReviewerBundle:
    return ReviewerBundle(
        initial_task="Add reviewer adapter contract.",
        current_prompt="Add reviewer adapter contract.",
        attempt_number=1,
        changed_files=["research_lab/orchestration/codex_review_loop.py"],
        validation_output={"success": True},
        diff_summary="Changed 1 file.",
    )


def test_parse_pass_decision():
    decision = parse_reviewer_decision(
        json.dumps(
            {
                "verdict": "PASS",
                "reason": "Validation passed.",
                "next_codex_instruction": None,
                "risk_flags": [],
                "allowed_to_continue": True,
            }
        )
    )

    assert decision == ReviewerDecision(
        verdict=ReviewerDecisionVerdict.PASS,
        reason="Validation passed.",
        next_codex_instruction=None,
        risk_flags=[],
        allowed_to_continue=True,
    )


def test_parse_retry_decision_requires_instruction():
    decision = parse_reviewer_decision(
        {
            "verdict": "RETRY",
            "reason": "Add a regression test.",
            "next_codex_instruction": "Add a regression test for malformed reviewer JSON.",
            "risk_flags": ["missing-regression-test"],
            "allowed_to_continue": True,
        }
    )

    assert decision.verdict is ReviewerDecisionVerdict.RETRY
    assert decision.next_codex_instruction == "Add a regression test for malformed reviewer JSON."


def test_parse_abort_decision():
    decision = parse_reviewer_decision(
        {
            "verdict": "ABORT",
            "reason": "Protected path touched.",
            "next_codex_instruction": None,
            "risk_flags": ["protected-path"],
            "allowed_to_continue": False,
        }
    )

    assert decision.verdict is ReviewerDecisionVerdict.ABORT
    assert decision.allowed_to_continue is False


def test_reject_unknown_verdict():
    with pytest.raises(ReviewerDecisionError, match="Unknown reviewer verdict"):
        parse_reviewer_decision(
            {
                "verdict": "MAYBE",
                "reason": "No decision.",
                "next_codex_instruction": None,
                "risk_flags": [],
                "allowed_to_continue": False,
            }
        )


def test_reject_missing_required_fields():
    with pytest.raises(ReviewerDecisionError, match="Missing required reviewer fields"):
        parse_reviewer_decision({"verdict": "PASS"})


def test_reject_malformed_json():
    with pytest.raises(ReviewerDecisionError, match="Malformed reviewer decision JSON"):
        parse_reviewer_decision("{not json}")


def test_replay_reviewer_is_deterministic_and_non_live():
    reviewer = ReplayReviewLoopReviewer.from_raw_decisions(
        [
            {
                "verdict": "PASS",
                "reason": "Looks good.",
                "next_codex_instruction": None,
                "risk_flags": [],
                "allowed_to_continue": True,
            }
        ]
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "PASS"
    assert reviewer.review_call_count == 1
    assert reviewer.live_provider_calls == 0


def test_live_reviewer_stub_refuses_execution():
    reviewer = LiveReviewerAdapterStub()

    with pytest.raises(RuntimeError, match="disabled"):
        reviewer.review(_bundle())
