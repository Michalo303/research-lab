from __future__ import annotations

from pathlib import Path
import re
import subprocess
import time

from research_lab.orchestration.codex_autonomous_contract import (
    CodexBudgetConfig,
    CodexExecutorInterface,
    CodexExecutionTier,
    CodexRoundInput,
    CodexRoundResult,
    CodexTierDecision,
    LoopMode,
)


EXECUTOR_BLOCKED_TASK_FRAGMENTS = [
    "git reset --hard",
    "git clean",
    "rm -rf",
    "deploy",
    "systemctl",
    "service restart",
    "daily research",
    "run_daily",
    "registry append",
    "push origin main",
    "merge main",
    "scripts/run_safe_sync_with_preflight.sh",
    ".env",
    "secrets/",
]


class CodexCliExecutor(CodexExecutorInterface):
    def __init__(
        self,
        *,
        repo_root: Path,
        task_prompt_text: str,
        timeout_seconds: int,
        dry_run: bool,
        requested_tier: CodexExecutionTier = CodexExecutionTier.AUTO,
        budget_config: CodexBudgetConfig | None = None,
        codex_binary: str = "codex",
        runner=subprocess.run,
        clock=time.monotonic,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.task_prompt_text = task_prompt_text
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.requested_tier = requested_tier
        self.budget_config = budget_config or CodexBudgetConfig()
        self.codex_binary = codex_binary
        self.runner = runner
        self.clock = clock
        self.codex_calls_made = 0
        self.high_rounds_used = 0
        self.very_high_rounds_used = 0
        self.last_changed_files: list[str] = []
        self.last_diff_line_count = 0

    def execute(self, round_input: CodexRoundInput) -> CodexRoundResult:
        prompt = self._build_prompt(round_input)
        decision = self.decide_tier(round_input)
        argv = self._build_command_argv(decision)
        if decision.budget_blocked:
            return CodexRoundResult(
                changed_files=[],
                diff_line_count=0,
                summary=f"Codex CLI execution blocked by budget guard. {decision.escalation_reason}",
                meaningful_progress=False,
                executor_failed=True,
                executor_details=self._build_executor_details(
                    argv=argv,
                    decision=decision,
                    returncode=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    apply_budget=False,
                ),
            )
        if self._contains_dangerous_text(prompt):
            return CodexRoundResult(
                changed_files=[],
                diff_line_count=0,
                summary="Blocked dangerous task text before Codex CLI execution.",
                meaningful_progress=False,
                executor_failed=True,
                executor_details=self._build_executor_details(
                    argv=argv,
                    decision=decision,
                    returncode=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    apply_budget=False,
                ),
            )

        if self.dry_run:
            return CodexRoundResult(
                changed_files=[],
                diff_line_count=0,
                summary="Codex CLI execution skipped in dry-run mode.",
                meaningful_progress=False,
                executor_details=self._build_executor_details(
                    argv=argv,
                    decision=decision,
                    returncode=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    apply_budget=False,
                ),
            )

        started = self.clock()
        try:
            self.codex_calls_made += 1
            completed = self.runner(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(self.repo_root),
                timeout=self.timeout_seconds,
                check=False,
            )
            duration_seconds = round(self.clock() - started, 3)
            changed_files = self._collect_changed_files()
            diff_line_count = self._collect_diff_line_count()
            self.last_changed_files = list(changed_files)
            self.last_diff_line_count = diff_line_count
            stdout = self._sanitize_text(completed.stdout or "").strip()
            stderr = self._sanitize_text(completed.stderr or "").strip()
            return CodexRoundResult(
                changed_files=changed_files,
                diff_line_count=diff_line_count,
                proposed_commands=[],
                summary=self._build_summary(completed.returncode, stdout, stderr),
                patch_digest=stdout[-200:],
                meaningful_progress=bool(changed_files or stdout),
                executor_failed=completed.returncode != 0,
                executor_details=self._build_executor_details(
                    argv=argv,
                    decision=decision,
                    returncode=completed.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration_seconds,
                    apply_budget=True,
                ),
            )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = round(self.clock() - started, 3)
            changed_files = self._collect_changed_files()
            diff_line_count = self._collect_diff_line_count()
            self.last_changed_files = list(changed_files)
            self.last_diff_line_count = diff_line_count
            return CodexRoundResult(
                changed_files=changed_files,
                diff_line_count=diff_line_count,
                summary=f"Codex CLI timed out after {self.timeout_seconds} seconds.",
                meaningful_progress=False,
                executor_failed=True,
                executor_details=self._build_executor_details(
                    argv=argv,
                    decision=decision,
                    returncode=None,
                    stdout=self._sanitize_text((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
                    stderr=self._sanitize_text((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
                    duration_seconds=duration_seconds,
                    apply_budget=True,
                ),
            )

    def decide_tier(self, round_input: CodexRoundInput) -> CodexTierDecision:
        reason = ""
        selected_tier = self.requested_tier
        requested_tier = self.requested_tier
        budget_blocked = False

        if self.codex_calls_made >= self.budget_config.max_codex_calls_per_run:
            return CodexTierDecision(
                requested_tier=requested_tier,
                selected_tier=CodexExecutionTier.STANDARD,
                codex_model=self.budget_config.default_model,
                codex_reasoning="medium",
                escalation_reason="Maximum Codex calls per run exceeded.",
                high_rounds_used=self.high_rounds_used,
                very_high_rounds_used=self.very_high_rounds_used,
                max_high_rounds=self.budget_config.max_high_rounds_per_run,
                max_very_high_rounds=self.budget_config.max_very_high_rounds_per_run,
                budget_blocked=True,
            )

        if requested_tier is CodexExecutionTier.AUTO:
            selected_tier = self.budget_config.default_tier
            auto_reasons = self._auto_escalation_reasons(round_input)
            if auto_reasons:
                selected_tier = CodexExecutionTier.HIGH
                reason = "; ".join(auto_reasons)
            if (
                selected_tier is CodexExecutionTier.HIGH
                and self.budget_config.allow_very_high
                and (
                    round_input.round_number > 10
                    or "deep review" in self.task_prompt_text.lower()
                    or "architectural blocker" in self.task_prompt_text.lower()
                )
            ):
                selected_tier = CodexExecutionTier.VERY_HIGH
                reason = f"{reason}; escalated to very_high"
        elif requested_tier is CodexExecutionTier.VERY_HIGH and not self.budget_config.allow_very_high:
            return CodexTierDecision(
                requested_tier=requested_tier,
                selected_tier=CodexExecutionTier.VERY_HIGH,
                codex_model=self.budget_config.very_high_model,
                codex_reasoning="very_high",
                escalation_reason="Requested very_high tier is disabled by budget configuration.",
                high_rounds_used=self.high_rounds_used,
                very_high_rounds_used=self.very_high_rounds_used,
                max_high_rounds=self.budget_config.max_high_rounds_per_run,
                max_very_high_rounds=self.budget_config.max_very_high_rounds_per_run,
                budget_blocked=True,
            )

        if selected_tier is CodexExecutionTier.HIGH:
            if self.high_rounds_used >= self.budget_config.max_high_rounds_per_run:
                if requested_tier is CodexExecutionTier.HIGH:
                    budget_blocked = True
                    reason = "Requested high tier exceeds max_high_rounds_per_run."
                else:
                    selected_tier = CodexExecutionTier.STANDARD
                    reason = f"{reason}; fell back to standard because high tier budget is exhausted.".strip("; ")
        if selected_tier is CodexExecutionTier.VERY_HIGH:
            if self.very_high_rounds_used >= self.budget_config.max_very_high_rounds_per_run:
                selected_tier = CodexExecutionTier.HIGH
                reason = f"{reason}; fell back from very_high because very_high budget is exhausted.".strip("; ")
            if selected_tier is CodexExecutionTier.HIGH and self.high_rounds_used >= self.budget_config.max_high_rounds_per_run:
                selected_tier = CodexExecutionTier.STANDARD
                reason = f"{reason}; fell back from high because high tier budget is exhausted.".strip("; ")

        codex_model = self._model_for_tier(selected_tier)
        codex_reasoning = self._reasoning_for_tier(selected_tier)
        return CodexTierDecision(
            requested_tier=requested_tier,
            selected_tier=selected_tier,
            codex_model=codex_model,
            codex_reasoning=codex_reasoning,
            escalation_reason=reason,
            high_rounds_used=self.high_rounds_used,
            very_high_rounds_used=self.very_high_rounds_used,
            max_high_rounds=self.budget_config.max_high_rounds_per_run,
            max_very_high_rounds=self.budget_config.max_very_high_rounds_per_run,
            budget_blocked=budget_blocked,
        )

    def _build_command_argv(self, decision: CodexTierDecision) -> list[str]:
        argv = [
            self.codex_binary,
            "exec",
            "--cd",
            str(self.repo_root),
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
        ]
        if decision.codex_model:
            argv.extend(["-m", decision.codex_model])
        argv.extend(["--json", "-"])
        return argv

    def _build_prompt(self, round_input: CodexRoundInput) -> str:
        return (
            f"Round: {round_input.round_number}\n"
            f"Mode: {round_input.mode.value}\n"
            f"Branch: {round_input.branch}\n"
            f"Task file: {round_input.task_file}\n\n"
            f"{self.task_prompt_text.strip()}\n"
        )

    def _auto_escalation_reasons(self, round_input: CodexRoundInput) -> list[str]:
        reasons: list[str] = []
        prior = [verdict.upper() for verdict in round_input.prior_reviewer_verdicts]
        lowered_text = self.task_prompt_text.lower()
        if round_input.round_number >= 3 and "REVISE" in prior:
            reasons.append("round >= 3 with prior REVISE verdicts")
        if "repeated failure" in lowered_text or "failure pattern" in lowered_text:
            reasons.append("repeated failure pattern is provided")
        if len(self.last_changed_files) > 6:
            reasons.append("previous changed files count exceeded 6")
        if self.last_diff_line_count > 800:
            reasons.append("previous diff line count exceeded 800")
        keywords = [
            "architecture",
            "policy",
            "safety",
            "executor",
            "reviewer",
            "github",
            "sync",
            "hertzner",
            "deployment",
            "orchestration",
        ]
        if any(keyword in lowered_text for keyword in keywords):
            reasons.append("task text matched escalation keywords")
        if round_input.mode is LoopMode.SUPER_AUTO and round_input.round_number > 5:
            reasons.append("super_auto round exceeded 5")
        return reasons

    def _contains_dangerous_text(self, prompt: str) -> bool:
        lowered = prompt.lower()
        return any(fragment.lower() in lowered for fragment in EXECUTOR_BLOCKED_TASK_FRAGMENTS)

    def _collect_changed_files(self) -> list[str]:
        completed = self.runner(
            ["git", "diff", "--name-only"],
            text=True,
            capture_output=True,
            cwd=str(self.repo_root),
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            return []
        return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]

    def _collect_diff_line_count(self) -> int:
        completed = self.runner(
            ["git", "diff", "--numstat"],
            text=True,
            capture_output=True,
            cwd=str(self.repo_root),
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            return 0
        total = 0
        for line in (completed.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, removed = parts[0], parts[1]
            if added.isdigit():
                total += int(added)
            if removed.isdigit():
                total += int(removed)
        return total

    def _build_summary(self, returncode: int, stdout: str, stderr: str) -> str:
        if returncode == 0:
            if stdout:
                return f"Codex CLI completed successfully. Last output: {stdout.splitlines()[-1]}"
            return "Codex CLI completed successfully."
        if stderr:
            return f"Codex CLI failed with exit code {returncode}. {stderr.splitlines()[-1]}"
        return f"Codex CLI failed with exit code {returncode}."

    def _build_executor_details(
        self,
        *,
        argv: list[str],
        decision: CodexTierDecision,
        returncode: int | None,
        stdout: str,
        stderr: str,
        duration_seconds: float,
        apply_budget: bool,
    ) -> dict[str, object]:
        if apply_budget:
            self._apply_budget_counters(decision)
        return {
            "command_argv": argv,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": duration_seconds,
            "tier_decision": decision.to_dict(),
            "reasoning_flag_supported": False,
        }

    def _apply_budget_counters(self, decision: CodexTierDecision) -> None:
        if decision.budget_blocked:
            return
        if decision.selected_tier is CodexExecutionTier.HIGH:
            self.high_rounds_used += 1
        elif decision.selected_tier is CodexExecutionTier.VERY_HIGH:
            self.very_high_rounds_used += 1

    def _model_for_tier(self, tier: CodexExecutionTier) -> str:
        if tier is CodexExecutionTier.HIGH:
            return self.budget_config.high_model
        if tier is CodexExecutionTier.VERY_HIGH:
            return self.budget_config.very_high_model
        return self.budget_config.default_model

    def _reasoning_for_tier(self, tier: CodexExecutionTier) -> str:
        if tier is CodexExecutionTier.FAST:
            return "low"
        if tier is CodexExecutionTier.HIGH:
            return "high"
        if tier is CodexExecutionTier.VERY_HIGH:
            return "very_high"
        return "medium"

    def _sanitize_text(self, text: str) -> str:
        sanitized = text
        sanitized = re.sub(
            r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=([^\s]+)",
            r"\1=[REDACTED]",
            sanitized,
        )
        sanitized = re.sub(
            r"(?i)\b(token|secret|password)\s*[:=]\s*([^\s]+)",
            r"\1=[REDACTED]",
            sanitized,
        )
        return sanitized
