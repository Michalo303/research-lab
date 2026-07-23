from __future__ import annotations

import json
from pathlib import Path


def write_failure_artifact(
    root: Path,
    *,
    job: str,
    exc: Exception,
    started_at: str,
    finished_at: str,
) -> Path:
    """Write a small, sanitized operational failure record for a scheduled job."""
    payload = {
        "version": "operational_failure_artifact_v1",
        "job": job,
        "result_category": "failure",
        "reason_code": type(exc).__name__,
        "started_at": started_at,
        "finished_at": finished_at,
        "failure_summary": f"{type(exc).__name__}: failure details redacted",
    }
    path = root / "reports" / "operational" / f"{job}-latest-failure.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
