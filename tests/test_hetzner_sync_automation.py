from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_safe_sync_with_preflight.sh"
SERVICE = ROOT / "deploy" / "systemd" / "research-lab-sync.service"
TIMER = ROOT / "deploy" / "systemd" / "research-lab-sync.timer"


def test_safe_sync_wrapper_runs_preflight_before_existing_sync():
    assert WRAPPER.exists()

    content = WRAPPER.read_text(encoding="utf-8")

    assert "set -euo pipefail" in content
    assert "logs/hetzner-sync.log" in content
    assert "bash scripts/check_hetzner_sync_readiness.sh" in content
    assert "bash scripts/sync_from_github.sh" in content
    assert content.index("bash scripts/check_hetzner_sync_readiness.sh") < content.index("bash scripts/sync_from_github.sh")


def test_sync_systemd_service_runs_as_trading_and_uses_wrapper():
    assert SERVICE.exists()

    content = SERVICE.read_text(encoding="utf-8")

    assert "Type=oneshot" in content
    assert "User=trading" in content
    assert "Group=trading" in content
    assert "WorkingDirectory=/opt/trading/research-lab" in content
    assert "ExecStart=/opt/trading/research-lab/scripts/run_safe_sync_with_preflight.sh" in content
    assert "research-lab-sync.timer" not in content


def test_sync_systemd_service_uses_repo_local_git_config_paths():
    assert SERVICE.exists()

    content = SERVICE.read_text(encoding="utf-8")

    assert "ProtectHome=true" in content
    assert "Environment=GIT_CONFIG_GLOBAL=/dev/null" in content
    assert "Environment=GIT_CONFIG_NOSYSTEM=1" in content
    assert "Environment=XDG_CONFIG_HOME=/opt/trading/research-lab/.git-systemd-config" in content
    assert "/home/trading/.config/git" not in content


def test_sync_timer_is_conservative_and_persistent():
    assert TIMER.exists()

    content = TIMER.read_text(encoding="utf-8")

    assert "Unit=research-lab-sync.service" in content
    assert "OnCalendar=hourly" in content
    assert "Persistent=true" in content
    assert "WantedBy=timers.target" in content


def test_sync_automation_contains_no_destructive_or_research_commands():
    paths = [WRAPPER, SERVICE, TIMER]
    for path in paths:
        assert path.exists()

    content = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden_fragments = [
        "reset --hard",
        "git clean",
        "rm -rf",
        "chown",
        "chmod",
        "systemctl restart",
        "run_daily_research",
        "run_hourly_research",
        "run_weekly_deep_research",
        "run_self_improvement",
        ".env",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in content
