from __future__ import annotations

import json

import pytest

from research_lab.orchestration.codex_autonomous_contract import (
    CodexRoundInput,
    CodexRoundResult,
    LoopMode,
    LoopStatus,
    ReviewerBudgetConfig,
    ReviewerModelTier,
    ValidationResult,
)
from research_lab.orchestration.gpt_reviewer_adapter import GptReviewerAdapter
from research_lab.orchestration.openai_reviewer_provider import OpenAICompatibleReviewerProvider
from research_lab.orchestration.reviewer_provider_config import (
    ReviewerProviderConfig,
    reviewer_provider_preflight,
)
from scripts.run_codex_auto_loop import _build_reviewer, parse_args
from research_lab.orchestration.codex_autonomous_contract import CodexLoopConfig


class StubTransport:
    def __init__(self, response: dict[str, object] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _provider_response_json(text: str) -> dict[str, object]:
    return {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                    }
                ]
            }
        ]
    }


def _round_input() -> CodexRoundInput:
    return CodexRoundInput(
        run_id="run-1",
        round_number=1,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SUPER_AUTO,
        branch="codex/example",
    )


def _round_result() -> CodexRoundResult:
    return CodexRoundResult(
        changed_files=["research_lab/orchestration/gpt_reviewer_adapter.py"],
        diff_line_count=42,
        summary="Updated reviewer provider integration.",
        executor_details={"stdout": "safe output"},
    )


def _validation() -> ValidationResult:
    return ValidationResult(
        success=True,
        tests_requested=["python -m pytest tests/test_openai_reviewer_provider.py -q"],
        tests_passed=["python -m pytest tests/test_openai_reviewer_provider.py -q"],
    )


def test_provider_refuses_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=StubTransport(_provider_response_json("{}")),
    )

    with pytest.raises(RuntimeError, match="missing"):
        provider.review_json(payload="{}", selected_model="gpt-reviewer-high", selected_tier=ReviewerModelTier.HIGH)


def test_provider_does_not_print_or_log_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-key-value")
    transport = StubTransport(_provider_response_json('{"verdict":"PASS"}'))
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=transport,
    )

    provider.review_json(payload='{"task":"review"}', selected_model="gpt-reviewer-high", selected_tier=ReviewerModelTier.HIGH)
    metadata = provider.last_call_metadata

    assert metadata is not None
    assert metadata["credential_present"] is True
    assert "super-secret-key-value" not in json.dumps(metadata)
    assert "super-secret-key-value" not in provider.describe_state()


def test_provider_builds_strict_json_review_request(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    transport = StubTransport(_provider_response_json('{"verdict":"PASS"}'))
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(
            model="gpt-reviewer-high",
            base_url="https://api.example.test/v1",
            timeout_seconds=17,
            max_output_tokens=333,
        ),
        transport=transport,
    )

    provider.review_json(
        payload='{"task":"review"}',
        selected_model="gpt-reviewer-high",
        selected_tier=ReviewerModelTier.HIGH,
    )

    call = transport.calls[0]
    assert call["url"] == "https://api.example.test/v1/responses"
    assert call["timeout_seconds"] == 17
    assert call["body"]["model"] == "gpt-reviewer-high"
    assert call["body"]["max_output_tokens"] == 333
    assert call["body"]["text"]["format"]["type"] == "json_object"
    assert "strict json" in json.dumps(call["body"]).lower()


def test_provider_passes_selected_model_and_tier(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    transport = StubTransport(_provider_response_json('{"verdict":"PASS"}'))
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="default-model"),
        transport=transport,
    )

    provider.review_json(
        payload="{}",
        selected_model="selected-model",
        selected_tier=ReviewerModelTier.VERY_HIGH,
    )

    assert transport.calls[0]["body"]["model"] == "selected-model"
    assert transport.calls[0]["body"]["reasoning"]["effort"] == "high"
    assert provider.last_call_metadata["selected_tier"] == "very_high"


def test_provider_returns_mocked_json_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=StubTransport(_provider_response_json('{"verdict":"PASS"}')),
    )

    response = provider.review_json(payload="{}", selected_model="gpt-reviewer-high", selected_tier=ReviewerModelTier.HIGH)

    assert response == '{"verdict":"PASS"}'


def test_gpt_reviewer_adapter_parses_pass_json_through_openai_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=StubTransport(
            _provider_response_json(
                json.dumps(
                    {
                        "verdict": "PASS",
                        "confidence": 0.93,
                        "reason": "Looks correct.",
                        "required_changes": [],
                        "safety_notes": [],
                        "escalation_recommended": False,
                    }
                )
            )
        ),
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary={"status": "PASS"},
    )

    assert verdict.status is LoopStatus.PASS


def test_invalid_provider_json_maps_to_blocked(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=StubTransport(_provider_response_json("not-json")),
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary={"status": "PASS"},
    )

    assert verdict.status is LoopStatus.BLOCKED


def test_missing_credential_maps_to_blocked(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleReviewerProvider(
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        transport=StubTransport(_provider_response_json("{}")),
    )
    adapter = GptReviewerAdapter(provider=provider, dry_run=False)

    verdict = adapter.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary={"status": "PASS"},
    )

    assert verdict.status is LoopStatus.BLOCKED


def test_reviewer_fake_remains_default():
    args = parse_args([])

    assert args.reviewer == "fake"
    assert args.reviewer_provider == "disabled"
    assert args.enable_live_reviewer == "false"


def test_cli_accepts_review_only_mode_for_non_live_smoke():
    args = parse_args(["--mode", "review_only", "--reviewer", "gpt"])

    assert args.mode == "review_only"
    assert args.reviewer == "gpt"


def test_reviewer_gpt_without_enable_live_reviewer_does_not_call_live_provider():
    args = parse_args(["--reviewer", "gpt", "--reviewer-provider", "openai-compatible"])
    reviewer = _build_reviewer(args, CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO), "task")

    assert isinstance(reviewer, GptReviewerAdapter)
    assert reviewer.provider.__class__.__name__ == "DisabledReviewerProvider"


def test_reviewer_gpt_with_provider_disabled_does_not_call_live_provider():
    args = parse_args(["--reviewer", "gpt", "--reviewer-provider", "disabled", "--enable-live-reviewer", "true"])
    reviewer = _build_reviewer(args, CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO), "task")

    assert isinstance(reviewer, GptReviewerAdapter)
    assert reviewer.provider.__class__.__name__ == "DisabledReviewerProvider"


def test_reviewer_gpt_with_openai_provider_and_missing_credential_blocks_safely(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = parse_args(
        [
            "--reviewer",
            "gpt",
            "--reviewer-provider",
            "openai-compatible",
            "--enable-live-reviewer",
            "true",
        ]
    )
    reviewer = _build_reviewer(args, CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO), "task")

    verdict = reviewer.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary={"status": "PASS"},
    )

    assert verdict.status is LoopStatus.BLOCKED


def test_credential_preflight_reports_presence_but_never_value(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    preflight = reviewer_provider_preflight(
        provider_name="openai-compatible",
        live_reviewer_enabled=True,
        config=ReviewerProviderConfig(model="gpt-reviewer-high"),
        selected_tier=ReviewerModelTier.HIGH,
        allow_very_high=False,
        max_reviewer_calls=20,
        max_very_high_calls=1,
    )

    assert preflight["api_key_env_var"] == "OPENAI_API_KEY"
    assert preflight["api_key_present"] is True
    assert "super-secret-value" not in json.dumps(preflight)


def test_reviewer_gpt_with_openai_provider_and_mocked_credential_client_works_in_tests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = CodexLoopConfig.for_mode(LoopMode.SUPER_AUTO)
    config.dry_run_external_calls = False
    reviewer = _build_reviewer(
        parse_args(
            [
                "--reviewer",
                "gpt",
                "--reviewer-provider",
                "openai-compatible",
                "--enable-live-reviewer",
                "true",
            ]
        ),
        config,
        "task",
        provider_transport=StubTransport(
            _provider_response_json(
                json.dumps(
                    {
                        "verdict": "PASS",
                        "confidence": 0.9,
                        "reason": "Looks good.",
                        "required_changes": [],
                        "safety_notes": [],
                        "escalation_recommended": False,
                    }
                )
            )
        ),
    )

    verdict = reviewer.review_with_context(
        round_input=_round_input(),
        round_result=_round_result(),
        validation_result=_validation(),
        policy_summary={"status": "PASS"},
    )

    assert verdict.status is LoopStatus.PASS
