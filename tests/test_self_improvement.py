import json

import pytest

from research_lab.self_improvement import (
    MAX_SELF_IMPROVEMENT_INPUT_ROWS,
    MAX_SELF_IMPROVEMENT_LINE_BYTES,
    _read_csv,
    _read_jsonl,
    _weak_points,
)


def test_self_improvement_jsonl_read_is_bounded(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text("\n".join(json.dumps({"id": index}) for index in range(MAX_SELF_IMPROVEMENT_INPUT_ROWS + 1)), encoding="utf-8")

    rows = _read_jsonl(path)

    assert len(rows) == MAX_SELF_IMPROVEMENT_INPUT_ROWS
    assert rows[-1] == {"id": MAX_SELF_IMPROVEMENT_INPUT_ROWS - 1}


def test_self_improvement_rejects_oversized_jsonl_and_csv_lines_before_unbounded_read(tmp_path):
    jsonl = tmp_path / "oversized.jsonl"
    csv_path = tmp_path / "oversized.csv"
    jsonl.write_bytes(b"x" * (MAX_SELF_IMPROVEMENT_LINE_BYTES + 1))
    csv_path.write_bytes(b"x" * (MAX_SELF_IMPROVEMENT_LINE_BYTES + 1))

    with pytest.raises(ValueError, match="bounded input size"):
        _read_jsonl(jsonl)
    with pytest.raises(ValueError, match="bounded input size"):
        _read_csv(csv_path)


def test_self_improvement_runtime_failure_writes_sanitized_failure_artifact(tmp_path, monkeypatch):
    import scripts.run_self_improvement as self_improvement_script

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        self_improvement_script,
        "run_self_improvement",
        lambda root: (_ for _ in ()).throw(RuntimeError("secret=top-secret")),
    )

    assert self_improvement_script.main() == 1
    payload = (tmp_path / "reports" / "operational" / "self-improvement-latest-failure.json").read_text(encoding="utf-8")
    assert "top-secret" not in payload
    assert "failure details redacted" in payload


def test_eodhd_leaderboard_is_not_reported_as_synthetic_only():
    points = _weak_points(
        [{"strategy_id": "EODHD1", "data_source": "eodhd", "tier": "C"}],
        [{"hypothesis_id": "H1"} for _ in range(5)],
        [{"hypothesis_id": "H1", "strategy_id": "EODHD1"}],
    )

    assert "Current leaderboard is synthetic-only; capital relevance is zero until real data ingestion runs." not in points
