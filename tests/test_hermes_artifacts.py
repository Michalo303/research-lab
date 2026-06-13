import json
from datetime import datetime, timezone

import pytest

from research_lab.hermes.artifacts import (
    dominant_blocker,
    latest_hermes_artifact,
    read_diagnostic_input,
    write_run_artifact,
)
from research_lab.hermes.artifacts import dominant_blocker


def test_daily_report_walk_forward_text_is_canonicalized():
    report = "- biggest risk discovered: insufficient rolling walk-forward robustness\n"

    assert dominant_blocker(report) == "walk_forward_fail"


NOW = datetime(2026, 6, 12, 1, 55, tzinfo=timezone.utc)


def test_prefers_latest_immutable_daily_run_report(tmp_path):
    immutable = tmp_path / "reports" / "runs" / "2026-06-11" / "run-b" / "daily_report.md"
    immutable.parent.mkdir(parents=True)
    immutable.write_text("# Daily\n- biggest risk discovered: excessive drawdown\n", encoding="utf-8")
    daily = tmp_path / "reports" / "daily" / "2026-06-12.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("# Daily fallback\n", encoding="utf-8")

    diagnostic = read_diagnostic_input(tmp_path)

    assert diagnostic.path == immutable
    assert "excessive drawdown" in diagnostic.text
    assert diagnostic.blocker == "drawdown"


def test_falls_back_to_latest_daily_report(tmp_path):
    daily = tmp_path / "reports" / "daily" / "2026-06-12.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("# Daily\n- LONGTERM: Unseen max drawdown exceeds 15%.\n", encoding="utf-8")

    diagnostic = read_diagnostic_input(tmp_path)

    assert diagnostic.path == daily
    assert diagnostic.blocker == "drawdown"


def test_dominant_blocker_has_safe_fallback():
    assert dominant_blocker("# Empty report") == "no explicit blocker found"


def test_writes_immutable_artifact_and_refuses_collision(tmp_path):
    artifact = {
        "run_id": "20260612T015500000000Z-abcdef1",
        "timestamp_utc": NOW.isoformat(),
        "provider": "command",
        "status": "ok",
    }

    path = write_run_artifact(tmp_path, artifact, timestamp=NOW)

    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == artifact["run_id"]
    with pytest.raises(FileExistsError):
        write_run_artifact(tmp_path, artifact, timestamp=NOW)


def test_latest_artifact_respects_daily_run_timestamp(tmp_path):
    before = {
        "run_id": "before",
        "timestamp_utc": "2026-06-12T01:55:00+00:00",
        "provider": "command",
        "status": "ok",
    }
    after = {
        "run_id": "after",
        "timestamp_utc": "2026-06-12T03:00:00+00:00",
        "provider": "command",
        "status": "ok",
    }
    write_run_artifact(tmp_path, before, timestamp=NOW)
    write_run_artifact(tmp_path, after, timestamp=datetime(2026, 6, 12, 3, 0, tzinfo=timezone.utc))

    selected = latest_hermes_artifact(tmp_path, before=datetime(2026, 6, 12, 2, 30, tzinfo=timezone.utc))

    assert selected["run_id"] == "before"
    assert selected["artifact_path"].endswith("before.json")


def test_latest_artifact_prefers_terminal_phase_over_precommit_at_same_timestamp(tmp_path):
    validated = {
        "run_id": "two-phase",
        "timestamp_utc": NOW.isoformat(),
        "provider": "command",
        "status": "validated",
        "artifact_phase": "artifact_written",
    }
    committed = {
        **validated,
        "status": "ok",
        "artifact_phase": "queue_committed",
    }
    write_run_artifact(tmp_path, validated, timestamp=NOW, suffix="validated")
    write_run_artifact(tmp_path, committed, timestamp=NOW)

    selected = latest_hermes_artifact(tmp_path)

    assert selected["status"] == "ok"
    assert selected["artifact_phase"] == "queue_committed"
    assert selected["artifact_path"].endswith("two-phase.json")
