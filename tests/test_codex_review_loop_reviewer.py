from __future__ import annotations

import json

import pytest

from research_lab.orchestration.codex_review_loop import ReviewerBundle
from research_lab.orchestration.codex_review_loop_reviewer import (
    _build_provider_request_payload,
    LiveReviewerAdapterStub,
    LiveOpenAIReviewLoopReviewer,
    ReplayReviewLoopReviewer,
    ReviewLoopReviewerMode,
    ReviewerDecision,
    ReviewerDecisionError,
    ReviewerDecisionVerdict,
    validate_provider_call_gate,
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


class FakeProviderClient:
    def __init__(self, responses: list[str] | None = None, *, error: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[dict] = []

    def review_json(self, *, request_payload: dict[str, object], api_key: str, model_name: str) -> str:
        self.calls.append(
            {
                "request_payload": request_payload,
                "api_key": api_key,
                "model_name": model_name,
            }
        )
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("No fake provider response configured.")
        return self._responses.pop(0)


def test_parse_pass_decision_accepts_null_next_codex_instruction():
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


def test_parse_pass_decision_rejects_non_null_next_codex_instruction():
    with pytest.raises(ReviewerDecisionError, match="PASS decisions must not include next_codex_instruction"):
        parse_reviewer_decision(
            {
                "verdict": "PASS",
                "reason": "Validation passed.",
                "next_codex_instruction": "Make another change.",
                "risk_flags": [],
                "allowed_to_continue": True,
            }
        )


def test_parse_retry_decision_accepts_non_empty_next_codex_instruction():
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


def test_provider_request_payload_includes_conditional_parser_contract():
    payload = _build_provider_request_payload(_bundle())

    contract = payload["review_request"]["response_contract"]
    assert contract["allowed_verdicts"] == ["PASS", "RETRY", "ABORT"]
    assert contract["unsupported_verdict_aliases"] == {
        "CHANGES_REQUESTED": "RETRY",
        "BLOCKED": "ABORT",
    }
    assert contract["conditional_rules"] == {
        "PASS": {
            "allowed_to_continue": True,
            "next_codex_instruction": None,
            "follow_up_codex_work": "must_not_be_requested",
        },
        "RETRY": {
            "allowed_to_continue": True,
            "next_codex_instruction": "must_be_a_non_empty_actionable_instruction",
        },
        "ABORT": {
            "allowed_to_continue": False,
            "next_codex_instruction": None,
        },
    }


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


def test_live_openai_reviewer_missing_api_key_fails_closed_without_provider_call():
    provider_client = FakeProviderClient(
        responses=[
            json.dumps(
                {
                    "verdict": "PASS",
                    "reason": "Approved.",
                    "next_codex_instruction": None,
                    "risk_flags": [],
                    "allowed_to_continue": True,
                }
            )
        ]
    )
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="",
        max_reviewer_calls=2,
        provider_client=provider_client,
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "BLOCKED"
    assert provider_client.calls == []
    assert reviewer.reviewer_calls_used == 0
    assert reviewer.latest_provider_metadata["provider_call_attempted"] is False
    assert reviewer.latest_provider_metadata["provider_call_failed"] is True
    assert "OPENAI_API_KEY" in reviewer.latest_provider_metadata["failure_reason"]


def test_live_openai_reviewer_parses_valid_provider_json_through_reviewer_decision():
    provider_client = FakeProviderClient(
        responses=[
            json.dumps(
                {
                    "verdict": "PASS",
                    "reason": "Validation and diff summary look safe.",
                    "next_codex_instruction": None,
                    "risk_flags": [],
                    "allowed_to_continue": True,
                    "required_changes": [],
                    "confidence": "high",
                }
            )
        ]
    )
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="test-key",
        max_reviewer_calls=2,
        provider_client=provider_client,
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "PASS"
    assert reviewer.reviewer_calls_used == 1
    assert reviewer.latest_decision == ReviewerDecision(
        verdict=ReviewerDecisionVerdict.PASS,
        reason="Validation and diff summary look safe.",
        next_codex_instruction=None,
        risk_flags=[],
        allowed_to_continue=True,
    )
    assert reviewer.latest_provider_metadata["provider_call_attempted"] is True
    assert reviewer.latest_provider_metadata["provider_call_succeeded"] is True
    assert reviewer.latest_provider_metadata["provider_call_failed"] is False
    assert reviewer.latest_provider_metadata["parsed_reviewer_decision"]["verdict"] == "PASS"


def test_live_openai_reviewer_malformed_provider_json_fails_closed():
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="test-key",
        max_reviewer_calls=2,
        provider_client=FakeProviderClient(responses=["{not-json}"]),
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "BLOCKED"
    assert reviewer.reviewer_calls_used == 1
    assert reviewer.latest_provider_metadata["provider_call_attempted"] is True
    assert reviewer.latest_provider_metadata["provider_call_failed"] is True
    assert "Malformed reviewer decision JSON" in reviewer.latest_provider_metadata["parse_failure"]


def test_live_openai_reviewer_invalid_provider_fields_fail_closed():
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="test-key",
        max_reviewer_calls=2,
        provider_client=FakeProviderClient(responses=[json.dumps({"verdict": "PASS"})]),
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "BLOCKED"
    assert reviewer.reviewer_calls_used == 1
    assert reviewer.latest_provider_metadata["provider_call_failed"] is True
    assert "Missing required reviewer fields" in reviewer.latest_provider_metadata["parse_failure"]


def test_live_openai_reviewer_budget_exhaustion_blocks_second_call():
    provider_client = FakeProviderClient(
        responses=[
            json.dumps(
                {
                    "verdict": "PASS",
                    "reason": "Approved.",
                    "next_codex_instruction": None,
                    "risk_flags": [],
                    "allowed_to_continue": True,
                }
            )
        ]
    )
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="test-key",
        max_reviewer_calls=1,
        provider_client=provider_client,
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    first_verdict = reviewer.review(_bundle())
    second_verdict = reviewer.review(_bundle())

    assert first_verdict.status.value == "PASS"
    assert second_verdict.status.value == "BLOCKED"
    assert reviewer.reviewer_calls_used == 1
    assert len(provider_client.calls) == 1
    assert reviewer.latest_provider_metadata["provider_call_attempted"] is False
    assert reviewer.latest_provider_metadata["provider_call_failed"] is True
    assert "budget" in reviewer.latest_provider_metadata["failure_reason"].lower()


def test_live_openai_reviewer_provider_exception_fails_closed():
    reviewer = LiveOpenAIReviewLoopReviewer(
        api_key="test-key",
        max_reviewer_calls=2,
        provider_client=FakeProviderClient(error=RuntimeError("provider transport failed")),
        provider_name="openai",
        model_name="gpt-4.1-mini",
    )

    verdict = reviewer.review(_bundle())

    assert verdict.status.value == "BLOCKED"
    assert reviewer.reviewer_calls_used == 1
    assert reviewer.latest_provider_metadata["provider_call_attempted"] is True
    assert reviewer.latest_provider_metadata["provider_call_failed"] is True
    assert "provider transport failed" in reviewer.latest_provider_metadata["failure_reason"]


def test_provider_gate_blocks_live_mode_without_allow_provider_calls():
    gate = validate_provider_call_gate(
        reviewer_mode=ReviewLoopReviewerMode.LIVE_OPENAI,
        allow_provider_calls=False,
        max_reviewer_calls=1,
    )

    assert gate.passed is False
    assert gate.blocked is True
    assert "allow-provider-calls" in gate.blocked_reason


def test_provider_gate_blocks_live_mode_without_positive_budget():
    gate = validate_provider_call_gate(
        reviewer_mode=ReviewLoopReviewerMode.LIVE_OPENAI,
        allow_provider_calls=True,
        max_reviewer_calls=0,
    )

    assert gate.passed is False
    assert gate.blocked is True
    assert "max-reviewer-calls" in gate.blocked_reason


def test_provider_gate_allows_replay_mode_even_when_provider_calls_are_allowed():
    gate = validate_provider_call_gate(
        reviewer_mode=ReviewLoopReviewerMode.REPLAY,
        allow_provider_calls=True,
        max_reviewer_calls=0,
    )

    assert gate.passed is True
    assert gate.blocked is False
    assert gate.blocked_reason is None


def test_provider_gate_allows_live_mode_only_with_explicit_budget():
    gate = validate_provider_call_gate(
        reviewer_mode=ReviewLoopReviewerMode.LIVE_OPENAI,
        allow_provider_calls=True,
        max_reviewer_calls=2,
    )

    assert gate.passed is True
    assert gate.blocked is False
    assert gate.blocked_reason is None
