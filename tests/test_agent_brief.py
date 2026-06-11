import subprocess
import sys
from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_agent_brief_prints_latest_daily_summary_and_next_actions(tmp_path: Path):
    _write(
        tmp_path / "AGENTS.md",
        "\n".join(
            [
                "# Agent Guidance",
                "",
                "The current research problem is strategy quality, not pipeline availability.",
                "",
                "Primary blockers observed in daily reports:",
                "",
                "- insufficient rolling walk-forward robustness;",
                "- excessive unseen max drawdown;",
                "- too many weak or duplicate candidate variants;",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "reports" / "daily" / "2026-06-01.md",
        "# Daily Research Report - 2026-06-01\n\n## Summary\n\n- old report\n",
    )
    _write(
        tmp_path / "reports" / "daily" / "2026-06-05.md",
        "\n".join(
            [
                "# Daily Research Report - 2026-06-05",
                "",
                "## Summary",
                "",
                "- experiments run: 12",
                "- accepted: 0",
                "- rejected: 11",
                "- best research result: ROTATION_ETF_1D_DEFENSIVE_ROTATION_20260605_008",
                "",
                "## Rejections",
                "",
                "- LONGTERM_ETF_1D_TREND_FILTER_20260605_001: Unseen max drawdown exceeds 15%.",
                "- LONGTERM_ETF_1D_TREND_VOL_CAP_20260605_006: Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                "",
                "## Next Actions",
                "",
                "- Add walk-forward and parameter-neighborhood stability for the weekly deep run.",
                "- Keep deployment blocked until paper validation and existing gates pass.",
                "",
            ]
        ),
    )
    _write(
        tmp_path / "registry" / "leaderboard.csv",
        "strategy_id,tier,unseen_cagr,unseen_max_drawdown\n"
        "LONGTERM_ETF_1D_TREND_VOL_CAP_20260605_006,C,0.0399,-0.1339\n",
    )

    script = Path(__file__).resolve().parents[1] / "scripts" / "agent_brief.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=True,
    )

    output = result.stdout

    assert "# Agent Brief" in output
    assert "latest_daily_report: reports/daily/2026-06-05.md" in output
    assert "experiments run: 12" in output
    assert "accepted: 0" in output
    assert "best research result: ROTATION_ETF_1D_DEFENSIVE_ROTATION_20260605_008" in output
    assert "Unseen max drawdown exceeds 15%" in output
    assert "Add walk-forward and parameter-neighborhood stability" in output
    assert "Keep deployment blocked" in output
    assert "Do not weaken validation gates" in output
    assert "Follow AGENTS.md" in output
    assert "Do not start by reading large generated artifacts" in output
    assert "Choose one narrow next action after this brief" in output
    assert "Avoid reading INVENTORY_full_diff.patch" in output
    assert "old report" not in output


def test_agent_brief_handles_missing_artifacts_without_failing(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "agent_brief.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=True,
    )

    output = result.stdout

    assert "# Agent Brief" in output
    assert "latest_daily_report: not found" in output
    assert "leaderboard: not found" in output
    assert "Next safe action" in output
