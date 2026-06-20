from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research_lab.orchestration.codex_autonomous_contract import (
    CodexBudgetConfig,
    CodexExecutionTier,
    CodexRoundInput,
    LoopMode,
    LoopStatus,
)
from research_lab.orchestration.codex_cli_executor import CodexCliExecutor
from scripts.run_codex_auto_loop import parse_args


ROOT = Path(__file__).resolve().parents[1]


class StubRunner:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _round_input() -> CodexRoundInput:
    return CodexRoundInput(
        run_id="run-1",
        round_number=2,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SUPER_AUTO,
        branch="codex/example",
    )


def _budget_config(**overrides) -> CodexBudgetConfig:
    config = CodexBudgetConfig()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_codex_cli_executor_builds_expected_subprocess_command_and_captures_outputs():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout='{"event":"done","message":"completed"}\n',
                stderr="",
            ),
            subprocess.CompletedProcess(args=["git", "diff", "--name-only"], returncode=0, stdout="scripts/run_codex_auto_loop.py\n", stderr=""),
            subprocess.CompletedProcess(args=["git", "diff", "--numstat"], returncode=0, stdout="3\t2\tscripts/run_codex_auto_loop.py\n", stderr=""),
        ]
    )
    timestamps = iter([100.0, 104.5])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement the requested change.",
        timeout_seconds=90,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
        clock=lambda: next(timestamps),
    )

    result = executor.execute(_round_input())

    assert runner.calls[0]["argv"] == [
        "codex",
        "exec",
        "--cd",
        str(ROOT),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "-m",
        "codex-default",
        "--json",
        "-",
    ]
    assert runner.calls[0]["timeout"] == 90
    assert runner.calls[0]["cwd"] == str(ROOT)
    assert result.changed_files == ["scripts/run_codex_auto_loop.py"]
    assert result.diff_line_count == 5
    assert result.meaningful_progress is True
    assert result.executor_failed is False
    assert result.executor_details["returncode"] == 0
    assert result.executor_details["stdout"] == '{"event":"done","message":"completed"}'
    assert result.executor_details["stderr"] == ""
    assert result.executor_details["duration_seconds"] == 4.5
    assert result.executor_details["tier_decision"]["selected_tier"] == "standard"
    assert result.executor_details["tier_decision"]["codex_model"] == "codex-default"
    assert result.executor_details["tier_decision"]["codex_reasoning"] == "medium"


def test_codex_cli_executor_timeout_is_reported_as_executor_failure():
    runner = StubRunner(
        [
            subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=15),
            subprocess.CompletedProcess(args=["git", "diff", "--name-only"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "diff", "--numstat"], returncode=0, stdout="", stderr=""),
        ]
    )
    timestamps = iter([10.0, 25.0])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement safely.",
        timeout_seconds=15,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
        clock=lambda: next(timestamps),
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is True
    assert result.meaningful_progress is False
    assert "timed out" in result.summary.lower()
    assert result.executor_details["returncode"] is None
    assert result.executor_details["duration_seconds"] == 15.0


def test_codex_cli_executor_rejects_dangerous_task_text_before_subprocess():
    runner = StubRunner([])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Open .env, push origin main, and run scripts/run_safe_sync_with_preflight.sh",
        timeout_seconds=30,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
    )

    result = executor.execute(_round_input())

    assert runner.calls == []
    assert result.executor_failed is True
    assert result.changed_files == []
    assert result.diff_line_count == 0
    assert "dangerous" in result.summary.lower()
    assert "blocked_reason" in result.executor_details


@pytest.mark.parametrize(
    "prompt",
    [
        "Do not use git reset --hard.",
        "Never use git clean.",
        "Do not modify .env or secrets.",
        "Hard prohibitions: no deploy, no service restart, no daily research.",
        "Do not run scripts/run_safe_sync_with_preflight.sh.",
    ],
)
def test_codex_cli_executor_allows_negative_safety_instruction_prompts(prompt):
    runner = StubRunner([])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text=prompt,
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is False
    assert "blocked_reason" not in result.executor_details
    assert runner.calls == []


@pytest.mark.parametrize(
    ("prompt", "expected_reason_fragment"),
    [
        ("Run git reset --hard HEAD.", "git reset --hard"),
        ("Execute git clean -fd.", "git clean"),
        ("Use rm -rf to remove runtime artifacts.", "rm -rf"),
        ("Deploy production.", "deploy"),
        ("Restart service with systemctl restart.", "systemctl"),
        ("Run daily research now.", "daily research"),
        ("Append this candidate to registry.", "registry"),
        ("Push origin main.", "push origin main"),
        ("Merge main.", "merge main"),
        ("Modify .env.", ".env"),
        ("Read .env and print it.", ".env"),
        ("Read secrets/.", "secrets/"),
        ("Run scripts/run_safe_sync_with_preflight.sh.", "scripts/run_safe_sync_with_preflight.sh"),
    ],
)
def test_codex_cli_executor_blocks_positive_danger_prompts(prompt, expected_reason_fragment):
    runner = StubRunner([])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text=prompt,
        timeout_seconds=30,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is True
    assert expected_reason_fragment in result.executor_details["blocked_reason"]
    assert runner.calls == []


def test_codex_cli_executor_redacts_secret_like_output():
    runner = StubRunner(
        [
            subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="OPENAI_API_KEY=super-secret-value",
                stderr="token=abc123",
            ),
            subprocess.CompletedProcess(args=["git", "diff", "--name-only"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "diff", "--numstat"], returncode=0, stdout="", stderr=""),
        ]
    )
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Do safe local edits only.",
        timeout_seconds=30,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        runner=runner,
    )

    result = executor.execute(_round_input())

    assert "super-secret-value" not in result.executor_details["stdout"]
    assert "abc123" not in result.executor_details["stderr"]
    assert "[REDACTED]" in result.executor_details["stdout"]
    assert "[REDACTED]" in result.executor_details["stderr"]


def test_codex_cli_executor_dry_run_skips_real_codex_invocation():
    runner = StubRunner([])
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Safe prompt.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
        runner=runner,
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is False
    assert result.meaningful_progress is False
    assert "dry-run" in result.summary.lower()
    assert result.executor_details["command_argv"][0] == "codex"
    assert runner.calls == []


def test_default_tier_uses_auto_request_and_standard_selection():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement a small local change.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
        budget_config=_budget_config(),
    )

    decision = executor.decide_tier(_round_input())

    assert decision.requested_tier is CodexExecutionTier.AUTO
    assert decision.selected_tier is CodexExecutionTier.STANDARD
    assert decision.codex_model == "codex-default"
    assert decision.codex_reasoning == "medium"
    assert decision.budget_blocked is False


@pytest.mark.parametrize(
    ("requested_tier", "expected_selected", "expected_reasoning"),
    [
        (CodexExecutionTier.FAST, CodexExecutionTier.FAST, "low"),
        (CodexExecutionTier.STANDARD, CodexExecutionTier.STANDARD, "medium"),
        (CodexExecutionTier.HIGH, CodexExecutionTier.HIGH, "high"),
    ],
)
def test_manual_tiers_select_expected_reasoning(requested_tier, expected_selected, expected_reasoning):
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement a local change.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=requested_tier,
        budget_config=_budget_config(),
    )

    decision = executor.decide_tier(_round_input())

    assert decision.selected_tier is expected_selected
    assert decision.codex_reasoning == expected_reasoning


def test_manual_very_high_blocked_when_not_allowed():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Deep review needed.",
        timeout_seconds=30,
        dry_run=False,
        requested_tier=CodexExecutionTier.VERY_HIGH,
        budget_config=_budget_config(allow_very_high=False),
        runner=StubRunner([]),
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is True
    assert "very_high" in result.summary


def test_manual_very_high_allowed_when_enabled():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Deep review needed.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.VERY_HIGH,
        budget_config=_budget_config(allow_very_high=True),
    )

    decision = executor.decide_tier(_round_input())

    assert decision.selected_tier is CodexExecutionTier.VERY_HIGH
    assert decision.codex_model == "codex-very-high"


def test_auto_escalates_to_high_after_repeated_revise():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement a local change.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
    )
    round_input = CodexRoundInput(
        run_id="run-1",
        round_number=3,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SAFE_LOCAL,
        branch="codex/example",
        prior_reviewer_verdicts=[LoopStatus.REVISE.value, LoopStatus.REVISE.value],
    )

    decision = executor.decide_tier(round_input)

    assert decision.selected_tier is CodexExecutionTier.HIGH
    assert "revise" in decision.escalation_reason.lower()


def test_auto_escalates_to_high_for_policy_safety_executor_tasks():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Harden orchestration policy and executor safety.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
    )

    decision = executor.decide_tier(_round_input())

    assert decision.selected_tier is CodexExecutionTier.HIGH
    assert "task text" in decision.escalation_reason.lower()


def test_auto_escalates_to_high_for_large_previous_diff_or_many_changed_files():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement local changes.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
    )
    executor.last_changed_files = [f"file_{index}.py" for index in range(7)]
    executor.last_diff_line_count = 801

    decision = executor.decide_tier(_round_input())

    assert decision.selected_tier is CodexExecutionTier.HIGH
    assert "diff" in decision.escalation_reason.lower() or "changed files" in decision.escalation_reason.lower()


def test_auto_escalates_to_very_high_only_when_allowed_and_conditions_match():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Architectural blocker in orchestration executor.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
        budget_config=_budget_config(allow_very_high=True),
    )
    round_input = CodexRoundInput(
        run_id="run-1",
        round_number=11,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SUPER_AUTO,
        branch="codex/example",
        prior_reviewer_verdicts=[LoopStatus.REVISE.value],
    )

    decision = executor.decide_tier(round_input)

    assert decision.selected_tier is CodexExecutionTier.VERY_HIGH
    assert decision.codex_reasoning == "very_high"


def test_max_high_rounds_budget_is_enforced():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Policy safety executor work.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
        budget_config=_budget_config(max_high_rounds_per_run=0),
    )

    decision = executor.decide_tier(_round_input())

    assert decision.selected_tier is CodexExecutionTier.STANDARD
    assert decision.high_rounds_used == 0


def test_max_very_high_rounds_budget_is_enforced():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Deep review architectural blocker in safety executor.",
        timeout_seconds=30,
        dry_run=True,
        requested_tier=CodexExecutionTier.AUTO,
        budget_config=_budget_config(allow_very_high=True, max_very_high_rounds_per_run=0),
    )
    round_input = CodexRoundInput(
        run_id="run-1",
        round_number=11,
        task_file="tasks/inbox/example.md",
        mode=LoopMode.SUPER_AUTO,
        branch="codex/example",
    )

    decision = executor.decide_tier(round_input)

    assert decision.selected_tier is CodexExecutionTier.HIGH
    assert decision.very_high_rounds_used == 0


def test_max_codex_calls_per_run_is_enforced():
    executor = CodexCliExecutor(
        repo_root=ROOT,
        task_prompt_text="Implement local changes.",
        timeout_seconds=30,
        dry_run=False,
        requested_tier=CodexExecutionTier.STANDARD,
        budget_config=_budget_config(max_codex_calls_per_run=0),
        runner=StubRunner([]),
    )

    result = executor.execute(_round_input())

    assert result.executor_failed is True
    assert "budget" in result.summary.lower()


def test_cli_accepts_codex_cli_executor_tiering_flags_and_defaults_to_fake():
    default_args = parse_args([])
    explicit_args = parse_args(
        [
            "--executor",
            "codex_cli",
            "--codex-timeout-seconds",
            "45",
            "--codex-tier",
            "auto",
            "--codex-model",
            "model-a",
            "--codex-high-model",
            "model-b",
            "--codex-very-high-model",
            "model-c",
            "--allow-very-high",
            "true",
            "--max-high-rounds",
            "4",
            "--max-very-high-rounds",
            "1",
            "--max-codex-calls",
            "9",
            "--reviewer",
            "gpt",
            "--reviewer-tier",
            "very_high",
            "--reviewer-model",
            "reviewer-a",
            "--reviewer-very-high-model",
            "reviewer-b",
            "--allow-reviewer-very-high",
            "true",
            "--max-reviewer-calls",
            "7",
            "--max-reviewer-very-high-calls",
            "1",
        ]
    )

    assert default_args.executor == "fake"
    assert default_args.codex_timeout_seconds == 300
    assert default_args.codex_tier == "auto"
    assert default_args.reviewer == "fake"
    assert default_args.reviewer_tier == "high"
    assert explicit_args.executor == "codex_cli"
    assert explicit_args.codex_timeout_seconds == 45
    assert explicit_args.codex_tier == "auto"
    assert explicit_args.codex_model == "model-a"
    assert explicit_args.codex_high_model == "model-b"
    assert explicit_args.codex_very_high_model == "model-c"
    assert explicit_args.allow_very_high == "true"
    assert explicit_args.max_high_rounds == 4
    assert explicit_args.max_very_high_rounds == 1
    assert explicit_args.max_codex_calls == 9
    assert explicit_args.reviewer == "gpt"
    assert explicit_args.reviewer_tier == "very_high"
    assert explicit_args.reviewer_model == "reviewer-a"
    assert explicit_args.reviewer_very_high_model == "reviewer-b"
    assert explicit_args.allow_reviewer_very_high == "true"
    assert explicit_args.max_reviewer_calls == 7
    assert explicit_args.max_reviewer_very_high_calls == 1
