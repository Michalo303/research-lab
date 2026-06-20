from __future__ import annotations

from dataclasses import dataclass
import os

from research_lab.orchestration.codex_autonomous_contract import ReviewerModelTier


@dataclass
class ReviewerProviderConfig:
    api_key_env_var: str = "OPENAI_API_KEY"
    base_url: str | None = None
    model: str = "gpt-reviewer-high"
    timeout_seconds: int = 60
    max_output_tokens: int = 1200


def reviewer_provider_preflight(
    *,
    provider_name: str,
    live_reviewer_enabled: bool,
    config: ReviewerProviderConfig,
    selected_tier: ReviewerModelTier,
    allow_very_high: bool,
    max_reviewer_calls: int,
    max_very_high_calls: int,
) -> dict[str, object]:
    api_key_present = bool(os.environ.get(config.api_key_env_var))
    selected_model = config.model
    return {
        "provider_selected": provider_name,
        "live_reviewer_enabled": live_reviewer_enabled,
        "api_key_env_var": config.api_key_env_var,
        "api_key_present": api_key_present,
        "selected_model": selected_model,
        "selected_tier": selected_tier.value,
        "allow_very_high": allow_very_high,
        "max_reviewer_calls": max_reviewer_calls,
        "max_very_high_calls": max_very_high_calls,
    }
