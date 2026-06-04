import json

from research_lab.queue_dedupe import audit_queue_file, candidate_fingerprint, dedupe_candidates


def _record(**overrides):
    base = {
        "hypothesis_id": "H1",
        "family": "SWING",
        "asset_class": "ETF",
        "timeframe": "1D",
        "template": "rsi_pullback",
        "parameters": {"symbol": "SPY", "rsi_entry": 35, "rsi_exit": 55},
        "filters": {"trend": {"sma": 100}},
        "risk_controls": {"max_position_weight": 0.75},
        "created_at": "2026-06-04T01:00:00Z",
        "run_id": "run-a",
        "report_path": "reports/a.md",
    }
    base.update(overrides)
    return base


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) if isinstance(row, dict) else row for row in rows) + "\n", encoding="utf-8")


def test_exact_duplicate_records_dedupe_keeps_first():
    first = _record(hypothesis_id="first")
    second = dict(first)
    second["hypothesis_id"] = "second"

    result = dedupe_candidates([first, second])

    assert [item["hypothesis_id"] for item in result.retained_records] == ["first"]
    assert [item["hypothesis_id"] for item in result.duplicate_records] == ["second"]
    assert result.input_count == 2
    assert result.retained_count == 1
    assert result.duplicate_count == 1


def test_json_key_order_does_not_affect_fingerprint():
    first = _record(parameters={"symbol": "SPY", "rsi_entry": 35, "rsi_exit": 55})
    second = {
        "risk_controls": {"max_position_weight": 0.75},
        "filters": {"trend": {"sma": 100}},
        "parameters": {"rsi_exit": 55, "rsi_entry": 35, "symbol": "SPY"},
        "template": "rsi_pullback",
        "timeframe": "1D",
        "asset_class": "ETF",
        "family": "SWING",
    }

    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_parameter_order_and_numeric_format_do_not_affect_fingerprint():
    first = _record(parameters={"symbols": ["SPY", "QQQ"], "lookback": 126, "target_vol": 0.10})
    second = _record(parameters={"target_vol": 0.1000, "lookback": 126.0, "symbols": ["QQQ", "SPY"]})

    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_non_semantic_timestamps_run_id_and_notes_do_not_affect_fingerprint():
    first = _record(created_at="2026-06-04T01:00:00Z", run_id="run-a", notes="audit note")
    second = _record(created_at="2026-06-04T02:00:00Z", run_id="run-b", notes="different audit note")

    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_semantically_different_parameters_produce_different_fingerprints():
    first = _record(parameters={"symbol": "SPY", "rsi_entry": 35, "rsi_exit": 55})
    second = _record(parameters={"symbol": "SPY", "rsi_entry": 30, "rsi_exit": 55})

    assert candidate_fingerprint(first) != candidate_fingerprint(second)


def test_malformed_records_are_retained_and_warned(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    _write_jsonl(queue, [_record(hypothesis_id="A"), "{not valid json", _record(hypothesis_id="B")])

    result = audit_queue_file(queue)

    assert result.input_count == 3
    assert result.retained_count == 2
    assert result.duplicate_count == 1
    assert result.malformed_count == 1
    assert any("line 2" in warning for warning in result.warnings)
    assert queue.read_text(encoding="utf-8").splitlines()[1] == "{not valid json"


def test_retained_order_preserves_first_occurrence():
    unique = _record(hypothesis_id="unique", parameters={"symbol": "QQQ", "rsi_entry": 35, "rsi_exit": 55})
    first = _record(hypothesis_id="first")
    second = _record(hypothesis_id="second")

    result = dedupe_candidates([unique, first, second])

    assert [item["hypothesis_id"] for item in result.retained_records] == ["unique", "first"]


def test_dry_run_mode_does_not_modify_queue_file(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    _write_jsonl(queue, [_record(hypothesis_id="A"), _record(hypothesis_id="B")])
    before = queue.read_text(encoding="utf-8")

    result = audit_queue_file(queue, write=False)

    assert result.applied is False
    assert result.backup_path is None
    assert queue.read_text(encoding="utf-8") == before


def test_write_mode_creates_backup_and_writes_retained_records(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    _write_jsonl(queue, [_record(hypothesis_id="A"), _record(hypothesis_id="B")])

    result = audit_queue_file(queue, write=True, backup_stamp="20260604T120000Z")

    assert result.applied is True
    assert result.backup_path is not None
    assert result.backup_path.name == "hypothesis_queue.20260604T120000Z.before_dedupe.jsonl"
    assert result.backup_path.read_text(encoding="utf-8").count("\n") == 2
    lines = queue.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["hypothesis_id"] == "A"


def test_diagnostics_report_counts_fingerprints_groups_reasons_and_backup(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    _write_jsonl(queue, [_record(hypothesis_id="A"), _record(hypothesis_id="B"), "{broken"])

    result = audit_queue_file(queue, write=True, backup_stamp="20260604T120000Z")
    diagnostics = result.to_dict()

    assert diagnostics["input_count"] == 3
    assert diagnostics["retained_count"] == 2
    assert diagnostics["duplicate_count"] == 1
    assert diagnostics["malformed_count"] == 1
    assert diagnostics["fingerprints_generated"] == 2
    assert diagnostics["duplicate_groups"][0]["retained_index"] == 0
    assert diagnostics["duplicate_groups"][0]["duplicate_indices"] == [1]
    assert diagnostics["reasons"]["duplicate"] == 1
    assert diagnostics["warnings"]
    assert diagnostics["backup_path"].endswith("hypothesis_queue.20260604T120000Z.before_dedupe.jsonl")
