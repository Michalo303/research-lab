import json

import pytest

from research_lab.operational_runtime import write_failure_artifact
from research_lab.edge import MAX_EDGE_AUDIT_LINE_BYTES, _read_jsonl as read_edge_jsonl


def test_failure_artifact_is_bounded_redacted_and_deterministic(tmp_path):
    artifact = write_failure_artifact(
        tmp_path,
        job="daily",
        exc=RuntimeError("provider failed API_KEY=top-secret token=abc123"),
        started_at="2026-07-23T05:00:00+00:00",
        finished_at="2026-07-23T05:00:01+00:00",
    )

    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert artifact == tmp_path / "reports" / "operational" / "daily-latest-failure.json"
    assert payload == {
        "version": "operational_failure_artifact_v1",
        "job": "daily",
        "result_category": "failure",
        "reason_code": "RuntimeError",
        "started_at": "2026-07-23T05:00:00+00:00",
        "finished_at": "2026-07-23T05:00:01+00:00",
        "failure_summary": "RuntimeError: failure details redacted",
    }


def test_edge_audit_rejects_oversized_jsonl_line_before_unbounded_read(tmp_path):
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(b"x" * (MAX_EDGE_AUDIT_LINE_BYTES + 1))

    with pytest.raises(ValueError, match="bounded input size"):
        read_edge_jsonl(path)
