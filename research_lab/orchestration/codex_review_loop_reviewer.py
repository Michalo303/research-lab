from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any

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
