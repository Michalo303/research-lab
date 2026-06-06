from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_from_github.sh"


def test_sync_from_github_script_has_required_safety_guards():
    assert SCRIPT.exists()

    content = SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in content
    assert "git pull --ff-only origin main" in content or "git merge --ff-only origin/main" in content
    assert "reset --hard" not in content
    assert "git clean" not in content
    assert "rm -rf" not in content
    assert "git branch --show-current" in content
    assert 'current_branch != "main"' in content or '${current_branch}" != "main"' in content

    dirty_check_position = content.index("git status --porcelain")
    pull_position = content.index("git pull --ff-only") if "git pull --ff-only" in content else content.index("git merge --ff-only")
    assert dirty_check_position < pull_position
