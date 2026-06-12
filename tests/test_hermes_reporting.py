from datetime import datetime, timezone

from research_lab.hermes.artifacts import write_run_artifact
from research_lab.reports import write_daily_report_artifacts


DAILY_TIME = datetime(2026, 6, 12, 2, 30, tzinfo=timezone.utc)


def _artifact(run_id, timestamp, **overrides):
    item = {
        "run_id": run_id,
        "timestamp_utc": timestamp.isoformat(),
        "provider": "command",
        "status": "completed_with_rejections",
        "generated_hypotheses_count": 3,
        "imported_hypotheses_count": 1,
        "rejected_hypotheses_count": 2,
        "rejection_reasons": ["hypothesis_2:builder_not_allowed", "hypothesis_3:duplicate_hypothesis"],
        "imported_hypothesis_ids": ["HERMES_RUN_001"],
        "input_report_path": "reports/daily/2026-06-11.md",
        "dominant_blocker": "unseen max drawdown exceeds 15%",
    }
    item.update(overrides)
    return item


def test_daily_report_includes_latest_eligible_hermes_provenance(tmp_path):
    hermes_time = datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc)
    write_run_artifact(tmp_path, _artifact("hermes-before", hermes_time), timestamp=hermes_time)

    outcome = write_daily_report_artifacts(
        tmp_path,
        [],
        timestamp=DAILY_TIME,
        git_info={"commit": "abcdef1", "branch": "test", "dirty": False, "dirty_paths": []},
    )

    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "## Hermes Pre-Research Stage" in report
    assert "- Hermes ran: yes" in report
    assert "- provider: command" in report
    assert "- status: completed_with_rejections" in report
    assert "- generated hypotheses: 3" in report
    assert "- imported hypotheses: 1" in report
    assert "- rejected hypotheses: 2" in report
    assert "hypothesis_2:builder_not_allowed" in report
    assert outcome["metadata"]["hermes"]["run_id"] == "hermes-before"
    assert outcome["metadata"]["hermes"]["artifact_path"].endswith("hermes-before.json")


def test_daily_report_ignores_future_hermes_artifact(tmp_path):
    future = datetime(2026, 6, 12, 3, 0, tzinfo=timezone.utc)
    write_run_artifact(tmp_path, _artifact("future", future), timestamp=future)

    outcome = write_daily_report_artifacts(
        tmp_path,
        [],
        timestamp=DAILY_TIME,
        git_info={"commit": "abcdef1", "branch": "test", "dirty": False, "dirty_paths": []},
    )

    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "- Hermes ran: no" in report
    assert outcome["metadata"]["hermes"] is None


def test_daily_report_surfaces_provider_unavailable_without_failure(tmp_path):
    hermes_time = datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc)
    write_run_artifact(
        tmp_path,
        _artifact(
            "unavailable",
            hermes_time,
            provider="not_configured",
            status="provider_unavailable",
            generated_hypotheses_count=0,
            imported_hypotheses_count=0,
            rejected_hypotheses_count=0,
            rejection_reasons=["unsupported Hermes provider: not configured"],
            imported_hypothesis_ids=[],
        ),
        timestamp=hermes_time,
    )

    outcome = write_daily_report_artifacts(
        tmp_path,
        [],
        timestamp=DAILY_TIME,
        git_info={"commit": "abcdef1", "branch": "test", "dirty": False, "dirty_paths": []},
    )

    report = outcome["latest_report_path"].read_text(encoding="utf-8")
    assert "- status: provider_unavailable" in report
    assert "unsupported Hermes provider: not configured" in report
