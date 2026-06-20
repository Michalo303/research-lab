from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_lab.orchestration.codex_autonomous_contract import (
    CodexBudgetConfig,
    CodexExecutionTier,
    CodexLoopConfig,
    LoopMode,
    ReviewerBudgetConfig,
    ReviewerModelTier,
)
from research_lab.orchestration.codex_cli_executor import CodexCliExecutor
from research_lab.orchestration.codex_autonomous_loop import (
    CodexAutonomousLoop,
    FakeCodexExecutor,
    FakeGitAction,
    FakeReviewer,
    FakeValidationRunner,
)
from research_lab.orchestration.gpt_reviewer_adapter import (
    DisabledReviewerProvider,
    GptReviewerAdapter,
)
from research_lab.orchestration.openai_reviewer_provider import OpenAICompatibleReviewerProvider
from research_lab.orchestration.reviewer_provider_config import (
    ReviewerProviderConfig,
    reviewer_provider_preflight,
)

TASKS_INBOX = ROOT / "tasks" / "inbox"
RUNS_DIR = ROOT / "codex_runs"
REVIEW_ONLY_MODE = "review_only"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Codex autonomous supervisor skeleton.")
    parser.add_argument("--task-file")
    parser.add_argument("--executor", choices=["fake", "codex_cli"], default="fake")
    parser.add_argument("--codex-timeout-seconds", type=int, default=300)
    parser.add_argument("--reviewer", choices=["fake", "gpt"], default="fake")
    parser.add_argument("--reviewer-provider", choices=["disabled", "openai-compatible"], default="disabled")
    parser.add_argument(
        "--reviewer-tier",
        choices=[tier.value for tier in ReviewerModelTier],
        default=ReviewerModelTier.HIGH.value,
    )
    parser.add_argument("--reviewer-model", default="gpt-reviewer-high")
    parser.add_argument("--reviewer-very-high-model", default="gpt-reviewer-very-high")
    parser.add_argument("--reviewer-api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--reviewer-base-url")
    parser.add_argument("--reviewer-timeout-seconds", type=int, default=60)
    parser.add_argument("--reviewer-max-output-tokens", type=int, default=1200)
    parser.add_argument("--enable-live-reviewer", choices=["true", "false"], default="false")
    parser.add_argument("--allow-reviewer-very-high", choices=["true", "false"], default="false")
    parser.add_argument("--max-reviewer-calls", type=int, default=20)
    parser.add_argument("--max-reviewer-very-high-calls", type=int, default=1)
    parser.add_argument(
        "--codex-tier",
        choices=[tier.value for tier in CodexExecutionTier],
        default=CodexExecutionTier.AUTO.value,
    )
    parser.add_argument("--codex-model", default="codex-default")
    parser.add_argument("--codex-high-model", default="codex-high")
    parser.add_argument("--codex-very-high-model", default="codex-very-high")
    parser.add_argument("--allow-very-high", choices=["true", "false"], default="false")
    parser.add_argument("--max-high-rounds", type=int, default=6)
    parser.add_argument("--max-very-high-rounds", type=int, default=1)
    parser.add_argument("--max-codex-calls", type=int, default=20)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in LoopMode] + [REVIEW_ONLY_MODE],
        default=LoopMode.DRY_RUN.value,
    )
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--max-runtime-minutes", type=int)
    parser.add_argument("--max-changed-files", type=int)
    parser.add_argument("--max-diff-lines", type=int)
    parser.add_argument("--targeted-tests", nargs="*")
    parser.add_argument("--dry-run-external-calls", choices=["true", "false"], default="true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    mode = LoopMode.DRY_RUN if args.mode == REVIEW_ONLY_MODE else LoopMode(args.mode)
    config = CodexLoopConfig.for_mode(mode)
    _apply_overrides(config, args)

    task_path, placeholder_used = _resolve_task_file(args.task_file)
    if placeholder_used:
        print("No task file found in tasks/inbox; using placeholder dry-run task.")
    task_prompt_text = _load_task_prompt_text(task_path, placeholder_used)

    loop = CodexAutonomousLoop(
        config=config,
        codex_executor=_build_executor(
            args.executor,
            config,
            task_prompt_text,
            args.codex_timeout_seconds,
            args,
        ),
        reviewer=_build_reviewer(args, config, task_prompt_text),
        validation_runner=FakeValidationRunner(),
        git_action=FakeGitAction(),
    )
    audit = loop.run(task_file=str(task_path.relative_to(ROOT)) if task_path.exists() else str(task_path))

    run_dir = RUNS_DIR / audit.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    audit_path = run_dir / "audit.json"
    report_path = run_dir / "final_report.md"
    audit_path.write_text(json.dumps(audit.to_dict(), indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_build_report(audit.to_dict()), encoding="utf-8")

    print("v1 dry-run only: no live Codex CLI, OpenAI API, git push, PR, merge, deploy, or Hetzner sync will run.")
    print(f"Final report: {report_path}")
    return 0


def _apply_overrides(config: CodexLoopConfig, args: argparse.Namespace) -> None:
    if args.max_rounds is not None:
        config.max_rounds = args.max_rounds
    if args.max_runtime_minutes is not None:
        config.max_runtime_minutes = args.max_runtime_minutes
    if args.max_changed_files is not None:
        config.max_changed_files = args.max_changed_files
    if args.max_diff_lines is not None:
        config.max_diff_lines = args.max_diff_lines
    if args.targeted_tests:
        config.targeted_tests = list(args.targeted_tests)
    config.dry_run_external_calls = args.dry_run_external_calls.lower() == "true"


def _build_executor(
    executor_name: str,
    config: CodexLoopConfig,
    task_prompt_text: str,
    codex_timeout_seconds: int,
    args: argparse.Namespace,
):
    if executor_name == "codex_cli":
        return CodexCliExecutor(
            repo_root=ROOT,
            task_prompt_text=task_prompt_text,
            timeout_seconds=codex_timeout_seconds,
            dry_run=config.dry_run_external_calls,
            requested_tier=CodexExecutionTier(args.codex_tier),
            budget_config=CodexBudgetConfig(
                max_codex_calls_per_run=args.max_codex_calls,
                max_high_rounds_per_run=args.max_high_rounds,
                max_very_high_rounds_per_run=args.max_very_high_rounds,
                allow_very_high=args.allow_very_high.lower() == "true",
                default_model=args.codex_model,
                high_model=args.codex_high_model,
                very_high_model=args.codex_very_high_model,
            ),
        )
    return FakeCodexExecutor()


def _build_reviewer(
    args: argparse.Namespace,
    config: CodexLoopConfig,
    task_prompt_text: str,
    provider_transport=None,
):
    if args.reviewer == "gpt":
        selected_tier = ReviewerModelTier(args.reviewer_tier)
        provider_config = ReviewerProviderConfig(
            api_key_env_var=args.reviewer_api_key_env,
            base_url=args.reviewer_base_url,
            model=args.reviewer_very_high_model if selected_tier is ReviewerModelTier.VERY_HIGH else args.reviewer_model,
            timeout_seconds=args.reviewer_timeout_seconds,
            max_output_tokens=args.reviewer_max_output_tokens,
        )
        live_enabled = args.enable_live_reviewer.lower() == "true"
        preflight = reviewer_provider_preflight(
            provider_name=args.reviewer_provider,
            live_reviewer_enabled=live_enabled,
            config=provider_config,
            selected_tier=selected_tier,
            allow_very_high=args.allow_reviewer_very_high.lower() == "true",
            max_reviewer_calls=args.max_reviewer_calls,
            max_very_high_calls=args.max_reviewer_very_high_calls,
        )
        if args.reviewer_provider == "openai-compatible" and live_enabled:
            provider = OpenAICompatibleReviewerProvider(
                config=provider_config,
                transport=provider_transport,
            )
        else:
            disabled_reason = "Live GPT reviewer provider is disabled."
            if args.reviewer_provider != "openai-compatible":
                disabled_reason = "Reviewer provider is disabled."
            provider = DisabledReviewerProvider(reason=disabled_reason)
        reviewer = GptReviewerAdapter(
            provider=provider,
            dry_run=config.dry_run_external_calls,
            task_text=task_prompt_text,
            requested_tier=selected_tier,
            budget_config=ReviewerBudgetConfig(
                max_reviewer_calls_per_run=args.max_reviewer_calls,
                max_very_high_calls_per_run=args.max_reviewer_very_high_calls,
                default_model=args.reviewer_model,
                high_model=args.reviewer_model,
                very_high_model=args.reviewer_very_high_model,
                allow_very_high=args.allow_reviewer_very_high.lower() == "true",
            ),
        )
        reviewer.reviewer_preflight = preflight
        return reviewer

    reviewer = FakeReviewer()
    reviewer.reviewer_preflight = reviewer_provider_preflight(
        provider_name="disabled",
        live_reviewer_enabled=False,
        config=ReviewerProviderConfig(),
        selected_tier=ReviewerModelTier.HIGH,
        allow_very_high=False,
        max_reviewer_calls=0,
        max_very_high_calls=0,
    )
    return reviewer


def _resolve_task_file(task_file: str | None) -> tuple[Path, bool]:
    if task_file:
        return Path(task_file).resolve(), False

    candidates = sorted(TASKS_INBOX.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0], False

    return TASKS_INBOX / "placeholder_dry_run_task.md", True


def _load_task_prompt_text(task_path: Path, placeholder_used: bool) -> str:
    if placeholder_used:
        return (
            "Placeholder dry-run task: no inbox task file was present. "
            "Operate within the repository safety policy and do not use destructive or production actions."
        )
    return task_path.read_text(encoding="utf-8")


def _build_report(audit: dict[str, object]) -> str:
    reviewer_preflight = audit.get("reviewer_preflight", {})
    reviewer_provider_metadata = audit.get("reviewer_provider_metadata", {})
    return (
        "# Codex Autonomous Loop v1 Report\n\n"
        f"- Status: `{audit['final_status']}`\n"
        f"- Mode: `{audit['mode']}`\n"
        f"- Task file: `{audit['task_file']}`\n"
        f"- Branch: `{audit['branch']}`\n"
        f"- Rounds used: `{audit['rounds_used']}/{audit['max_rounds']}`\n"
        f"- Tests requested: `{', '.join(audit['tests_requested']) if audit['tests_requested'] else 'none'}`\n"
        f"- Reviewer provider: `{reviewer_preflight.get('provider_selected', 'disabled')}`\n"
        f"- Live reviewer enabled: `{reviewer_preflight.get('live_reviewer_enabled', False)}`\n"
        f"- Reviewer credential present: `{reviewer_preflight.get('api_key_present', False)}`\n"
        f"- Reviewer live call attempted: `{reviewer_provider_metadata.get('live_call_attempted', False)}`\n"
        f"- Human action required: `{audit['final_human_action_required']}`\n\n"
        "This skeleton is dry-run only.\n"
        "Runtime artifact only; do not commit audit.json or final_report.md outputs.\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
