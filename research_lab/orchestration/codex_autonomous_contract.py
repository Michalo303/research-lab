from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol
import uuid


class LoopStatus(str, Enum):
    PASS = "PASS"
    REVISE = "REVISE"
    BLOCKED = "BLOCKED"
    UNSAFE = "UNSAFE"


class LoopMode(str, Enum):
    DRY_RUN = "dry_run"
    SAFE_LOCAL = "safe_local"
    AUTO_PR = "auto_pr"
    SUPER_AUTO = "super_auto"


class CodexExecutionTier(str, Enum):
    AUTO = "auto"
    FAST = "fast"
    STANDARD = "standard"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ReviewerModelTier(str, Enum):
    HIGH = "high"
    VERY_HIGH = "very_high"


DEFAULT_ALLOWED_PATHS = [
    ".gitignore",
    "research_lab/",
    "scripts/",
    "tests/",
    "tasks/",
    "codex_runs/",
]

DEFAULT_PROTECTED_PATHS = [
    ".env",
    "secrets/",
    "registry/",
    "reports/",
    "leaderboard/",
    "cache/",
    "data/",
    "logs/",
]

DEFAULT_FORBIDDEN_COMMAND_FRAGMENTS = [
    "git reset --hard",
    "git clean",
    "rm -rf",
    "systemctl",
    "service restart",
    "deploy",
    "daily research",
    "run_daily",
    "registry append",
    "push origin main",
    "merge main",
    "scripts/run_safe_sync_with_preflight.sh",
]


@dataclass
class CodexLoopConfig:
    mode: LoopMode
    max_rounds: int
    max_runtime_minutes: int
    max_changed_files: int
    max_diff_lines: int
    max_test_retries: int
    no_progress_round_limit: int
    allowed_paths: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_PATHS))
    protected_paths: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_PATHS))
    forbidden_command_fragments: list[str] = field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_COMMAND_FRAGMENTS)
    )
    targeted_tests: list[str] = field(default_factory=list)
    dry_run_external_calls: bool = True

    @classmethod
    def for_mode(cls, mode: LoopMode) -> "CodexLoopConfig":
        defaults: dict[LoopMode, dict[str, int]] = {
            LoopMode.DRY_RUN: {
                "max_rounds": 3,
                "max_runtime_minutes": 30,
                "max_changed_files": 10,
                "max_diff_lines": 1000,
                "max_test_retries": 3,
                "no_progress_round_limit": 2,
            },
            LoopMode.SAFE_LOCAL: {
                "max_rounds": 5,
                "max_runtime_minutes": 60,
                "max_changed_files": 10,
                "max_diff_lines": 1000,
                "max_test_retries": 5,
                "no_progress_round_limit": 2,
            },
            LoopMode.AUTO_PR: {
                "max_rounds": 5,
                "max_runtime_minutes": 60,
                "max_changed_files": 15,
                "max_diff_lines": 1500,
                "max_test_retries": 5,
                "no_progress_round_limit": 2,
            },
            LoopMode.SUPER_AUTO: {
                "max_rounds": 20,
                "max_runtime_minutes": 120,
                "max_changed_files": 20,
                "max_diff_lines": 3000,
                "max_test_retries": 10,
                "no_progress_round_limit": 3,
            },
        }
        return cls(mode=mode, **defaults[mode])


@dataclass
class CodexBudgetConfig:
    max_codex_calls_per_run: int = 20
    max_high_rounds_per_run: int = 6
    max_very_high_rounds_per_run: int = 1
    allow_very_high: bool = False
    default_tier: CodexExecutionTier = CodexExecutionTier.STANDARD
    default_model: str = "codex-default"
    high_model: str = "codex-high"
    very_high_model: str = "codex-very-high"


@dataclass
class CodexTierDecision:
    requested_tier: CodexExecutionTier
    selected_tier: CodexExecutionTier
    codex_model: str
    codex_reasoning: str
    escalation_reason: str
    high_rounds_used: int
    very_high_rounds_used: int
    max_high_rounds: int
    max_very_high_rounds: int
    budget_blocked: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["requested_tier"] = self.requested_tier.value
        payload["selected_tier"] = self.selected_tier.value
        return payload


@dataclass
class ReviewerBudgetConfig:
    max_reviewer_calls_per_run: int = 20
    max_very_high_calls_per_run: int = 1
    default_model: str = "gpt-reviewer-high"
    high_model: str = "gpt-reviewer-high"
    very_high_model: str = "gpt-reviewer-very-high"
    allow_very_high: bool = False


@dataclass
class CodexRoundInput:
    run_id: str
    round_number: int
    task_file: str
    mode: LoopMode
    branch: str
    prior_reviewer_verdicts: list[str] = field(default_factory=list)


@dataclass
class CodexRoundResult:
    changed_files: list[str]
    diff_line_count: int
    proposed_commands: list[str] = field(default_factory=list)
    summary: str = ""
    patch_digest: str = ""
    meaningful_progress: bool = True
    executor_failed: bool = False
    executor_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewerRequest:
    run_id: str
    round_number: int
    mode: LoopMode
    task_text: str
    changed_files: list[str]
    diff_line_count: int
    validation_summary: dict[str, Any]
    policy_summary: dict[str, Any]
    codex_summary: str
    codex_executor_details: dict[str, Any]
    previous_reviewer_verdicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        return payload


@dataclass
class ReviewerResponse:
    verdict: LoopStatus
    confidence: float
    reason: str
    required_changes: list[str]
    safety_notes: list[str]
    escalation_recommended: bool
    selected_model: str
    selected_tier: ReviewerModelTier
    budget_blocked: bool
    raw_response_redacted: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        payload["selected_tier"] = self.selected_tier.value
        return payload


@dataclass
class ReviewVerdict:
    status: LoopStatus
    summary: str = ""
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass
class ValidationResult:
    success: bool
    tests_requested: list[str] = field(default_factory=list)
    tests_passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GitActionResult:
    git_action_provider: str = "fake"
    git_action_live_enabled: bool = False
    git_action_attempted: bool = False
    commit_attempted: bool = False
    commit_created: bool = False
    commit_sha: str | None = None
    push_attempted: bool = False
    push_completed: bool = False
    pr_attempted: bool = False
    pr_created: bool = False
    pr_number: int | None = None
    pr_url: str | None = None
    pr_title: str | None = None
    pr_base_branch: str | None = None
    pr_head_branch: str | None = None
    merge_attempted: bool = False
    merge_blocked: bool = True
    deploy_blocked: bool = True
    registry_append_blocked: bool = True
    git_action_blocked_reason: str | None = None
    branch: str | None = None
    staged_files: list[str] = field(default_factory=list)
    planned_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GitActionRequest:
    mode: LoopMode
    branch: str
    changed_files: list[str]
    diff_line_count: int
    reviewer_status: LoopStatus
    validation_success: bool
    policy_status: str
    protected_paths_touched: list[str] = field(default_factory=list)
    disallowed_paths_touched: list[str] = field(default_factory=list)
    max_changed_files: int = 0
    max_diff_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["reviewer_status"] = self.reviewer_status.value
        return payload


AUDIT_REQUIRED_KEYS = [
    "run_id",
    "mode",
    "task_file",
    "branch",
    "final_status",
    "rounds_used",
    "max_rounds",
    "no_progress_rounds",
    "changed_files",
    "diff_line_count",
    "tests_requested",
    "tests_passed",
    "reviewer_verdicts",
    "protected_paths_touched",
    "forbidden_commands_detected",
    "commit_attempted",
    "commit_created",
    "commit_sha",
    "push_attempted",
    "push_completed",
    "pr_attempted",
    "pr_created",
    "pr_number",
    "pr_url",
    "pr_title",
    "pr_base_branch",
    "pr_head_branch",
    "merge_attempted",
    "merge_blocked",
    "git_action_provider",
    "git_action_live_enabled",
    "git_action_attempted",
    "git_action_blocked_reason",
    "staged_files",
    "deploy_attempted",
    "hertzner_sync_attempted",
    "hertzner_sync_completed",
    "registry_append_attempted",
    "dry_run_external_calls",
    "final_human_action_required",
    "reviewer_selected_model",
    "reviewer_selected_tier",
    "reviewer_call_count",
    "reviewer_budget_blocked",
    "reviewer_redaction_notes",
    "reviewer_provider_metadata",
    "reviewer_preflight",
]


@dataclass
class CodexLoopAudit:
    run_id: str
    mode: LoopMode
    task_file: str
    branch: str
    final_status: LoopStatus
    rounds_used: int
    max_rounds: int
    no_progress_rounds: int
    changed_files: list[str]
    diff_line_count: int
    tests_requested: list[str]
    tests_passed: bool
    reviewer_verdicts: list[str]
    protected_paths_touched: list[str]
    forbidden_commands_detected: list[str]
    commit_attempted: bool
    commit_created: bool
    commit_sha: str | None
    push_attempted: bool
    push_completed: bool
    pr_attempted: bool
    pr_created: bool
    pr_number: int | None
    pr_url: str | None
    pr_title: str | None
    pr_base_branch: str | None
    pr_head_branch: str | None
    merge_attempted: bool
    merge_blocked: bool
    deploy_attempted: bool
    hertzner_sync_attempted: bool
    hertzner_sync_completed: bool
    registry_append_attempted: bool
    dry_run_external_calls: bool
    final_human_action_required: bool
    git_action_provider: str = "fake"
    git_action_live_enabled: bool = False
    git_action_attempted: bool = False
    git_action_blocked_reason: str | None = None
    staged_files: list[str] = field(default_factory=list)
    reviewer_selected_model: str | None = None
    reviewer_selected_tier: str | None = None
    reviewer_call_count: int = 0
    reviewer_budget_blocked: bool = False
    reviewer_redaction_notes: list[str] = field(default_factory=list)
    reviewer_provider_metadata: dict[str, Any] = field(default_factory=dict)
    reviewer_preflight: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["final_status"] = self.final_status.value
        return payload


class CodexExecutorInterface(Protocol):
    def execute(self, round_input: CodexRoundInput) -> CodexRoundResult:
        ...


class ReviewerInterface(Protocol):
    def review(
        self,
        round_input: CodexRoundInput,
        round_result: CodexRoundResult,
        *,
        validation_result: ValidationResult | None = None,
        policy_summary: dict[str, Any] | None = None,
    ) -> ReviewVerdict:
        ...


class ValidationRunnerInterface(Protocol):
    def run_validation(self, round_input: CodexRoundInput, round_result: CodexRoundResult) -> ValidationResult:
        ...


class GitActionInterface(Protocol):
    def plan_after_pass(self, request: GitActionRequest) -> GitActionResult:
        ...


def new_run_id() -> str:
    return f"codex-auto-{uuid.uuid4().hex[:12]}"
