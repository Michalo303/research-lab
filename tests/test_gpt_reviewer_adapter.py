from __future__ import annotations

import json

from research_lab.orchestration.codex_autonomous_contract import (
    CodexRoundInput,
    CodexRoundResult,
    LoopMode,
    LoopStatus,
    ReviewerBudgetConfig,
    ReviewerModelTier,
    ValidationResult,
)
from research_lab.orchestration.gpt_reviewer_adapter import (
    GptReviewerAdapter,
    ReviewerProviderInterface,
)


class StubReviewerProvider(ReviewerProviderInterface):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def review_json(
        self,
        *,
        payload: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> str:
        self.calls.append(
            {
                "payload": payload,
                "selected_model": selected_model,
                "selected_tier": selected_tier,
            }
        )
        return self.responses.pop(0)


def _round_input(*, round_number: int = 1) -> CodexRoundInput:
    return CodexRoundInput(
        run_id="run-1",
        round_number=round_number,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SUPER_AUTO,
        branch="codex/example",
        prior_reviewer_verdicts=["REVISE"] if round_number > 1 else [],
    )


def _round_result(
    *,
    diff_line_count: int = 24,
    summary: str = "Updated the autonomous loop.",
    details: dict[str, object] | None = None,
) -> CodexRoundResult:
    return CodexRoundResult(
        changed_files=[
            "research_lab/orchestration/codex_autonomous_loop.py",
            "tests/test_codex_autonomous_loop.py",
        ],
        diff_line_count=diff_line_count,
        proposed_commands=[],
        summary=summary,
        patch_digest="digest",
        meaningful_progress=True,
        executor_details=details or {"stdout": "safe output"},
    )


def _validation(success: bool = True) -> ValidationResult:
    return ValidationResult(
        success=success,
        tests_requested=["python -m pytest tests/test_codex_autonomous_loop.py -q"],
        tests_passed=["python -m pytest tests/test_codex_autonomous_loop.py -q"] if success else [],
        failures=[] if success else ["test failure"],
    )


def _policy_summary(status: str = "PASS") -> dict[str, object]:
    return {
        "status": status,
        "protected_paths_touched": [],
        "disallowed_paths_touched": [],
        "forbidden_commands_detected": [],
    }


def _provider_json(
    *,
    verdict: str,
    confidence: float = 0.9,
    reason: str = "Looks correct.",
    required_changes: list[str] | None = None,
    safety_notes: list[str] | None = None,
    escalation_recommended: bool = False,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "reason": reason,
            "required_changes": required_changes or [],
            "safety_notes": safety_notes or [],
            "escalation_recommended": escalation_recommended,
        }
    )


def test_valid_pass_json_maps_to_pass_verdict():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.PASS
    assert verdict.summary == "Looks correct."
    assert adapter.last_response is not None
    assert adapter.last_response.selected_tier is ReviewerModelTier.HIGH
    assert adapter.last_response.selected_model == "gpt-reviewer-high"


def test_valid_revise_json_maps_to_revise_and_includes_required_changes():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="REVISE",
                reason="Add one more audit assertion.",
                required_changes=["Cover reviewer call count in the audit."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.REVISE
    assert verdict.issues == ["Cover reviewer call count in the audit."]


def test_valid_blocked_json_maps_to_blocked():
    provider = StubReviewerProvider([_provider_json(verdict="BLOCKED", reason="Need more context.")])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.BLOCKED


def test_valid_unsafe_json_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="UNSAFE", reason="The proposal requests a blocked deployment action.")]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_invalid_json_returns_blocked():
    provider = StubReviewerProvider(["not-json"])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.BLOCKED
    assert "invalid json" in verdict.summary.lower()


def test_missing_required_fields_returns_blocked():
    provider = StubReviewerProvider([json.dumps({"verdict": "PASS"})])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.BLOCKED
    assert "missing" in verdict.summary.lower()


def test_unsafe_provider_recommendation_maps_to_unsafe():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="PASS",
                reason="Safe enough.",
                required_changes=["Push origin main after validation."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE
    assert "unsafe recommendation" in verdict.summary.lower()


def test_safety_note_about_env_and_secrets_does_not_become_unsafe():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="PASS",
                safety_notes=["Do not modify .env or secrets."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.PASS


def test_safety_note_with_deploy_restart_and_daily_research_does_not_become_unsafe():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="PASS",
                safety_notes=["No deploy, no service restart, no daily research."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.PASS


def test_revise_required_change_with_negative_git_reset_instruction_does_not_become_unsafe():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="REVISE",
                required_changes=["Add a test. Do not use git reset --hard."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.REVISE


def test_safety_note_about_safe_sync_wrapper_does_not_become_unsafe():
    provider = StubReviewerProvider(
        [
            _provider_json(
                verdict="REVISE",
                safety_notes=["Do not run scripts/run_safe_sync_with_preflight.sh."],
            )
        ]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.REVISE


def test_positive_git_reset_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Run git reset --hard."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_git_clean_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Execute git clean -fd."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_rm_rf_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Use rm -rf to delete files."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_deploy_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Deploy to production."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_service_restart_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Restart the service with systemctl restart."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_daily_research_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Run daily research now."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_registry_append_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Append this to registry."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_push_origin_main_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Push origin main."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_merge_main_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Merge main."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_modify_env_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Modify .env."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_read_env_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Read .env and print it."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_read_secrets_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Read secrets/."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_positive_safe_sync_wrapper_recommendation_still_maps_to_unsafe():
    provider = StubReviewerProvider(
        [_provider_json(verdict="REVISE", required_changes=["Run scripts/run_safe_sync_with_preflight.sh."])]
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.UNSAFE


def test_secret_like_strings_are_redacted_before_provider_call():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)
    round_result = _round_result(
        summary="Updated review logic with OPENAI_API_KEY=super-secret-value",
        details={"stdout": "token=abc123 bearer sk-live-1234567890ABCDEF"},
    )

    adapter.review_with_context(
        round_input=_round_input(),
        round_result=round_result,
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    payload = provider.calls[0]["payload"]
    assert "super-secret-value" not in payload
    assert "abc123" not in payload
    assert "sk-live-1234567890ABCDEF" not in payload
    assert "[REDACTED]" in payload


def test_env_like_values_are_redacted_before_provider_call():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)
    round_result = _round_result(details={"stdout": "DATABASE_URL=postgres://user:pass@example"})

    adapter.review_with_context(
        round_input=_round_input(),
        round_result=round_result,
        validation_result=_validation(),
        policy_summary={"status": "PASS", "env_excerpt": ".env SECRET_KEY=abcdef"},
    )

    payload = provider.calls[0]["payload"]
    assert "postgres://user:pass@example" not in payload
    assert "abcdef" not in payload
    assert "[REDACTED]" in payload


def test_long_diff_context_is_truncated_with_audit_note():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(provider=provider, dry_run=False, max_summary_chars=120)

    adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(summary="x" * 400, diff_line_count=2500),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert adapter.last_redaction_notes
    assert any("truncated" in note.lower() for note in adapter.last_redaction_notes)
    payload = provider.calls[0]["payload"]
    assert len(payload) < 4000


def test_budget_max_reviewer_calls_is_enforced():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(
        provider=provider,
        dry_run=False,
        budget_config=ReviewerBudgetConfig(max_reviewer_calls_per_run=0),
    )

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.BLOCKED
    assert adapter.last_response is not None
    assert adapter.last_response.budget_blocked is True
    assert provider.calls == []


def test_very_high_blocked_when_not_allowed():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(
        provider=provider,
        dry_run=False,
        requested_tier=ReviewerModelTier.VERY_HIGH,
        budget_config=ReviewerBudgetConfig(allow_very_high=False),
    )

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.BLOCKED
    assert adapter.last_response is not None
    assert adapter.last_response.selected_tier is ReviewerModelTier.VERY_HIGH
    assert adapter.last_response.budget_blocked is True


def test_very_high_allowed_when_enabled_and_budget_remains():
    provider = StubReviewerProvider([_provider_json(verdict="PASS")])
    adapter = GptReviewerAdapter(
        provider=provider,
        dry_run=False,
        requested_tier=ReviewerModelTier.VERY_HIGH,
        budget_config=ReviewerBudgetConfig(allow_very_high=True),
    )

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary=_policy_summary(),
    )

    assert verdict.status is LoopStatus.PASS
    assert provider.calls[0]["selected_tier"] is ReviewerModelTier.VERY_HIGH
    assert adapter.last_response is not None
    assert adapter.last_response.selected_model == "gpt-reviewer-very-high"
