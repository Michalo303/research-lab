from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_lab.orchestration.codex_autonomous_contract import CodexLoopConfig, LoopMode
from research_lab.orchestration.codex_autonomous_loop import (
    CodexAutonomousLoop,
    FakeCodexExecutor,
    FakeGitAction,
    FakeReviewer,
    FakeValidationRunner,
)

TASKS_INBOX = ROOT / "tasks" / "inbox"
RUNS_DIR = ROOT / "codex_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Codex autonomous supervisor skeleton.")
    parser.add_argument("--task-file")
    parser.add_argument("--mode", choices=[mode.value for mode in LoopMode], default=LoopMode.DRY_RUN.value)
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--max-runtime-minutes", type=int)
    parser.add_argument("--max-changed-files", type=int)
    parser.add_argument("--max-diff-lines", type=int)
    parser.add_argument("--targeted-tests", nargs="*")
    parser.add_argument("--dry-run-external-calls", choices=["true", "false"], default="true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = LoopMode(args.mode)
    config = CodexLoopConfig.for_mode(mode)
    _apply_overrides(config, args)

    task_path, placeholder_used = _resolve_task_file(args.task_file)
    if placeholder_used:
        print("No task file found in tasks/inbox; using placeholder dry-run task.")

    loop = CodexAutonomousLoop(
        config=config,
        codex_executor=FakeCodexExecutor(),
        reviewer=FakeReviewer(),
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


def _resolve_task_file(task_file: str | None) -> tuple[Path, bool]:
    if task_file:
        return Path(task_file).resolve(), False

    candidates = sorted(TASKS_INBOX.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0], False

    return TASKS_INBOX / "placeholder_dry_run_task.md", True


def _build_report(audit: dict[str, object]) -> str:
    return (
        "# Codex Autonomous Loop v1 Report\n\n"
        f"- Status: `{audit['final_status']}`\n"
        f"- Mode: `{audit['mode']}`\n"
        f"- Task file: `{audit['task_file']}`\n"
        f"- Branch: `{audit['branch']}`\n"
        f"- Rounds used: `{audit['rounds_used']}/{audit['max_rounds']}`\n"
        f"- Tests requested: `{', '.join(audit['tests_requested']) if audit['tests_requested'] else 'none'}`\n"
        f"- Human action required: `{audit['final_human_action_required']}`\n\n"
        "This skeleton is dry-run only.\n"
        "Runtime artifact only; do not commit audit.json or final_report.md outputs.\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
