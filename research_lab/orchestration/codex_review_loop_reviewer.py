from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import os
import re
from typing import Any, Protocol

from research_lab.orchestration.codex_autonomous_contract import LoopStatus, ReviewVerdict
from research_lab.orchestration.codex_review_loop import ReviewerBundle


class ReviewerDecisionError(ValueError):
    pass


class ReviewLoopReviewerMode(str, Enum):
    REPLAY = "replay"
    LIVE_OPENAI = "live-openai"


class ReviewerDecisionVerdict(str, Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    ABORT = "ABORT"


@dataclass(frozen=True)
class ReviewerDecision:
    verdict: ReviewerDecisionVerdict
    reason: str
    next_codex_instruction: str | None
    risk_flags: list[str] = field(default_factory=list)
    allowed_to_continue: bool = True

    def __post_init__(self) -> None:
        _validate_decision_fields(self)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        return payload

    def to_review_verdict(self) -> ReviewVerdict:
        if self.verdict is ReviewerDecisionVerdict.PASS:
            return ReviewVerdict(status=LoopStatus.PASS, summary=self.reason, issues=list(self.risk_flags))
        if self.verdict is ReviewerDecisionVerdict.RETRY:
            issues = list(self.risk_flags)
            if self.next_codex_instruction:
                issues.append(self.next_codex_instruction)
            return ReviewVerdict(status=LoopStatus.REVISE, summary=self.reason, issues=issues)
        return ReviewVerdict(status=LoopStatus.BLOCKED, summary=self.reason, issues=list(self.risk_flags))


def parse_reviewer_decision(raw_value: str | dict[str, Any]) -> ReviewerDecision:
    payload = _normalize_payload(raw_value)
    required_fields = {
        "verdict",
        "reason",
        "next_codex_instruction",
        "risk_flags",
        "allowed_to_continue",
    }
    missing = sorted(required_fields.difference(payload))
    if missing:
        raise ReviewerDecisionError(f"Missing required reviewer fields: {', '.join(missing)}")

    try:
        verdict = ReviewerDecisionVerdict(str(payload["verdict"]).upper())
    except ValueError as exc:
        raise ReviewerDecisionError(f"Unknown reviewer verdict: {payload['verdict']}") from exc

    return ReviewerDecision(
        verdict=verdict,
        reason=_require_non_empty_string(payload["reason"], field_name="reason"),
        next_codex_instruction=_normalize_optional_string(payload["next_codex_instruction"]),
        risk_flags=_normalize_risk_flags(payload["risk_flags"]),
        allowed_to_continue=_require_bool(payload["allowed_to_continue"], field_name="allowed_to_continue"),
    )


class ReplayReviewLoopReviewer:
    def __init__(self, decisions: list[ReviewerDecision]) -> None:
        if not decisions:
            raise ReviewerDecisionError("At least one replay reviewer decision is required.")
        self._decisions = list(decisions)
        self.bundles: list[ReviewerBundle] = []
        self.emitted_decisions: list[ReviewerDecision] = []
        self.live_provider_calls = 0

    @classmethod
    def from_raw_decisions(cls, raw_decisions: list[str | dict[str, Any]]) -> "ReplayReviewLoopReviewer":
        return cls([parse_reviewer_decision(raw_decision) for raw_decision in raw_decisions])

    @property
    def review_call_count(self) -> int:
        return len(self.bundles)

    @property
    def latest_decision(self) -> ReviewerDecision | None:
        if not self.emitted_decisions:
            return None
        return self.emitted_decisions[-1]

    def review(self, bundle: ReviewerBundle) -> ReviewVerdict:
        self.bundles.append(bundle)
        index = min(bundle.attempt_number - 1, len(self._decisions) - 1)
        decision = self._decisions[index]
        self.emitted_decisions.append(decision)
        return decision.to_review_verdict()


class ReviewerProviderClientInterface(Protocol):
    def review_json(self, *, request_payload: dict[str, object], api_key: str, model_name: str) -> str:
        ...


class OpenAIResponsesReviewerClient:
    def __init__(self) -> None:
        self._client = None

    def review_json(self, *, request_payload: dict[str, object], api_key: str, model_name: str) -> str:
        client = self._client or _build_openai_client(api_key)
        self._client = client
        response = client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": str(request_payload["system_instruction"])}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(request_payload["review_request"], sort_keys=True)}],
                },
            ],
            text={"format": {"type": "json_object"}},
        )
        return _extract_openai_response_text(response)


class LiveOpenAIReviewLoopReviewer:
    def __init__(
        self,
        *,
        api_key: str,
        max_reviewer_calls: int,
        provider_client: ReviewerProviderClientInterface,
        provider_name: str,
        model_name: str,
    ) -> None:
        self.api_key = api_key.strip()
        self.max_reviewer_calls = max_reviewer_calls
        self.provider_client = provider_client
        self.provider_name = provider_name
        self.model_name = model_name
        self.bundles: list[ReviewerBundle] = []
        self.emitted_decisions: list[ReviewerDecision] = []
        self.reviewer_calls_used = 0
        self._latest_decision: ReviewerDecision | None = None
        self.latest_provider_metadata: dict[str, Any] = _default_provider_runtime_metadata(
            provider_name=provider_name,
            model_name=model_name,
            max_reviewer_calls=max_reviewer_calls,
            reviewer_calls_used=0,
        )

    @property
    def review_call_count(self) -> int:
        return len(self.bundles)

    @property
    def live_provider_calls(self) -> int:
        return self.reviewer_calls_used

    @property
    def latest_decision(self) -> ReviewerDecision | None:
        return self._latest_decision

    def review(self, bundle: ReviewerBundle) -> ReviewVerdict:
        self.bundles.append(bundle)
        self._latest_decision = None
        metadata = _default_provider_runtime_metadata(
            provider_name=self.provider_name,
            model_name=self.model_name,
            max_reviewer_calls=self.max_reviewer_calls,
            reviewer_calls_used=self.reviewer_calls_used,
        )

        if not self.api_key:
            metadata["provider_failure_stage"] = "credential_gate"
            return self._blocked_result(
                metadata,
                "Live reviewer mode requires OPENAI_API_KEY to be set before provider calls are allowed.",
            )
        if self.reviewer_calls_used >= self.max_reviewer_calls:
            metadata["provider_failure_stage"] = "budget_gate"
            return self._blocked_result(metadata, "Reviewer call budget exhausted before provider request.")

        request_payload = _build_provider_request_payload(bundle)
        metadata["provider_call_attempted"] = True
        self.reviewer_calls_used += 1
        metadata["reviewer_calls_used"] = self.reviewer_calls_used
        metadata["reviewer_call_budget_used"] = self.reviewer_calls_used
        metadata["reviewer_call_budget_remaining"] = max(self.max_reviewer_calls - self.reviewer_calls_used, 0)
        metadata["reviewer_call_budget_exhausted"] = (
            self.max_reviewer_calls > 0 and metadata["reviewer_call_budget_remaining"] == 0
        )

        try:
            raw_response = self.provider_client.review_json(
                request_payload=request_payload,
                api_key=self.api_key,
                model_name=self.model_name,
            )
            metadata["provider_response_received"] = True
        except Exception as exc:
            metadata["provider_failure_stage"] = "provider_transport"
            return self._blocked_result(metadata, _sanitize_text(str(exc), limit=400))

        try:
            decision = parse_reviewer_decision(raw_response)
        except ReviewerDecisionError as exc:
            sanitized_reason = _classify_parse_failure_reason(exc)
            metadata["parse_failure"] = sanitized_reason
            metadata["provider_failure_stage"] = "strict_parse"
            metadata["provider_parse_failure_reason"] = sanitized_reason
            return self._blocked_result(metadata, sanitized_reason)

        self._latest_decision = decision
        self.emitted_decisions.append(decision)
        metadata["provider_call_succeeded"] = True
        metadata["provider_call_failed"] = False
        metadata["provider_response_parser_valid"] = True
        metadata["provider_failure_stage"] = "none"
        metadata["parsed_reviewer_decision"] = decision.to_dict()
        self.latest_provider_metadata = metadata
        return decision.to_review_verdict()

    def _blocked_result(self, metadata: dict[str, Any], failure_reason: str) -> ReviewVerdict:
        metadata["provider_call_succeeded"] = False
        metadata["provider_call_failed"] = True
        metadata["failure_reason"] = failure_reason
        self.latest_provider_metadata = metadata
        return ReviewVerdict(status=LoopStatus.BLOCKED, summary=failure_reason, issues=[failure_reason])


class LiveReviewerAdapterStub:
    def review(self, bundle: ReviewerBundle) -> ReviewVerdict:
        raise RuntimeError(
            "Live reviewer adapter execution is disabled in this deterministic review-loop path."
        )


@dataclass(frozen=True)
class ProviderCallGateResult:
    passed: bool
    blocked: bool
    blocked_reason: str | None = None


def validate_provider_call_gate(
    *,
    reviewer_mode: ReviewLoopReviewerMode,
    allow_provider_calls: bool,
    max_reviewer_calls: int,
) -> ProviderCallGateResult:
    if reviewer_mode is not ReviewLoopReviewerMode.LIVE_OPENAI:
        return ProviderCallGateResult(passed=True, blocked=False, blocked_reason=None)
    if not allow_provider_calls:
        return ProviderCallGateResult(
            passed=False,
            blocked=True,
            blocked_reason="Live reviewer mode requires --allow-provider-calls true.",
        )
    if max_reviewer_calls < 1:
        return ProviderCallGateResult(
            passed=False,
            blocked=True,
            blocked_reason="Live reviewer mode requires --max-reviewer-calls to be a positive integer.",
        )
    return ProviderCallGateResult(passed=True, blocked=False, blocked_reason=None)


def build_live_openai_reviewer(
    *,
    max_reviewer_calls: int,
    environment: dict[str, str] | None = None,
    provider_client: ReviewerProviderClientInterface | None = None,
    provider_name: str = "openai",
    model_name: str = "gpt-4.1-mini",
) -> LiveOpenAIReviewLoopReviewer:
    env = environment or os.environ
    return LiveOpenAIReviewLoopReviewer(
        api_key=env.get("OPENAI_API_KEY", ""),
        max_reviewer_calls=max_reviewer_calls,
        provider_client=provider_client or OpenAIResponsesReviewerClient(),
        provider_name=provider_name,
        model_name=model_name,
    )


def _normalize_payload(raw_value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_value, str):
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ReviewerDecisionError("Malformed reviewer decision JSON.") from exc
    elif isinstance(raw_value, dict):
        payload = dict(raw_value)
    else:
        raise ReviewerDecisionError("Reviewer decision must be JSON text or a dict payload.")

    if not isinstance(payload, dict):
        raise ReviewerDecisionError("Reviewer decision payload must be a JSON object.")
    return payload


def _validate_decision_fields(decision: ReviewerDecision) -> None:
    if decision.verdict is ReviewerDecisionVerdict.RETRY and not decision.next_codex_instruction:
        raise ReviewerDecisionError("RETRY decisions must include a non-empty next_codex_instruction.")
    if decision.verdict is ReviewerDecisionVerdict.ABORT and decision.allowed_to_continue:
        raise ReviewerDecisionError("ABORT decisions must set allowed_to_continue to false.")
    if decision.verdict is ReviewerDecisionVerdict.PASS and decision.next_codex_instruction:
        raise ReviewerDecisionError("PASS decisions must not include next_codex_instruction.")


def _require_non_empty_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewerDecisionError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReviewerDecisionError("next_codex_instruction must be null or a non-empty string.")
    return value.strip()


def _normalize_risk_flags(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReviewerDecisionError("risk_flags must be a list of non-empty strings.")
    return [item.strip() for item in value]


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ReviewerDecisionError(f"{field_name} must be a boolean.")
    return value


def _build_openai_client(api_key: str):
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - exercised via fake clients in tests
        raise RuntimeError("OpenAI Python client package is unavailable for live reviewer mode.") from exc
    return OpenAI(api_key=api_key)


def _extract_openai_response_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            contents = getattr(item, "content", None) or item.get("content", []) if isinstance(item, dict) else []
            for content in contents:
                text = getattr(content, "text", None) or (content.get("text") if isinstance(content, dict) else None)
                if text:
                    return str(text)
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    raise RuntimeError("OpenAI live reviewer returned no textual JSON response.")


def _build_provider_request_payload(bundle: ReviewerBundle) -> dict[str, object]:
    return {
        "system_instruction": (
            "You are a code-review decision helper. Return JSON only. "
            "Do not suggest or trigger actions directly. "
            "Never request file writes, commits, merges, deployments, registry appends, backtests, Hermes calls, "
            "broker/order/API actions, or strategy promotion. "
            "Return only review metadata."
        ),
        "review_request": {
            "task_prompt_summary": _sanitize_text(bundle.current_prompt or bundle.initial_task, limit=600),
            "executor_output_summary": {
                "changed_files": list(bundle.changed_files[:10]),
                "changed_file_count": len(bundle.changed_files),
            },
            "validator_output_summary": {
                "success": bool(bundle.validation_output.get("success", False)),
                "tests_requested": _bounded_string_list(bundle.validation_output.get("tests_requested", []), limit=5),
                "tests_passed": _bounded_string_list(bundle.validation_output.get("tests_passed", []), limit=5),
                "failures": _bounded_string_list(bundle.validation_output.get("failures", []), limit=5),
            },
            "diff_summary": _sanitize_text(bundle.diff_summary, limit=500),
            "response_contract": {
                "required_fields": [
                    "verdict",
                    "reason",
                    "next_codex_instruction",
                    "risk_flags",
                    "allowed_to_continue",
                ],
                "allowed_verdicts": ["PASS", "RETRY", "ABORT"],
                "unsupported_verdict_aliases": {
                    "CHANGES_REQUESTED": "RETRY",
                    "BLOCKED": "ABORT",
                },
                "conditional_rules": {
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
                },
                "optional_metadata_fields": ["required_changes", "confidence"],
                "json_only": True,
            },
        },
    }


def _bounded_string_list(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_sanitize_text(str(value), limit=200) for value in values[:limit]]


def _sanitize_text(value: str, *, limit: int) -> str:
    text = value.strip()
    text = re.sub(r"(?i)\b([A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[REDACTED_API_KEY]", text)
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _default_provider_runtime_metadata(
    *,
    provider_name: str | None,
    model_name: str | None,
    max_reviewer_calls: int,
    reviewer_calls_used: int,
) -> dict[str, Any]:
    reviewer_call_budget_remaining = max(max_reviewer_calls - reviewer_calls_used, 0)
    return {
        "reviewer_calls_used": reviewer_calls_used,
        "max_reviewer_calls": max_reviewer_calls,
        "reviewer_call_budget_total": max_reviewer_calls,
        "reviewer_call_budget_used": reviewer_calls_used,
        "reviewer_call_budget_remaining": reviewer_call_budget_remaining,
        "reviewer_call_budget_exhausted": max_reviewer_calls > 0 and reviewer_call_budget_remaining == 0,
        "provider_name": provider_name,
        "model_name": model_name,
        "provider_call_attempted": False,
        "provider_call_succeeded": False,
        "provider_call_failed": False,
        "provider_response_received": False,
        "provider_response_parser_valid": False,
        "provider_failure_stage": "not_attempted",
        "failure_reason": None,
        "parse_failure": None,
        "provider_parse_failure_reason": "none",
        "parsed_reviewer_decision": None,
    }


def _classify_parse_failure_reason(exc: ReviewerDecisionError) -> str:
    message = str(exc)
    if message == "Malformed reviewer decision JSON.":
        return "malformed_json"
    if message.startswith("Missing required reviewer fields:"):
        return "missing_required_field"
    if message.startswith("Unknown reviewer verdict:"):
        return "invalid_verdict"
    if message == "PASS decisions must not include next_codex_instruction.":
        return "invalid_pass_instruction"
    return "parser_exception"
