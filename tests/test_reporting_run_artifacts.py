from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_lab.reports import classify_git_dirty_paths, write_daily_report_artifacts


def test_daily_report_artifacts_include_run_id_metadata_and_expected_paths(tmp_path):
    timestamp = datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc)
    git_info = {"commit": "abcdef1234567890", "branch": "codex/test", "dirty": False}

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=timestamp,
        git_info=git_info,
        command=["scripts/run_daily_research.py"],
        runner_name="run_daily_research",
    )

    assert outcome["metadata"]["run_id"] == "20260603T120304000000Z-abcdef1"
    assert outcome["latest_report_path"] == tmp_path / "reports" / "daily" / "2026-06-03.md"
    assert outcome["run_report_path"] == (
        tmp_path
        / "reports"
        / "runs"
        / "2026-06-03"
        / "20260603T120304000000Z-abcdef1"
        / "daily_report.md"
    )
    assert outcome["metadata_path"] == outcome["run_report_path"].with_name("run_metadata.json")
    assert outcome["latest_report_path"].read_text(encoding="utf-8").startswith("# Daily Research Report - 2026-06-03")
    assert outcome["run_report_path"].read_text(encoding="utf-8") == outcome["latest_report_path"].read_text(encoding="utf-8")

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["run_id"] == "20260603T120304000000Z-abcdef1"
    assert metadata["timestamp_utc"] == "2026-06-03T12:03:04+00:00"
    assert metadata["git"] == {
        **git_info,
        "code_dirty": False,
        "runtime_artifacts_dirty": False,
        "dirty_files": [],
        "dirty_classification": "clean",
    }
    assert metadata["runner"] == "run_daily_research"
    assert metadata["command"] == ["scripts/run_daily_research.py"]
    assert metadata["latest_report_path"] == "reports/daily/2026-06-03.md"
    assert metadata["run_report_path"] == "reports/runs/2026-06-03/20260603T120304000000Z-abcdef1/daily_report.md"
    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "- git_dirty: False" in report
    assert "- code_dirty: False" in report
    assert "- runtime_artifacts_dirty: False" in report
    assert "- dirty_classification: clean" in report
    assert "- dirty_files: none" in report


def test_two_runs_on_same_date_create_distinct_immutable_artifacts(tmp_path):
    first = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )
    second = write_daily_report_artifacts(
        tmp_path,
        [_result("massive")],
        timestamp=datetime(2026, 6, 3, 12, 4, 5, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    assert first["latest_report_path"] == second["latest_report_path"]
    assert first["run_report_path"] != second["run_report_path"]
    assert first["run_report_path"].exists()
    assert second["run_report_path"].exists()
    assert "- data sources: massive" in second["latest_report_path"].read_text(encoding="utf-8")
    assert "- data sources: eodhd" in first["run_report_path"].read_text(encoding="utf-8")


def test_reusing_same_run_id_requires_explicit_allowance(tmp_path):
    kwargs = {
        "timestamp": datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        "git_info": {"commit": "abcdef1234567890", "branch": "main", "dirty": False},
        "run_id": "manual-rerun-id",
    }

    write_daily_report_artifacts(tmp_path, [_result("eodhd")], **kwargs)

    with pytest.raises(FileExistsError, match="manual-rerun-id"):
        write_daily_report_artifacts(tmp_path, [_result("massive")], **kwargs)

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("massive")],
        allow_existing_run_id=True,
        **kwargs,
    )
    assert outcome["run_report_path"].read_text(encoding="utf-8").startswith("# Daily Research Report")


def test_metadata_includes_provider_history_summary_without_secrets(tmp_path):
    result = _result("eodhd")
    result["data_manifest"] |= {
        "api_key": "SUPERSECRET",
        "symbol_diagnostics": [
            {
                "requested_symbol": "SPY",
                "selected_provider": "eodhd",
                "first_date": "1993-01-29",
                "last_date": "2026-06-02",
                "daily_bars": 8390,
                "history_years": 33.3,
                "token": "SUPERSECRET",
            }
        ],
    }

    outcome = write_daily_report_artifacts(
        tmp_path,
        [result],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": True},
        command=["python", "scripts/run_daily_research.py", "--api-key", "SUPERSECRET", "--token=SUPERSECRET"],
    )

    metadata_text = outcome["metadata_path"].read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    assert metadata["data_sources"] == ["eodhd"]
    assert metadata["provider_history_summary"] == [
        {
            "source": "eodhd",
            "start": "1993-01-29",
            "end": "2026-06-02",
            "rows": 9000,
            "years": 33.3,
            "symbols": ["SPY"],
            "symbol_history": [
                {
                    "requested_symbol": "SPY",
                    "selected_provider": "eodhd",
                    "first_date": "1993-01-29",
                    "last_date": "2026-06-02",
                    "daily_bars": 8390,
                    "history_years": 33.3,
                }
            ],
        }
    ]
    assert "SUPERSECRET" not in metadata_text
    assert metadata["command"] == ["python", "scripts/run_daily_research.py", "--api-key", "<redacted>", "--token=<redacted>"]


def test_existing_caller_can_still_read_latest_daily_report_path(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    latest_report_path = tmp_path / "reports" / "daily" / "2026-06-03.md"
    assert outcome["latest_report_path"] == latest_report_path
    report = latest_report_path.read_text(encoding="utf-8")
    assert "# Daily Research Report - 2026-06-03" in report
    assert "| S1 | LONGTERM | ETF | 1D | eodhd |" in report


def test_report_metadata_separates_runtime_artifact_dirty_from_code_dirty(tmp_path):
    git_info = {
        "commit": "abcdef1234567890",
        "branch": "main",
        **classify_git_dirty_paths(["registry/leaderboard.csv"]),
    }

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info=git_info,
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["git"]["dirty"] is True
    assert metadata["git"]["code_dirty"] is False
    assert metadata["git"]["runtime_artifacts_dirty"] is True
    assert metadata["git"]["dirty_files"] == ["registry/leaderboard.csv"]
    assert metadata["git"]["dirty_classification"] == "runtime_artifacts_only"

    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "- git_dirty: True" in report
    assert "- code_dirty: False" in report
    assert "- runtime_artifacts_dirty: True" in report
    assert "- dirty_classification: runtime_artifacts_only" in report
    assert "- dirty_files: registry/leaderboard.csv" in report


def test_report_artifacts_are_written_only_under_supplied_root(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    for path in (outcome["latest_report_path"], outcome["run_report_path"], outcome["metadata_path"]):
        assert path.is_relative_to(tmp_path)


def test_report_artifact_writer_does_not_stage_runtime_outputs(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert staged.stdout == ""


@pytest.mark.parametrize(
    ("dirty_paths", "expected"),
    [
        (
            [],
            {
                "dirty": False,
                "code_dirty": False,
                "runtime_artifacts_dirty": False,
                "dirty_files": [],
                "dirty_classification": "clean",
            },
        ),
        (
            ["registry/leaderboard.csv"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/leaderboard.csv"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            ["research_lab/runner.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["research_lab/runner.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["registry/leaderboard.csv", "research_lab/runner.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/leaderboard.csv", "research_lab/runner.py"],
                "dirty_classification": "mixed_code_and_runtime_dirty",
            },
        ),
        (
            ["tests/test_x.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["tests/test_x.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["data/manifests/eodhd_manifest.json"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/eodhd_manifest.json"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
    ],
)
def test_classify_git_dirty_paths(dirty_paths, expected):
    assert classify_git_dirty_paths(dirty_paths) == expected


def _result(source: str) -> dict:
    return {
        "strategy_id": "S1",
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": "TREND",
        "hypothesis": "test",
        "rules": "test",
        "parameters": {},
        "data_manifest": {
            "source": source,
            "start": "1993-01-29",
            "end": "2026-06-02",
            "rows": 9000,
            "years": 33.3,
            "symbols": ["SPY"],
        },
        "data_source": source,
        "cost_stress": {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": True,
            "double_unseen_cagr": 0.05,
        },
        "split_metrics": {
            "train": {"cagr": 0.1},
            "validation": {"cagr": 0.1},
            "unseen": {
                "cagr": 0.1,
                "sharpe": 1.0,
                "mar": 1.0,
                "max_drawdown": -0.05,
                "profit_factor": 1.2,
                "trade_count": 10,
            },
        },
        "tier": "C",
        "tier_reason": "test",
        "average_exposure": 1.0,
        "average_turnover": 0.1,
    }
