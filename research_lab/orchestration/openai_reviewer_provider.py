from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib import request

from research_lab.orchestration.codex_autonomous_contract import ReviewerModelTier
from research_lab.orchestration.reviewer_provider_config import ReviewerProviderConfig


TransportCallable = Callable[..., dict[str, Any]]


class OpenAICompatibleReviewerProvider:
    def __init__(
        self,
        *,
        config: ReviewerProviderConfig,
        transport: TransportCallable | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or self._default_transport
        self.environment = environment or os.environ
        self.last_call_metadata: dict[str, object] | None = None

    def review_json(
        self,
        *,
        payload: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> str:
        api_key = self.environment.get(self.config.api_key_env_var, "")
        self.last_call_metadata = {
            "provider_name": "openai-compatible",
            "selected_model": selected_model,
            "selected_tier": selected_tier.value,
            "live_call_attempted": False,
            "credential_present": bool(api_key),
        }
        if not api_key:
            raise RuntimeError(f"missing reviewer API credential in env var {self.config.api_key_env_var}.")

        self.last_call_metadata["live_call_attempted"] = True
        response = self.transport(
            url=self._responses_url(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            body=self._build_request_body(payload, selected_model, selected_tier),
            timeout_seconds=self.config.timeout_seconds,
        )
        return self._extract_text(response)

    def describe_state(self) -> str:
        return json.dumps(self.last_call_metadata or {}, sort_keys=True)

    def _responses_url(self) -> str:
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/responses"

    def _build_request_body(
        self,
        payload: str,
        selected_model: str,
        selected_tier: ReviewerModelTier,
    ) -> dict[str, object]:
        return {
            "model": selected_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are a strict code reviewer. Return only a strict JSON object with the "
                                "required verdict, confidence, reason, required_changes, safety_notes, "
                                "and escalation_recommended fields."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": payload}],
                },
            ],
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": self.config.max_output_tokens,
            "reasoning": {"effort": "high" if selected_tier is ReviewerModelTier.VERY_HIGH else "medium"},
        }

    def _extract_text(self, response: dict[str, Any]) -> str:
        output = response.get("output", [])
        for item in output:
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        choices = response.get("choices", [])
        for choice in choices:
            message = choice.get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [part.get("text", "") for part in content if isinstance(part, dict)]
                combined = "".join(texts).strip()
                if combined:
                    return combined
        raise RuntimeError("OpenAI-compatible reviewer provider returned no textual JSON response.")

    def _default_transport(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        req = request.Request(
            url=url,
            headers=headers,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
