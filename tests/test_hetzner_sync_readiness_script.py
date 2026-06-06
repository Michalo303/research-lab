from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_hetzner_sync_readiness.sh"


def test_hetzner_sync_readiness_script_has_required_diagnostics():
    assert SCRIPT.exists()

    content = SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in content
    assert "git branch --show-current" in content
    assert '"main"' in content
    assert "git status --porcelain --untracked-files=no" in content
    assert "origin/main" in content
    assert "stat " in content


def test_hetzner_sync_readiness_script_is_non_destructive():
    assert SCRIPT.exists()

    content = SCRIPT.read_text(encoding="utf-8")

    forbidden_fragments = [
        "git pull",
        "reset --hard",
        "git clean",
        "rm -rf",
        "chown",
        "chmod",
        "run_daily_research",
        "systemctl restart",
        "service ",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in content
