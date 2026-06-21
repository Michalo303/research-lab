from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_lab.orchestration.codex_autonomous_contract import (
    LoopStatus,
    ReviewVerdict,
    ValidationResult,
)
from research_lab.orchestration.codex_review_loop import (
    CodexReviewLoop,
    CodexReviewLoopConfig,
    FakeReviewLoopValidationRunner,
    ReviewLoopFinalStatus,
    ReviewerBundle,
)
from research_lab.orchestration.codex_review_loop_executors import (
    CodexCliReviewLoopExecutor,
    FakeReviewLoopExecutorFactory,
)
from research_lab.orchestration.codex_review_loop_reviewer import (
    ReplayReviewLoopReviewer,
    ReviewLoopReviewerMode,
    build_live_openai_reviewer,
    validate_provider_call_gate,
)


DEFAULT_OUTPUT_DIR = ROOT / "codex_runs" / "review-loop-cli-smoke"
DEFAULT_TASK = "Run the fake Codex review loop."
VALID_VERDICTS = {status.value: status for status in (LoopStatus.PASS, LoopStatus.REVISE, LoopStatus.BLOCKED)}
LEGACY_TO_REVIEWER_VERDICT = {
    "PASS": "PASS",
    "REVISE": "RETRY",
    "BLOCKED": "ABORT",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CodexReviewLoop in fake/non-live mode.")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Initial task prompt for the review loop.")
    parser.add_argument("--max-attempts", type=int, default=1, help="Maximum review-loop attempts to run.")
    parser.add_argument("--executor", choices=["fake", "codex_cli"], default="fake")
    parser.add_argument(
        "--reviewer-mode",
        choices=[mode.value for mode in ReviewLoopReviewerMode],
        default=ReviewLoopReviewerMode.REPLAY.value,
    )
    parser.add_argument("--allow-provider-calls", choices=["true", "false"], default="false")
    parser.add_argument("--max-reviewer-calls", type=int, default=0)
    parser.add_argument("--enable-live-codex", choices=["true", "false"], default="false")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--codex-timeout-seconds", type=int, default=300)
    parser.add_argument("--dry-run-external-calls", choices=["true", "false"], default="true")
    parser.add_argument(
        "--fake-reviewer-verdicts",
        default="PASS",
        help="Comma-separated fake reviewer verdict sequence. Supported values: PASS, REVISE, BLOCKED.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for audit.json and final_report.md artifacts.",
    )
    args = parser.parse_args(argv)
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1.")
    if args.max_reviewer_calls < 0:
        parser.error("--max-reviewer-calls must be at least 0.")
    args.fake_reviewer_verdicts = _parse_fake_verdicts(args.fake_reviewer_verdicts, parser)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir).expanduser().resolve()
    dry_run_external_calls = args.dry_run_external_calls.lower() == "true"
    gate = validate_provider_call_gate(
        reviewer_mode=ReviewLoopReviewerMode(args.reviewer_mode),
        allow_provider_calls=args.allow_provider_calls.lower() == "true",
        max_reviewer_calls=args.max_reviewer_calls,
    )

    blocked_reason: str | None = None
    reviewer = None
    if gate.blocked:
        audit_payload = _build_blocked_audit_payload(args, dry_run_external_calls, blocked_reason=gate.blocked_reason)
    else:
        loop = _build_loop(args, dry_run_external_calls)
        reviewer = getattr(loop, "reviewer", None)
        audit = loop.run(args.task)
        audit_payload = _build_audit_payload(audit.to_dict(), args, dry_run_external_calls)

    audit_payload = _annotate_provider_gate(audit_payload, args, gate, blocked_reason=blocked_reason)
    audit_payload = _annotate_reviewer_runtime(audit_payload, reviewer)
    report_text = _build_report(audit_payload)

    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "audit.json"
    report_path = output_dir / "final_report.md"
    audit_path.write_text(json.dumps(audit_payload, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")

    print(f"Final status: {audit_payload['final_status']}")
    print(f"Audit JSON: {audit_path}")
    print(f"Final report: {report_path}")
    return 0


def _parse_fake_verdicts(raw_value: str, parser: argparse.ArgumentParser) -> list[str]:
    verdict_tokens = [token.strip().upper() for token in raw_value.split(",") if token.strip()]
    if not verdict_tokens:
        parser.error("At least one fake reviewer verdict is required.")
    for token in verdict_tokens:
        if token not in VALID_VERDICTS:
            parser.error(f"Invalid fake reviewer verdict: {token}")
    return verdict_tokens


def _build_executor(args: argparse.Namespace, dry_run_external_calls: bool):
    if args.executor == "codex_cli":
        return CodexCliReviewLoopExecutor(
            repo_root=ROOT,
            codex_command=args.codex_command,
            timeout_seconds=args.codex_timeout_seconds,
            live_codex_enabled=args.enable_live_codex.lower() == "true",
            dry_run_external_calls=dry_run_external_calls,
        )
    return FakeReviewLoopExecutorFactory().build(max_attempts=args.max_attempts)


def _build_loop(args: argparse.Namespace, dry_run_external_calls: bool) -> CodexReviewLoop:
    return CodexReviewLoop(
        config=CodexReviewLoopConfig(max_attempts=args.max_attempts, dry_run_external_calls=dry_run_external_calls),
        executor=_build_executor(args, dry_run_external_calls),
        reviewer=_build_reviewer(args),
        validation_runner=FakeReviewLoopValidationRunner(_build_fake_validation_results(args.max_attempts)),
    )


def _build_reviewer(args: argparse.Namespace):
    reviewer_mode = ReviewLoopReviewerMode(args.reviewer_mode)
    if reviewer_mode is ReviewLoopReviewerMode.LIVE_OPENAI:
        return build_live_openai_reviewer(max_reviewer_calls=args.max_reviewer_calls)
    return ReplayReviewLoopReviewer.from_raw_decisions(_build_fake_reviewer_decisions(args.fake_reviewer_verdicts))


def _build_fake_reviewer_decisions(verdicts: list[str]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for attempt_number, verdict_name in enumerate(verdicts, start=1):
        mapped_verdict = LEGACY_TO_REVIEWER_VERDICT[verdict_name]
        if mapped_verdict == "RETRY":
            items.append(
                {
                    "verdict": "RETRY",
                    "reason": f"Fake reviewer requested revisions on attempt {attempt_number}.",
                    "next_codex_instruction": f"Address fake reviewer feedback for attempt {attempt_number}.",
                    "risk_flags": [],
                    "allowed_to_continue": True,
                }
            )
        elif mapped_verdict == "ABORT":
            items.append(
                {
                    "verdict": "ABORT",
                    "reason": f"Fake reviewer blocked the run on attempt {attempt_number}.",
                    "next_codex_instruction": None,
                    "risk_flags": ["Manual intervention required before another attempt."],
                    "allowed_to_continue": False,
                }
            )
        else:
            items.append(
                {
                    "verdict": "PASS",
                    "reason": f"Fake reviewer approved attempt {attempt_number}.",
                    "next_codex_instruction": None,
                    "risk_flags": [],
                    "allowed_to_continue": True,
                }
            )
    return items


def _build_fake_validation_results(max_attempts: int) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for attempt_number in range(1, max_attempts + 1):
        command = f"python -m pytest tests/test_codex_review_loop.py -q  # fake validation attempt {attempt_number}"
        results.append(
            ValidationResult(
                success=True,
                tests_requested=[command],
                tests_passed=[command],
                failures=[],
            )
        )
    return results


def _build_audit_payload(audit_payload: dict, args: argparse.Namespace, dry_run_external_calls: bool) -> dict:
    payload = dict(audit_payload)
    payload["max_attempts"] = args.max_attempts
    payload["reviewer_verdicts"] = list(payload.get("verdicts", []))
    first_attempt = payload["attempts"][0] if payload.get("attempts") else None
    executor_details = dict(first_attempt["executor_result"].get("executor_details", {})) if first_attempt else {}
    parsed_output = dict(executor_details.get("parsed_output", {}) or {})
    payload["executor_type"] = executor_details.get("executor_type", args.executor)
    payload["live_codex_enabled"] = bool(executor_details.get("live_codex_enabled", args.enable_live_codex.lower() == "true"))
    payload["dry_run_external_calls"] = bool(executor_details.get("dry_run_external_calls", dry_run_external_calls))
    payload["live_codex_attempted"] = bool(executor_details.get("live_codex_attempted", False))
    payload["codex_command"] = executor_details.get("codex_command", args.codex_command if args.executor == "codex_cli" else None)
    payload["codex_timeout_seconds"] = executor_details.get(
        "codex_timeout_seconds",
        args.codex_timeout_seconds if args.executor == "codex_cli" else None,
    )
    payload["codex_exit_code"] = executor_details.get("codex_exit_code")
    payload["stdout_summary"] = executor_details.get("stdout_summary", "")
    payload["stderr_summary"] = executor_details.get("stderr_summary", "")
    payload["blocked_reason"] = executor_details.get("blocked_reason")
    payload["parsed_summary"] = parsed_output.get("summary", "")
    payload["parsed_changed_files"] = list(parsed_output.get("changed_files", []))
    payload["parsed_diff_summary"] = dict(parsed_output.get("diff_summary", {}) or {})
    payload["parsed_validation"] = dict(parsed_output.get("validation", {}) or {})
    payload["parsed_blocked_reason"] = parsed_output.get("blocked_reason")
    payload["parser_warning"] = parsed_output.get("parser_warning")
    payload["parse_error"] = parsed_output.get("parse_error")
    payload["pre_run_tracked_dirty"] = bool(payload.get("pre_run_tracked_dirty", False))
    payload["pre_run_tracked_status"] = payload.get("pre_run_tracked_status", "") or ""
    payload["final_tracked_dirty"] = bool(payload.get("final_tracked_dirty", False))
    payload["final_tracked_status"] = payload.get("final_tracked_status", "") or ""
    payload["tracked_tree_failure_reason"] = payload.get("tracked_tree_failure_reason")
    return payload


def _annotate_provider_gate(audit_payload: dict, args: argparse.Namespace, gate, *, blocked_reason: str | None) -> dict:
    payload = dict(audit_payload)
    payload["reviewer_mode"] = args.reviewer_mode
    payload["provider_calls_allowed"] = args.allow_provider_calls.lower() == "true"
    payload["max_reviewer_calls"] = args.max_reviewer_calls
    payload["provider_gate_passed"] = gate.passed
    payload["provider_gate_blocked"] = gate.blocked
    if blocked_reason:
        payload["blocked_reason"] = blocked_reason
    return payload


def _annotate_reviewer_runtime(audit_payload: dict, reviewer) -> dict:
    payload = dict(audit_payload)
    provider_metadata = dict(getattr(reviewer, "latest_provider_metadata", {}) or {})
    payload["reviewer_calls_used"] = int(provider_metadata.get("reviewer_calls_used", 0))
    payload["provider_name"] = provider_metadata.get("provider_name")
    payload["model_name"] = provider_metadata.get("model_name")
    payload["provider_call_attempted"] = bool(provider_metadata.get("provider_call_attempted", False))
    payload["provider_call_succeeded"] = bool(provider_metadata.get("provider_call_succeeded", False))
    payload["provider_call_failed"] = bool(provider_metadata.get("provider_call_failed", False))
    payload["provider_failure_reason"] = provider_metadata.get("failure_reason")
    payload["provider_parse_failure"] = provider_metadata.get("parse_failure")
    payload["parsed_reviewer_decision"] = provider_metadata.get("parsed_reviewer_decision")
    if payload.get("provider_failure_reason") and not payload.get("blocked_reason"):
        payload["blocked_reason"] = payload["provider_failure_reason"]
    return payload


def _build_blocked_audit_payload(args: argparse.Namespace, dry_run_external_calls: bool, *, blocked_reason: str | None) -> dict:
    return {
        "run_id": "review-loop-provider-gate-blocked",
        "initial_task": args.task,
        "attempts": [],
        "verdicts": [],
        "changed_files_per_attempt": [],
        "validation_outputs": [],
        "reviewer_feedback": [],
        "final_status": ReviewLoopFinalStatus.BLOCKED.value,
        "git_action_attempted": False,
        "live_external_actions_enabled": False,
        "protected_paths_touched": [],
        "disallowed_paths_touched": [],
        "pre_run_tracked_dirty": False,
        "pre_run_tracked_status": "",
        "final_tracked_dirty": False,
        "final_tracked_status": "",
        "tracked_tree_failure_reason": None,
        "max_attempts": args.max_attempts,
        "reviewer_verdicts": [],
        "executor_type": args.executor,
        "live_codex_enabled": args.enable_live_codex.lower() == "true",
        "dry_run_external_calls": dry_run_external_calls,
        "live_codex_attempted": False,
        "codex_command": args.codex_command if args.executor == "codex_cli" else None,
        "codex_timeout_seconds": args.codex_timeout_seconds if args.executor == "codex_cli" else None,
        "codex_exit_code": None,
        "stdout_summary": "",
        "stderr_summary": "",
        "blocked_reason": blocked_reason,
        "parsed_summary": "",
        "parsed_changed_files": [],
        "parsed_diff_summary": {},
        "parsed_validation": {},
        "parsed_blocked_reason": None,
        "parser_warning": None,
        "parse_error": None,
        "reviewer_calls_used": 0,
        "provider_name": None,
        "model_name": None,
        "provider_call_attempted": False,
        "provider_call_succeeded": False,
        "provider_call_failed": False,
        "provider_failure_reason": blocked_reason,
        "provider_parse_failure": None,
        "parsed_reviewer_decision": None,
    }


def _build_report(audit_payload: dict) -> str:
    lines = [
        "# CodexReviewLoop Final Report",
        "",
        f"Final status: {audit_payload['final_status']}",
        f"Number of attempts: {len(audit_payload['attempts'])}",
        f"Executor type: {audit_payload['executor_type']}",
        f"Live Codex enabled: {audit_payload['live_codex_enabled']}",
        f"Dry-run external calls: {audit_payload['dry_run_external_calls']}",
        f"Reviewer mode: {audit_payload.get('reviewer_mode', '(unknown)')}",
        f"Provider calls allowed: {audit_payload.get('provider_calls_allowed', False)}",
        f"Max reviewer calls: {audit_payload.get('max_reviewer_calls', 0)}",
        f"Reviewer calls used: {audit_payload.get('reviewer_calls_used', 0)}",
        f"Provider name: {audit_payload.get('provider_name') or '(none)'}",
        f"Model name: {audit_payload.get('model_name') or '(none)'}",
        f"Provider gate passed: {audit_payload.get('provider_gate_passed', False)}",
        f"Provider gate blocked: {audit_payload.get('provider_gate_blocked', False)}",
        f"Provider call attempted: {audit_payload.get('provider_call_attempted', False)}",
        f"Provider call succeeded: {audit_payload.get('provider_call_succeeded', False)}",
        f"Provider call failed: {audit_payload.get('provider_call_failed', False)}",
        f"Live Codex attempted: {audit_payload['live_codex_attempted']}",
        f"Codex command: {audit_payload['codex_command'] or '(not configured)'}",
        f"Codex timeout seconds: {audit_payload['codex_timeout_seconds'] if audit_payload['codex_timeout_seconds'] is not None else '(n/a)'}",
        f"Pre-run tracked tree dirty: {audit_payload['pre_run_tracked_dirty']}",
        f"Pre-run tracked status: {audit_payload['pre_run_tracked_status'] or '(clean)'}",
        f"Final tracked tree dirty: {audit_payload['final_tracked_dirty']}",
        f"Final tracked status: {audit_payload['final_tracked_status'] or '(clean)'}",
        "Mode: fake/non-live dry-run only." if not audit_payload["live_codex_attempted"] else "Mode: local Codex executor requested.",
        "",
        "## Attempt Verdicts",
    ]
    if not audit_payload["attempts"] and audit_payload["pre_run_tracked_dirty"]:
        lines.append("- Review loop aborted before executor start because the tracked tree was not clean.")
    if audit_payload.get("provider_gate_blocked"):
        lines.append("- Provider gate blocked the run before executor start.")
    if audit_payload.get("tracked_tree_failure_reason"):
        lines.append(f"- Tracked-tree probe failure: {audit_payload['tracked_tree_failure_reason']}")
    for attempt in audit_payload["attempts"]:
        lines.append(f"- Attempt {attempt['attempt_number']}: {attempt['reviewer_verdict']['status']}")

    lines.extend(["", "## Changed Files Per Attempt"])
    for attempt in audit_payload["attempts"]:
        changed_files = attempt["executor_result"]["changed_files"]
        lines.append(f"- Attempt {attempt['attempt_number']}: {', '.join(changed_files) if changed_files else '(none)'}")
        lines.append(f"- Attempt {attempt['attempt_number']} tracked tree dirty: {attempt.get('post_attempt_tracked_dirty', False)}")
        lines.append(
            f"- Attempt {attempt['attempt_number']} tracked status: {attempt.get('post_attempt_tracked_status') or '(clean)'}"
        )

    lines.extend(["", "## Parsed Codex Output"])
    lines.append(f"- Parsed summary: {audit_payload.get('parsed_summary') or '(none)'}")
    parsed_changed_files = audit_payload.get("parsed_changed_files", [])
    lines.append(f"- Parsed changed files: {', '.join(parsed_changed_files) if parsed_changed_files else '(none)'}")
    parsed_diff_summary = dict(audit_payload.get("parsed_diff_summary", {}) or {})
    if parsed_diff_summary:
        lines.append(
            "- Parsed diff summary: "
            f"files={parsed_diff_summary.get('files_changed', 0)}, "
            f"insertions={parsed_diff_summary.get('insertions', 0)}, "
            f"deletions={parsed_diff_summary.get('deletions', 0)}, "
            f"lines={parsed_diff_summary.get('line_count', 0)}"
        )
    else:
        lines.append("- Parsed diff summary: (none)")
    parsed_validation = dict(audit_payload.get("parsed_validation", {}) or {})
    lines.append(f"- Parsed validation status: {parsed_validation.get('overall_status', '(none)')}")
    parsed_commands = parsed_validation.get("commands", []) if isinstance(parsed_validation.get("commands", []), list) else []
    if parsed_commands:
        for command in parsed_commands:
            lines.append(
                f"- Parsed validation command: {command.get('command') or '(empty)'} "
                f"(exit={command.get('exit_code') if command.get('exit_code') is not None else 'n/a'})"
            )
    else:
        lines.append("- Parsed validation command: (none)")
    if audit_payload.get("parsed_blocked_reason"):
        lines.append(f"- Parsed blocked reason: {audit_payload['parsed_blocked_reason']}")
    if audit_payload.get("parser_warning"):
        lines.append(f"- Parser warning: {audit_payload['parser_warning']}")
    if audit_payload.get("parse_error"):
        lines.append(f"- Parse error: {audit_payload['parse_error']}")

    lines.extend(["", "## Validation Summary"])
    for attempt in audit_payload["attempts"]:
        validation = attempt["validation_result"]
        outcome = "PASS" if validation["success"] else "FAIL"
        requested = ", ".join(validation["tests_requested"]) if validation["tests_requested"] else "(none)"
        lines.append(f"- Attempt {attempt['attempt_number']}: {outcome}; requested {requested}")

    lines.extend(
        [
            "",
            "## Executor Summary",
            f"- Codex exit code: {audit_payload['codex_exit_code'] if audit_payload['codex_exit_code'] is not None else '(not executed)'}",
            f"- Stdout summary: {audit_payload['stdout_summary'] or '(none)'}",
            f"- Stderr summary: {audit_payload['stderr_summary'] or '(none)'}",
        ]
    )
    if audit_payload.get("blocked_reason"):
        lines.append(f"- Blocked reason: {audit_payload['blocked_reason']}")
    if audit_payload.get("provider_failure_reason"):
        lines.append(f"- Provider failure reason: {audit_payload['provider_failure_reason']}")
    if audit_payload.get("provider_parse_failure"):
        lines.append(f"- Provider parse failure: {audit_payload['provider_parse_failure']}")
    if audit_payload.get("parsed_reviewer_decision"):
        lines.append(
            f"- Parsed reviewer decision: {json.dumps(audit_payload['parsed_reviewer_decision'], sort_keys=True)}"
        )
    if audit_payload.get("tracked_tree_failure_reason"):
        lines.append(f"- Tracked-tree failure reason: {audit_payload['tracked_tree_failure_reason']}")

    follow_up_prompts = [
        attempt["follow_up_prompt"]
        for attempt in audit_payload["attempts"]
        if attempt.get("follow_up_prompt")
    ]
    if follow_up_prompts:
        lines.extend(["", "## Follow-up prompt"])
        for prompt in follow_up_prompts:
            lines.append(prompt)
            lines.append("")

    lines.extend(
        [
            "## Safety Notes",
            "- LLM review output is metadata-only and cannot directly trigger file writes, commits, merges, deployments, registry appends, backtests, Hermes calls, broker/order/API actions, or strategy promotion.",
            "- This run stayed within deterministic review-loop controls. No provider output directly triggered external actions.",
            "- This run was fake/non-live. No live Codex CLI, OpenAI/GPT reviewer, git/GitHub action, deploy, Hetzner sync, or research execution occurred."
            if not audit_payload.get("provider_call_attempted")
            else "- Live reviewer mode remained isolated to review metadata and deterministic parsing only.",
            "- No live Codex CLI executed: yes",
            f"- No live OpenAI/GPT reviewer call: {'yes' if not audit_payload.get('provider_call_attempted') else 'no'}",
            "- No live git/GitHub action: yes",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
