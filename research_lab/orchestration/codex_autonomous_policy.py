from __future__ import annotations

from dataclasses import dataclass, field

from research_lab.orchestration.codex_autonomous_contract import CodexLoopConfig


@dataclass
class PolicyEvaluation:
    status: str
    disallowed_paths_touched: list[str] = field(default_factory=list)
    protected_paths_touched: list[str] = field(default_factory=list)
    forbidden_commands_detected: list[str] = field(default_factory=list)
    changed_file_limit_exceeded: bool = False
    diff_limit_exceeded: bool = False


def evaluate_round_policy(
    config: CodexLoopConfig,
    *,
    changed_files: list[str],
    diff_line_count: int,
    proposed_commands: list[str],
    branch: str,
    human_merge_confirmed: bool,
) -> PolicyEvaluation:
    normalized_files = [_normalize_path(path) for path in changed_files]
    normalized_commands = [command.strip() for command in proposed_commands]

    protected_paths_touched = [
        path
        for path in normalized_files
        if any(_path_matches(path, protected) for protected in config.protected_paths)
    ]
    disallowed_paths_touched = [
        path
        for path in normalized_files
        if path not in protected_paths_touched
        if not any(_path_matches(path, allowed) for allowed in config.allowed_paths)
    ]

    forbidden_commands_detected: list[str] = []
    lowered_commands = [command.lower() for command in normalized_commands]
    for fragment in config.forbidden_command_fragments:
        lowered_fragment = fragment.lower()
        if any(lowered_fragment in command for command in lowered_commands):
            forbidden_commands_detected.append(fragment)

    extra_forbidden_checks = {
        "push origin main": any("push" in command and "origin main" in command for command in lowered_commands),
        "merge main": any("merge main" in command or "merge origin/main" in command for command in lowered_commands),
        "deploy": any("deploy" in command for command in lowered_commands),
        "service restart": any("service restart" in command for command in lowered_commands),
        "systemctl": any("systemctl" in command for command in lowered_commands),
        "registry append": any("registry append" in command for command in lowered_commands),
        "daily research": any("daily research" in command or "run_daily" in command for command in lowered_commands),
        "rm -rf": any("rm -rf" in command for command in lowered_commands),
    }
    for fragment, triggered in extra_forbidden_checks.items():
        if triggered and fragment not in forbidden_commands_detected:
            forbidden_commands_detected.append(fragment)

    if any("hetzner" in command for command in lowered_commands) and not human_merge_confirmed:
        if "scripts/run_safe_sync_with_preflight.sh" not in forbidden_commands_detected:
            forbidden_commands_detected.append("scripts/run_safe_sync_with_preflight.sh")

    if branch.strip().lower() in {"main", "origin/main"}:
        if "push origin main" not in forbidden_commands_detected:
            forbidden_commands_detected.append("push origin main")

    changed_file_limit_exceeded = len(normalized_files) > config.max_changed_files
    diff_limit_exceeded = diff_line_count > config.max_diff_lines

    status = "PASS"
    if (
        protected_paths_touched
        or disallowed_paths_touched
        or forbidden_commands_detected
        or changed_file_limit_exceeded
        or diff_limit_exceeded
    ):
        status = "UNSAFE"

    return PolicyEvaluation(
        status=status,
        disallowed_paths_touched=disallowed_paths_touched,
        protected_paths_touched=protected_paths_touched,
        forbidden_commands_detected=sorted(set(forbidden_commands_detected)),
        changed_file_limit_exceeded=changed_file_limit_exceeded,
        diff_limit_exceeded=diff_limit_exceeded,
    )


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def _path_matches(candidate: str, protected: str) -> bool:
    target = _normalize_path(protected)
    if target.endswith("/"):
        return candidate.startswith(target)
    return candidate == target or candidate.startswith(target + "/")
