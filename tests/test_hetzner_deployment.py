from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_hetzner_deployment import (
    RUNTIME_ARTIFACT_PATHS,
    CommandResult,
    assess_deployment,
    build_apply_commands,
    build_smoke_commands,
    collect_deployment_state,
    redact_secrets,
)


SECRET = "eodhd-secret-value"


class FakeExecutor:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, cwd=None):
        rendered = " ".join(command)
        self.commands.append(rendered)
        stdout = self.responses.get(rendered, "")
        return CommandResult(command=tuple(command), returncode=0, stdout=stdout, stderr="")


def test_dry_run_does_not_execute_destructive_commands():
    executor = FakeExecutor({})

    commands = build_apply_commands(
        repo_path="/opt/trading/research-lab",
        branch="main",
        restart_services=("trading-research-daily.timer",),
    )
    results = [command for command in commands if command.dry_run(executor)]

    assert results == commands
    assert executor.commands == []
    rendered = "\n".join(command.remote_command for command in commands)
    assert "git checkout main" in rendered
    assert "git pull --ff-only origin main" in rendered
    assert "systemctl restart trading-research-daily.timer" in rendered


def test_secrets_are_redacted_from_strings_and_json_payloads():
    payload = {
        "message": f"token={SECRET}",
        "nested": {"EODHD_API_KEY": SECRET, "MASSIVE_API_KEY": "massive-secret"},
    }

    redacted = redact_secrets(json.dumps(payload), known_secrets=[SECRET, "massive-secret"])

    assert SECRET not in redacted
    assert "massive-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_eodhd_key_presence_is_reported_without_value():
    state = collect_deployment_state(
        host="deploy@example",
        repo_path="/opt/trading/research-lab",
        executor=FakeExecutor(
            {
                "git branch --show-current": "main\n",
                "git rev-parse HEAD": "local-main\n",
                "git rev-parse main": "local-main\n",
                "git merge-base --is-ancestor server-main main": "",
                "ssh deploy@example cd /opt/trading/research-lab && git rev-parse --abbrev-ref HEAD": "main\n",
                "ssh deploy@example cd /opt/trading/research-lab && git rev-parse HEAD": "server-main\n",
                "ssh deploy@example cd /opt/trading/research-lab && git status --short": "",
                "ssh deploy@example cd /opt/trading/research-lab && python3 - <<'PY'\nfrom pathlib import Path\nimport json\nkeys = {}\npath = Path('.env')\nif path.exists():\n    for line in path.read_text(encoding='utf-8').splitlines():\n        line = line.strip()\n        if not line or line.startswith('#') or '=' not in line:\n            continue\n        key, value = line.split('=', 1)\n        keys[key.strip()] = value.strip().strip('\"').strip(\"'\")\nprint(json.dumps({\n    'env_file_present': path.exists(),\n    'RESEARCH_LAB_DATA_PROVIDER': {'present': 'RESEARCH_LAB_DATA_PROVIDER' in keys, 'value': keys.get('RESEARCH_LAB_DATA_PROVIDER', '')},\n    'EODHD_API_KEY': {'present': bool(keys.get('EODHD_API_KEY', '').strip())},\n    'EODHD_START_DATE': {'present': 'EODHD_START_DATE' in keys, 'value': keys.get('EODHD_START_DATE', '')},\n    'MASSIVE_API_KEY': {'present': bool(keys.get('MASSIVE_API_KEY', '').strip())},\n}, sort_keys=True))\nPY": json.dumps(
                    {
                        "env_file_present": True,
                        "RESEARCH_LAB_DATA_PROVIDER": {"present": True, "value": "eodhd"},
                        "EODHD_API_KEY": {"present": True},
                        "EODHD_START_DATE": {"present": True, "value": "1990-01-01"},
                        "MASSIVE_API_KEY": {"present": True},
                    }
                ),
            }
        ),
    )

    serialized = json.dumps(state.server_env, sort_keys=True)
    assert state.server_env["EODHD_API_KEY"] == {"present": True}
    assert SECRET not in serialized
    assert "value" not in state.server_env["EODHD_API_KEY"]
    assert state.server_env["MASSIVE_API_KEY"] == {"present": True}


def test_dirty_server_status_blocks_deploy_unless_forced():
    state = assess_deployment(
        local_branch="main",
        local_commit="local-main",
        expected_main_commit="local-main",
        server_branch="main",
        server_commit="local-main",
        server_status=" M scripts/run_daily_research.py\n",
        server_env={},
        force_dirty=False,
    )

    assert state.deploy_blocked is True
    assert "server checkout has uncommitted changes" in state.blockers

    forced = assess_deployment(
        local_branch="main",
        local_commit="local-main",
        expected_main_commit="local-main",
        server_branch="main",
        server_commit="local-main",
        server_status=" M scripts/run_daily_research.py\n",
        server_env={},
        force_dirty=True,
    )
    assert forced.deploy_blocked is False


def test_branch_and_commit_mismatch_are_reported():
    state = assess_deployment(
        local_branch="main",
        local_commit="local-main",
        expected_main_commit="local-main",
        server_branch="feature/manual-patch",
        server_commit="older-commit",
        server_status="",
        server_env={},
    )

    assert state.branch_matches is False
    assert state.commit_matches is False
    assert state.server_behind_main is True
    assert "server branch is feature/manual-patch, expected main" in state.warnings
    assert "server commit differs from local main" in state.warnings


def test_runtime_artifact_paths_are_not_modified():
    commands = build_apply_commands(repo_path="/opt/trading/research-lab", branch="main")
    rendered = "\n".join(command.remote_command for command in commands)

    assert RUNTIME_ARTIFACT_PATHS == (
        "data/manifests/*",
        "registry/*",
        "reports/*",
        "backtests/runs/*",
    )
    for path in RUNTIME_ARTIFACT_PATHS:
        assert path not in rendered
    assert "rm " not in rendered
    assert "git clean" not in rendered


def test_smoke_command_list_is_tiny_and_excludes_long_research_jobs():
    commands = build_smoke_commands(repo_path="/opt/trading/research-lab")
    rendered = "\n".join(command.remote_command for command in commands)

    assert len(commands) == 3
    assert "import research_lab" in rendered
    assert "check_eodhd_access.py --symbol SPY.US --daily-start 2026-01-01" in rendered
    assert "systemctl status trading-research-daily.timer" in rendered
    assert "run_daily_research.py" not in rendered
    assert "run_weekly_deep_research.py" not in rendered
    assert "run_self_improvement.py" not in rendered
