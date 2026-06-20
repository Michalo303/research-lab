from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from research_lab.orchestration.codex_autonomous_contract import (
    LoopStatus,
    ReviewerBudgetConfig,
    ReviewerInterface,
    ReviewerModelTier,
    ReviewerRequest,
    ReviewerResponse,
    ReviewVerdict,
)


NEGATIVE_CONTEXT_MARKERS = [
    "do not",
    "don't",
    "never",
    "must not",
    "forbidden",
    "hard prohibitions",
    "disallowed",
    "not allowed",
    "safety note",
    "safety_notes",
    "no deploy",
    "no service restart",
    "avoid",
]

POSITIVE_UNSAFE_INTENTS: list[tuple[str, tuple[str, ...]]] = [
    ("git reset --hard", ("run git reset --hard", "execute git reset --hard", "use git reset --hard")),
    ("git clean", ("run git clean", "execute git clean", "use git clean")),
    ("rm -rf", ("use rm -rf", "run rm -rf", "delete files with rm -rf")),
    ("deploy", ("deploy to production", "deploy production", "run deploy", "execute deploy")),
    ("systemctl", ("systemctl restart", "restart the service", "restart service", "service restart")),
    ("daily research", ("run daily research", "daily research now", "run_daily")),
    ("registry append", ("append this to registry", "append to registry", "registry append")),
    ("push origin main", ("push origin main",)),
    ("merge main", ("merge main",)),
    (".env", ("modify .env", "read .env", "print it", "print .env", "open .env")),
    ("secrets/", ("modify secrets", "read secrets", "print secrets", "read secrets/")),
    (
        "scripts/run_safe_sync_with_preflight.sh",
        ("run scripts/run_safe_sync_with_preflight.sh", "execute scripts/run_safe_sync_with_preflight.sh"),
    ),
]

RESPONSE_REQUIRED_FIELDS = {
    "verdict",
    "confidence",
    "reason",
    "required_changes",
    "safety_notes",
    "escalation_recommended",
}


class ReviewerProviderInterface(Protocol):
    def review_json(
        self,
        *,
        payload: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> str:
        ...


@dataclass
class DisabledReviewerProvider:
    reason: str = "Live GPT reviewer provider is not configured."

    def review_json(
        self,
        *,
        payload: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> str:
        raise RuntimeError(self.reason)


class GptReviewerAdapter(ReviewerInterface):
    def __init__(
        self,
        *,
        provider: ReviewerProviderInterface,
        dry_run: bool,
        task_text: str = "",
        requested_tier: ReviewerModelTier = ReviewerModelTier.HIGH,
        budget_config: ReviewerBudgetConfig | None = None,
        max_summary_chars: int = 2000,
    ) -> None:
        self.provider = provider
        self.dry_run = dry_run
        self.task_text = task_text
        self.requested_tier = requested_tier
        self.budget_config = budget_config or ReviewerBudgetConfig()
        self.max_summary_chars = max_summary_chars
        self.call_count = 0
        self.very_high_calls_used = 0
        self.last_request: ReviewerRequest | None = None
        self.last_response: ReviewerResponse | None = None
        self.last_redaction_notes: list[str] = []

    def review(
        self,
        round_input,
        round_result,
        *,
        validation_result=None,
        policy_summary: dict[str, Any] | None = None,
    ) -> ReviewVerdict:
        if validation_result is None:
            raise ValueError("validation_result is required for GPT reviewer execution.")
        return self.review_with_context(
            round_input=round_input,
            round_result=round_result,
            validation_result=validation_result,
            policy_summary=policy_summary or {},
        )

    def review_with_context(
        self,
        *,
        round_input,
        round_result,
        validation_result,
        policy_summary: dict[str, Any],
    ) -> ReviewVerdict:
        decision = self._select_tier()
        if decision["budget_blocked"]:
            return self._blocked_verdict(
                reason=decision["reason"],
                selected_model=decision["selected_model"],
                selected_tier=decision["selected_tier"],
                budget_blocked=True,
            )

        request = ReviewerRequest(
            run_id=round_input.run_id,
            round_number=round_input.round_number,
            mode=round_input.mode,
            task_text=self.task_text or f"Review task file {round_input.task_file}.",
            changed_files=list(round_result.changed_files),
            diff_line_count=round_result.diff_line_count,
            validation_summary=validation_result.to_dict(),
            policy_summary=dict(policy_summary),
            codex_summary=round_result.summary,
            codex_executor_details=dict(round_result.executor_details),
            previous_reviewer_verdicts=list(round_input.prior_reviewer_verdicts),
        )
        payload, notes = self._serialize_request(request)
        self.last_request = request
        self.last_redaction_notes = notes

        if self.dry_run:
            self.last_response = ReviewerResponse(
                verdict=LoopStatus.PASS,
                confidence=0.0,
                reason="GPT reviewer skipped in dry-run mode.",
                required_changes=[],
                safety_notes=[],
                escalation_recommended=False,
                selected_model=decision["selected_model"],
                selected_tier=decision["selected_tier"],
                budget_blocked=False,
                raw_response_redacted="",
            )
            return ReviewVerdict(status=LoopStatus.PASS, summary="GPT reviewer skipped in dry-run mode.")

        try:
            raw_response = self.provider.review_json(
                payload=payload,
                selected_model=decision["selected_model"],
                selected_tier=decision["selected_tier"],
            )
        except Exception as exc:
            return self._blocked_verdict(
                reason=f"GPT reviewer provider failed: {exc}",
                selected_model=decision["selected_model"],
                selected_tier=decision["selected_tier"],
                budget_blocked=False,
            )

        self.call_count += 1
        if decision["selected_tier"] is ReviewerModelTier.VERY_HIGH:
            self.very_high_calls_used += 1

        return self._parse_provider_response(
            raw_response=raw_response,
            selected_model=decision["selected_model"],
            selected_tier=decision["selected_tier"],
        )

    def _select_tier(self) -> dict[str, Any]:
        if self.call_count >= self.budget_config.max_reviewer_calls_per_run:
            return {
                "selected_tier": self.requested_tier,
                "selected_model": self._model_for_tier(self.requested_tier),
                "budget_blocked": True,
                "reason": "Maximum reviewer calls per run exceeded.",
            }

        if self.requested_tier is ReviewerModelTier.VERY_HIGH:
            if not self.budget_config.allow_very_high:
                return {
                    "selected_tier": ReviewerModelTier.VERY_HIGH,
                    "selected_model": self.budget_config.very_high_model,
                    "budget_blocked": True,
                    "reason": "Requested reviewer very_high tier is disabled.",
                }
            if self.very_high_calls_used >= self.budget_config.max_very_high_calls_per_run:
                return {
                    "selected_tier": ReviewerModelTier.VERY_HIGH,
                    "selected_model": self.budget_config.very_high_model,
                    "budget_blocked": True,
                    "reason": "Maximum reviewer very_high calls per run exceeded.",
                }

        selected_tier = self.requested_tier
        return {
            "selected_tier": selected_tier,
            "selected_model": self._model_for_tier(selected_tier),
            "budget_blocked": False,
            "reason": "",
        }

    def _serialize_request(self, request: ReviewerRequest) -> tuple[str, list[str]]:
        notes: list[str] = []
        payload = request.to_dict()
        payload = self._sanitize_payload(payload)
        codex_summary = payload["codex_summary"]
        if isinstance(codex_summary, str) and len(codex_summary) > self.max_summary_chars:
            payload["codex_summary"] = codex_summary[: self.max_summary_chars].rstrip() + "... [TRUNCATED]"
            notes.append("Truncated long codex summary before provider call.")
        executor_details = payload.get("codex_executor_details", {})
        if isinstance(executor_details, dict):
            for key, value in list(executor_details.items()):
                if isinstance(value, str) and len(value) > self.max_summary_chars:
                    executor_details[key] = value[: self.max_summary_chars].rstrip() + "... [TRUNCATED]"
                    notes.append(f"Truncated long executor detail field `{key}` before provider call.")
        if request.diff_line_count > 1000:
            notes.append("Large diff represented by metadata only; full diff content was not sent to the reviewer.")
        payload["redaction_truncation_notes"] = notes
        return json.dumps(payload, indent=2, sort_keys=True), notes

    def _parse_provider_response(
        self,
        *,
        raw_response: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> ReviewVerdict:
        sanitized_raw = self._sanitize_text(raw_response)
        try:
            payload = json.loads(raw_response)
        except json.JSONDecodeError:
            return self._blocked_verdict(
                reason="Invalid JSON from GPT reviewer provider.",
                selected_model=selected_model,
                selected_tier=selected_tier,
                budget_blocked=False,
                raw_response_redacted=sanitized_raw,
            )

        missing = sorted(RESPONSE_REQUIRED_FIELDS.difference(payload))
        if missing:
            return self._blocked_verdict(
                reason=f"Missing required reviewer response fields: {', '.join(missing)}.",
                selected_model=selected_model,
                selected_tier=selected_tier,
                budget_blocked=False,
                raw_response_redacted=sanitized_raw,
            )

        try:
            verdict = LoopStatus(str(payload["verdict"]).upper())
        except ValueError:
            return self._blocked_verdict(
                reason=f"Unsupported reviewer verdict: {payload['verdict']}.",
                selected_model=selected_model,
                selected_tier=selected_tier,
                budget_blocked=False,
                raw_response_redacted=sanitized_raw,
            )

        response = ReviewerResponse(
            verdict=verdict,
            confidence=float(payload["confidence"]),
            reason=str(payload["reason"]),
            required_changes=[str(item) for item in payload["required_changes"]],
            safety_notes=[str(item) for item in payload["safety_notes"]],
            escalation_recommended=bool(payload["escalation_recommended"]),
            selected_model=selected_model,
            selected_tier=selected_tier,
            budget_blocked=False,
            raw_response_redacted=sanitized_raw,
        )
        self.last_response = response

        unsafe_detected, unsafe_reason = self._detect_unsafe_reviewer_output(response)
        if unsafe_detected:
            unsafe_summary = "Unsafe recommendation detected in GPT reviewer output."
            self.last_response = ReviewerResponse(
                verdict=LoopStatus.UNSAFE,
                confidence=response.confidence,
                reason=unsafe_reason or unsafe_summary,
                required_changes=response.required_changes,
                safety_notes=response.safety_notes,
                escalation_recommended=response.escalation_recommended,
                selected_model=selected_model,
                selected_tier=selected_tier,
                budget_blocked=False,
                raw_response_redacted=sanitized_raw,
            )
            return ReviewVerdict(status=LoopStatus.UNSAFE, summary=unsafe_summary, issues=response.required_changes)

        return ReviewVerdict(status=verdict, summary=response.reason, issues=response.required_changes)

    def _blocked_verdict(
        self,
        *,
        reason: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
        budget_blocked: bool,
        raw_response_redacted: str = "",
    ) -> ReviewVerdict:
        self.last_response = ReviewerResponse(
            verdict=LoopStatus.BLOCKED,
            confidence=0.0,
            reason=reason,
            required_changes=[],
            safety_notes=[],
            escalation_recommended=False,
            selected_model=selected_model,
            selected_tier=selected_tier,
            budget_blocked=budget_blocked,
            raw_response_redacted=raw_response_redacted,
        )
        return ReviewVerdict(status=LoopStatus.BLOCKED, summary=reason)

    def _model_for_tier(self, tier: ReviewerModelTier) -> str:
        if tier is ReviewerModelTier.VERY_HIGH:
            return self.budget_config.very_high_model
        if tier is ReviewerModelTier.HIGH:
            return self.budget_config.high_model
        return self.budget_config.default_model

    def _detect_unsafe_reviewer_output(self, response: ReviewerResponse) -> tuple[bool, str]:
        for text in [response.reason, *response.required_changes, *response.safety_notes]:
            matched_fragment = self._screen_dangerous_intent(text)
            if matched_fragment:
                return True, f"Unsafe recommendation detected in GPT reviewer output: {matched_fragment}"
        return False, ""

    def _screen_dangerous_intent(self, text: str) -> str | None:
        lowered = " ".join(text.lower().split())
        for fragment, intents in POSITIVE_UNSAFE_INTENTS:
            matched_intent = next((intent for intent in intents if intent in lowered), None)
            if not matched_intent:
                continue
            if self._intent_is_negated(lowered, matched_intent):
                continue
            return fragment
        return None

    def _intent_is_negated(self, text: str, matched_intent: str) -> bool:
        index = text.find(matched_intent)
        if index == -1:
            return False
        context = text[max(0, index - 80) : index]
        return any(marker in context for marker in NEGATIVE_CONTEXT_MARKERS)

    def _sanitize_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_payload(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value

    def _sanitize_text(self, text: str) -> str:
        sanitized = text
        sanitized = re.sub(
            r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|URL)[A-Z0-9_]*)=([^\s]+)",
            r"\1=[REDACTED]",
            sanitized,
        )
        sanitized = re.sub(r"(?i)\bbearer\s+[a-z0-9._-]+\b", "bearer [REDACTED]", sanitized)
        sanitized = re.sub(r"(?i)\b(token|secret|password)\s*[:=]\s*([^\s]+)", r"\1=[REDACTED]", sanitized)
        sanitized = re.sub(r"(?i)\bsk-[a-z0-9_-]{10,}\b", "[REDACTED]", sanitized)
        sanitized = re.sub(r"(?i)(\.env|secrets/)\S*", r"\1[REDACTED]", sanitized)
        return sanitized
