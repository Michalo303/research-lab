#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


DEFAULT_REPO_PATH = "/opt/trading/research-lab"
DEFAULT_BRANCH = "main"
DEFAULT_HOST = "hetzner-research"
DEFAULT_RESTART_SERVICES = ("trading-research-daily.timer",)
RUNTIME_ARTIFACT_PATHS = (
    "data/manifests/*",
    "registry/*",
    "reports/*",
    "backtests/runs/*",
)

ENV_CHECK_SNIPPET = """python3 - <<'PY'
from pathlib import Path
import json
keys = {}
path = Path('.env')
if path.exists():
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        keys[key.strip()] = value.strip().strip('"').strip("'")
print(json.dumps({
    'env_file_present': path.exists(),
    'RESEARCH_LAB_DATA_PROVIDER': {'present': 'RESEARCH_LAB_DATA_PROVIDER' in keys, 'value': keys.get('RESEARCH_LAB_DATA_PROVIDER', '')},
    'EODHD_API_KEY': {'present': bool(keys.get('EODHD_API_KEY', '').strip())},
    'EODHD_START_DATE': {'present': 'EODHD_START_DATE' in keys, 'value': keys.get('EODHD_START_DATE', '')},
    'MASSIVE_API_KEY': {'present': bool(keys.get('MASSIVE_API_KEY', '').strip())},
}, sort_keys=True))
PY"""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PlannedCommand:
    label: str
    remote_command: str

    def dry_run(self, executor: Callable[[Sequence[str]], CommandResult] | None = None) -> "PlannedCommand":
        return self

    def execute(self, host: str, executor: Callable[[Sequence[str]], CommandResult]) -> CommandResult:
        return executor(("ssh", host, self.remote_command))


@dataclass(frozen=True)
class DeploymentState:
    local_branch: str
    local_commit: str
    expected_main_commit: str
    server_branch: str
    server_commit: str
    server_status: str
    server_env: dict
    branch_matches: bool
    commit_matches: bool
    server_behind_main: bool
    deploy_blocked: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


Executor = Callable[[Sequence[str], str | None], CommandResult]


def _run(command: Sequence[str], cwd: str | None = None) -> CommandResult:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _require_success(result: CommandResult) -> str:
    if result.returncode != 0:
        rendered = " ".join(result.command)
        raise RuntimeError(f"Command failed ({result.returncode}): {rendered}\n{result.stderr}")
    return result.stdout.strip()


def _remote_command(repo_path: str, command: str) -> str:
    return f"cd {repo_path} && {command}"


def _ssh(host: str, repo_path: str, command: str) -> tuple[str, str, str]:
    return ("ssh", host, _remote_command(repo_path, command))


def _parse_env(stdout: str) -> dict:
    if not stdout.strip():
        return {
            "env_file_present": False,
            "RESEARCH_LAB_DATA_PROVIDER": {"present": False, "value": ""},
            "EODHD_API_KEY": {"present": False},
            "EODHD_START_DATE": {"present": False, "value": ""},
            "MASSIVE_API_KEY": {"present": False},
        }
    parsed = json.loads(stdout)
    for secret_key in ("EODHD_API_KEY", "MASSIVE_API_KEY"):
        parsed[secret_key] = {"present": bool(parsed.get(secret_key, {}).get("present"))}
    return parsed


def redact_secrets(text: str, known_secrets: Iterable[str] = ()) -> str:
    redacted = text
    for secret in known_secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(
        r'(?i)("?(?:api[_-]?key|token|password|secret)"?\s*[:=]\s*")([^"]+)(")',
        r"\1[REDACTED]\3",
        redacted,
    )
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*=\s*)([^\s,&]+)",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def assess_deployment(
    *,
    local_branch: str,
    local_commit: str,
    expected_main_commit: str,
    server_branch: str,
    server_commit: str,
    server_status: str,
    server_env: dict,
    expected_branch: str = DEFAULT_BRANCH,
    force_dirty: bool = False,
) -> DeploymentState:
    branch_matches = server_branch == expected_branch
    commit_matches = server_commit == expected_main_commit
    dirty = bool(server_status.strip())
    server_behind_main = not commit_matches
    blockers: list[str] = []
    warnings: list[str] = []

    if dirty and not force_dirty:
        blockers.append("server checkout has uncommitted changes")
    if local_branch != expected_branch:
        warnings.append(f"local branch is {local_branch}, expected {expected_branch}")
    if not branch_matches:
        warnings.append(f"server branch is {server_branch}, expected {expected_branch}")
    if not commit_matches:
        warnings.append("server commit differs from local main")

    return DeploymentState(
        local_branch=local_branch,
        local_commit=local_commit,
        expected_main_commit=expected_main_commit,
        server_branch=server_branch,
        server_commit=server_commit,
        server_status=server_status,
        server_env=server_env,
        branch_matches=branch_matches,
        commit_matches=commit_matches,
        server_behind_main=server_behind_main,
        deploy_blocked=bool(blockers),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def collect_deployment_state(
    *,
    host: str,
    repo_path: str = DEFAULT_REPO_PATH,
    expected_branch: str = DEFAULT_BRANCH,
    executor: Executor = _run,
    force_dirty: bool = False,
) -> DeploymentState:
    local_branch = _require_success(executor(("git", "branch", "--show-current"), None))
    local_commit = _require_success(executor(("git", "rev-parse", "HEAD"), None))
    expected_main_commit = _require_success(executor(("git", "rev-parse", expected_branch), None))
    server_branch = _require_success(executor(_ssh(host, repo_path, "git rev-parse --abbrev-ref HEAD"), None))
    server_commit = _require_success(executor(_ssh(host, repo_path, "git rev-parse HEAD"), None))
    server_status = _require_success(executor(_ssh(host, repo_path, "git status --short"), None))
    server_env = _parse_env(_require_success(executor(_ssh(host, repo_path, ENV_CHECK_SNIPPET), None)))

    merge_base = executor(("git", "merge-base", "--is-ancestor", server_commit, expected_branch), None)
    state = assess_deployment(
        local_branch=local_branch,
        local_commit=local_commit,
        expected_main_commit=expected_main_commit,
        server_branch=server_branch,
        server_commit=server_commit,
        server_status=server_status,
        server_env=server_env,
        expected_branch=expected_branch,
        force_dirty=force_dirty,
    )
    if merge_base.returncode == 0 and not state.commit_matches:
        return state
    if merge_base.returncode != 0 and not state.commit_matches:
        warnings = tuple([*state.warnings, "server commit is not an ancestor of local main"])
        return DeploymentState(**(asdict(state) | {"warnings": warnings}))
    return state


def build_apply_commands(
    *,
    repo_path: str = DEFAULT_REPO_PATH,
    branch: str = DEFAULT_BRANCH,
    restart_services: Sequence[str] = DEFAULT_RESTART_SERVICES,
) -> list[PlannedCommand]:
    commands: list[PlannedCommand] = [
        PlannedCommand("checkout expected branch", _remote_command(repo_path, f"git checkout {branch}")),
        PlannedCommand("fast-forward from origin", _remote_command(repo_path, f"git pull --ff-only origin {branch}")),
    ]
    for service in restart_services:
        commands.append(PlannedCommand(f"restart {service}", f"sudo systemctl restart {service}"))
    return commands


def build_smoke_commands(*, repo_path: str = DEFAULT_REPO_PATH) -> list[PlannedCommand]:
    return [
        PlannedCommand(
            "import smoke",
            _remote_command(repo_path, ". .venv/bin/activate && python -c \"import research_lab; print('import ok')\""),
        ),
        PlannedCommand(
            "tiny EODHD access diagnostic",
            _remote_command(
                repo_path,
                ". .venv/bin/activate && python scripts/check_eodhd_access.py --symbol SPY.US --daily-start 2026-01-01",
            ),
        ),
        PlannedCommand(
            "systemd timer status",
            "systemctl status trading-research-daily.timer trading-research-daily.service --no-pager",
        ),
    ]


def _print_state(state: DeploymentState, recommend_deploy: bool, smoke: bool, repo_path: str, branch: str) -> None:
    payload = {
        "deployment": asdict(state),
        "runtime_artifact_paths_not_touched": RUNTIME_ARTIFACT_PATHS,
    }
    print(redact_secrets(json.dumps(payload, indent=2, sort_keys=True)))
    if recommend_deploy:
        print("\nRecommended deployment commands (review before running):")
        for command in build_apply_commands(repo_path=repo_path, branch=branch):
            print(f"- {command.label}: {command.remote_command}")
    if smoke:
        print("\nOptional tiny smoke commands:")
        for command in build_smoke_commands(repo_path=repo_path):
            print(f"- {command.label}: {command.remote_command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Hetzner deployment hygiene check.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host alias or user@host for the research server.")
    parser.add_argument("--repo-path", default=DEFAULT_REPO_PATH, help="Server checkout path.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Expected deployment branch.")
    parser.add_argument("--dry-run", action="store_true", help="Print checks and planned commands without applying deployment changes.")
    parser.add_argument("--recommend-deploy", action="store_true", help="Also print git checkout/pull recommendations.")
    parser.add_argument("--force-dirty", action="store_true", help="Do not block recommendations when server status is dirty.")
    parser.add_argument("--smoke", action="store_true", help="Print optional tiny smoke checks. Does not run daily research.")
    args = parser.parse_args(argv)

    try:
        state = collect_deployment_state(
            host=args.host,
            repo_path=args.repo_path,
            expected_branch=args.branch,
            force_dirty=args.force_dirty,
        )
    except Exception as exc:
        print(redact_secrets(str(exc)), file=sys.stderr)
        return 1
    _print_state(
        state,
        recommend_deploy=args.recommend_deploy,
        smoke=args.smoke,
        repo_path=args.repo_path,
        branch=args.branch,
    )
    if state.deploy_blocked:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
