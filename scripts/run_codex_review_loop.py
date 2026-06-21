from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_lab.orchestration.codex_autonomous_contract import (
    CodexRoundResult,
    LoopStatus,
    ReviewVerdict,
    ValidationResult,
)
from research_lab.orchestration.codex_review_loop import (
    CodexReviewLoop,
    CodexReviewLoopConfig,
    FakeReviewLoopExecutor,
    FakeReviewLoopReviewer,
    FakeReviewLoopValidationRunner,
)


DEFAULT_OUTPUT_DIR = ROOT / "codex_runs" / "review-loop-cli-smoke"
DEFAULT_TASK = "Run the fake Codex review loop."
VALID_VERDICTS = {status.value: status for status in (LoopStatus.PASS, LoopStatus.REVISE, LoopStatus.BLOCKED)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CodexReviewLoop in fake/non-live mode.")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Initial task prompt for the review loop.")
    parser.add_argument("--max-attempts", type=int, default=1, help="Maximum review-loop attempts to run.")
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
    args.fake_reviewer_verdicts = _parse_fake_verdicts(args.fake_reviewer_verdicts, parser)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir).expanduser().resolve()
    loop = CodexReviewLoop(
        config=CodexReviewLoopConfig(max_attempts=args.max_attempts, dry_run_external_calls=True),
        executor=FakeReviewLoopExecutor(_build_fake_rounds(args.max_attempts)),
        reviewer=FakeReviewLoopReviewer(_build_fake_reviewer_verdicts(args.fake_reviewer_verdicts)),
        validation_runner=FakeReviewLoopValidationRunner(_build_fake_validation_results(args.max_attempts)),
    )
    audit = loop.run(args.task)
    audit_payload = _build_audit_payload(audit.to_dict(), args.max_attempts)
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


def _build_fake_rounds(max_attempts: int) -> list[CodexRoundResult]:
    rounds: list[CodexRoundResult] = []
    for attempt_number in range(1, max_attempts + 1):
        changed_files = [
            "research_lab/orchestration/codex_review_loop.py",
            f"tests/fake_review_loop_attempt_{attempt_number}.py",
        ]
        rounds.append(
            CodexRoundResult(
                changed_files=changed_files,
                diff_line_count=10 * attempt_number,
                proposed_commands=[],
                summary=f"Fake executor completed attempt {attempt_number}.",
                patch_digest=f"fake-attempt-{attempt_number}",
                meaningful_progress=True,
                executor_details={"mode": "fake_non_live", "attempt_number": attempt_number},
            )
        )
    return rounds


def _build_fake_reviewer_verdicts(verdicts: list[str]) -> list[ReviewVerdict]:
    items: list[ReviewVerdict] = []
    for attempt_number, verdict_name in enumerate(verdicts, start=1):
        status = VALID_VERDICTS[verdict_name]
        if status is LoopStatus.REVISE:
            items.append(
                ReviewVerdict(
                    status=status,
                    summary=f"Fake reviewer requested revisions on attempt {attempt_number}.",
                    issues=[f"Address fake reviewer feedback for attempt {attempt_number}."],
                )
            )
        elif status is LoopStatus.BLOCKED:
            items.append(
                ReviewVerdict(
                    status=status,
                    summary=f"Fake reviewer blocked the run on attempt {attempt_number}.",
                    issues=["Manual intervention required before another attempt."],
                )
            )
        else:
            items.append(ReviewVerdict(status=status, summary=f"Fake reviewer approved attempt {attempt_number}."))
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


def _build_audit_payload(audit_payload: dict, max_attempts: int) -> dict:
    payload = dict(audit_payload)
    payload["max_attempts"] = max_attempts
    payload["reviewer_verdicts"] = list(payload.get("verdicts", []))
    return payload


def _build_report(audit_payload: dict) -> str:
    lines = [
        "# CodexReviewLoop Final Report",
        "",
        f"Final status: {audit_payload['final_status']}",
        f"Number of attempts: {len(audit_payload['attempts'])}",
        "Mode: fake/non-live dry-run only.",
        "",
        "## Attempt Verdicts",
    ]
    for attempt in audit_payload["attempts"]:
        lines.append(f"- Attempt {attempt['attempt_number']}: {attempt['reviewer_verdict']['status']}")

    lines.extend(["", "## Changed Files Per Attempt"])
    for attempt in audit_payload["attempts"]:
        changed_files = attempt["executor_result"]["changed_files"]
        lines.append(f"- Attempt {attempt['attempt_number']}: {', '.join(changed_files) if changed_files else '(none)'}")

    lines.extend(["", "## Validation Summary"])
    for attempt in audit_payload["attempts"]:
        validation = attempt["validation_result"]
        outcome = "PASS" if validation["success"] else "FAIL"
        requested = ", ".join(validation["tests_requested"]) if validation["tests_requested"] else "(none)"
        lines.append(f"- Attempt {attempt['attempt_number']}: {outcome}; requested {requested}")

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
            "- This run was fake/non-live. No live Codex CLI, OpenAI/GPT reviewer, git/GitHub action, deploy, Hetzner sync, or research execution occurred.",
            "- No live Codex CLI executed: yes",
            "- No live OpenAI/GPT reviewer call: yes",
            "- No live git/GitHub action: yes",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
