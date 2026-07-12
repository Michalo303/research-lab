from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)


MODULE_PATH = Path("research_lab/execution/local_ohlcv_file_input_adapter_v1.py")


def _rows() -> list[dict[str, object]]:
    return [
        {
            "timestamp": "2026-01-05T09:30:00-05:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 1_000_000,
        },
        {
            "timestamp": "2026-01-06T09:30:00-05:00",
            "open": 101.0,
            "high": 102.0,
            "low": 100.5,
            "close": 101.5,
            "volume": 1_100_000,
        },
    ]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(path: Path, *, format_name: str, timezone: str | None = None, expected_sha256: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "local_ohlcv_file_input_adapter_request_v1",
        "file_path": str(path),
        "format": format_name,
        "dataset_id": "QQQ_2026_SAMPLE",
        "symbol": "QQQ",
        "max_bytes": 1_000_000,
        "max_rows": 10,
        "provenance": {"source": "unit_test"},
    }
    if timezone is not None:
        payload["timezone"] = timezone
    if expected_sha256 is not None:
        payload["expected_sha256"] = expected_sha256
    return payload


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_local_ohlcv_file_input_adapter(copy.deepcopy(request))


def test_valid_json_and_jsonl_are_deterministic_and_bind_downstream_contract(tmp_path):
    json_path = tmp_path / "bars.json"
    jsonl_path = tmp_path / "bars.jsonl"
    _write_json(json_path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})
    _write_jsonl(jsonl_path, _rows())

    first = _run(_request(json_path, format_name="json"))
    second = _run(_request(json_path, format_name="json"))
    jsonl_result = _run(_request(jsonl_path, format_name="jsonl"))

    assert first == second
    assert first["status"] == "SUCCESS"
    assert first["dataset_id"] == "QQQ_2026_SAMPLE"
    assert first["symbol"] == "QQQ"
    assert first["row_count"] == 2
    assert first["downstream_adapter_result"]["version"] == "isolated_real_data_adapter_contract_result_v1"
    assert first["downstream_adapter_result"]["source_symbol"] == "QQQ"
    assert first["downstream_adapter_result"]["synthetic_bars"][0]["timestamp"] == "2026-01-05T14:30:00Z"
    assert jsonl_result["normalized_rows_hash"] == first["normalized_rows_hash"]
    assert first["source_file_identity"]["path"].lower().startswith(str(tmp_path.drive).lower())


def test_expected_sha_match_and_mismatch(tmp_path):
    path = tmp_path / "bars.json"
    _write_json(path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})

    matched = _run(_request(path, format_name="json", expected_sha256=_sha256(path)))
    assert matched["source_sha256"] == _sha256(path)

    with pytest.raises(ValueError, match="expected_sha256"):
        _run(_request(path, format_name="json", expected_sha256="0" * 64))


def test_file_too_large_too_many_rows_missing_file_and_directory_source_fail(tmp_path):
    path = tmp_path / "bars.json"
    _write_json(path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})
    too_many = _request(path, format_name="json")
    too_many["max_rows"] = 1
    with pytest.raises(ValueError, match="max_rows"):
        _run(too_many)

    too_large = _request(path, format_name="json")
    too_large["max_bytes"] = 1
    with pytest.raises(ValueError, match="max_bytes"):
        _run(too_large)

    with pytest.raises(ValueError, match="does not exist"):
        _run(_request(tmp_path / "missing.json", format_name="json"))

    directory = tmp_path / "dir-source"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        _run(_request(directory, format_name="json"))

    relative_request = _request(path, format_name="json")
    relative_request["file_path"] = "bars.json"
    with pytest.raises(ValueError, match="absolute local path"):
        _run(relative_request)


def test_url_uri_symlink_and_symlink_escape_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / "bars.json"
    _write_json(path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})

    with pytest.raises(ValueError, match="URL"):
        _run({**_request(path, format_name="json"), "file_path": "https://example.com/bars.json"})
    with pytest.raises(ValueError, match="URI"):
        _run({**_request(path, format_name="json"), "file_path": "file:///tmp/bars.json"})

    symlink_path = tmp_path / "bars-link.json"
    try:
        symlink_path.symlink_to(path)
    except OSError:
        monkeypatch.setattr("research_lab.execution.local_ohlcv_file_input_adapter_v1.Path.is_symlink", lambda self: str(self).endswith("bars-link.json"))
    with pytest.raises(ValueError, match="symlink"):
        _run(_request(symlink_path, format_name="json"))


def test_missing_required_field_unknown_field_duplicate_and_unordered_timestamps_fail(tmp_path):
    missing = _rows()
    del missing[0]["close"]
    missing_path = tmp_path / "missing.jsonl"
    _write_jsonl(missing_path, missing)
    with pytest.raises(ValueError, match="missing required field"):
        _run(_request(missing_path, format_name="jsonl"))

    unknown = _rows()
    unknown[0]["adj_close"] = 100.4
    unknown_path = tmp_path / "unknown.jsonl"
    _write_jsonl(unknown_path, unknown)
    with pytest.raises(ValueError, match="unknown field"):
        _run(_request(unknown_path, format_name="jsonl"))

    duplicate = _rows()
    duplicate[1]["timestamp"] = duplicate[0]["timestamp"]
    duplicate_path = tmp_path / "duplicate.jsonl"
    _write_jsonl(duplicate_path, duplicate)
    with pytest.raises(ValueError, match="strictly ordered"):
        _run(_request(duplicate_path, format_name="jsonl"))

    unordered = list(reversed(_rows()))
    unordered_path = tmp_path / "unordered.jsonl"
    _write_jsonl(unordered_path, unordered)
    with pytest.raises(ValueError, match="strictly ordered"):
        _run(_request(unordered_path, format_name="jsonl"))


def test_timezone_normalization_invalid_timestamp_and_naive_timestamp_policy(tmp_path):
    naive_rows = [
        {**_rows()[0], "timestamp": "2026-01-05T09:30:00"},
        {**_rows()[1], "timestamp": "2026-01-06T09:30:00"},
    ]
    naive_path = tmp_path / "naive.jsonl"
    _write_jsonl(naive_path, naive_rows)

    result = _run(_request(naive_path, format_name="jsonl", timezone="America/New_York"))
    assert result["first_timestamp"] == "2026-01-05T14:30:00Z"

    with pytest.raises(ValueError, match="timezone-naive"):
        _run(_request(naive_path, format_name="jsonl"))

    invalid_rows = _rows()
    invalid_rows[0]["timestamp"] = "not-a-timestamp"
    invalid_path = tmp_path / "invalid.jsonl"
    _write_jsonl(invalid_path, invalid_rows)
    with pytest.raises(ValueError, match="invalid timestamp"):
        _run(_request(invalid_path, format_name="jsonl"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("open", float("nan"), "finite"),
        ("high", float("inf"), "finite"),
        ("low", float("-inf"), "finite"),
        ("high", 99.0, "greater than or equal to open"),
        ("high", 100.4, "greater than or equal to close"),
        ("low", 100.1, "less than or equal to open"),
        ("low", 100.55, "less than or equal to close"),
        ("high", 99.75, "greater than or equal to low"),
        ("volume", 0.0, "positive"),
    ],
)
def test_invalid_numeric_and_ohlcv_relationships_fail(tmp_path, field, value, message):
    rows = _rows()
    rows[0][field] = value
    if field == "high" and value == 99.0:
        rows[0]["low"] = 98.5
    if field == "low" and value == 100.55:
        rows[0]["open"] = 100.6
        rows[0]["high"] = 101.0
    if field == "high" and value == 99.75:
        rows[0]["open"] = 99.5
        rows[0]["close"] = 99.6
        rows[0]["low"] = 99.9
    if field == "low" and value == 100.1:
        rows[0]["high"] = 101.0
    path = tmp_path / "bad.jsonl"
    _write_jsonl(path, rows)
    with pytest.raises(ValueError, match=message):
        _run(_request(path, format_name="jsonl"))


def test_dataset_and_symbol_identity_mismatch_fail(tmp_path):
    path = tmp_path / "bars.json"
    _write_json(path, {"dataset_id": "OTHER", "symbol": "SPY", "rows": _rows()})

    with pytest.raises(ValueError, match="dataset identity mismatch"):
        _run(_request(path, format_name="json"))

    _write_json(path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "SPY", "rows": _rows()})
    with pytest.raises(ValueError, match="symbol identity mismatch"):
        _run(_request(path, format_name="json"))


def test_source_unchanged_and_no_extra_files_written_on_success_and_failure(tmp_path):
    success_path = tmp_path / "success.json"
    _write_json(success_path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})
    before_success = (success_path.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))
    result = _run(_request(success_path, format_name="json"))
    after_success = (success_path.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))

    assert result["source_modified"] is False
    assert before_success == after_success

    failure_path = tmp_path / "failure.jsonl"
    bad_rows = _rows()
    bad_rows[1]["timestamp"] = bad_rows[0]["timestamp"]
    _write_jsonl(failure_path, bad_rows)
    before_failure = (failure_path.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))
    with pytest.raises(ValueError):
        _run(_request(failure_path, format_name="jsonl"))
    after_failure = (failure_path.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))
    assert before_failure == after_failure


def test_csv_returns_clear_unsupported_format_result_and_no_network_calls(tmp_path, monkeypatch):
    path = tmp_path / "bars.csv"
    path.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    result = _run(_request(path, format_name="csv"))
    assert result["status"] == "UNSUPPORTED_FORMAT"
    assert result["network_used"] is False
    assert result["provider_calls_used"] == 0


def test_module_does_not_import_provider_or_broker_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "requests",
        "urllib.request",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
