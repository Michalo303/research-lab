from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "exchange_session_calendar_contract_v1.py"
_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _session(session_date: str, session_type: str = "REGULAR", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_date": session_date,
        "session_type": session_type,
        "source_identity": "synthetic-xnys-calendar-v1",
        "source_sha256": _SHA_A,
        "provenance": {"source": "unit_test"},
    }
    if session_type != "CLOSED":
        payload.update(
            {
                "open_timestamp": f"{session_date}T09:30:00",
                "close_timestamp": f"{session_date}T16:00:00",
            }
        )
    else:
        payload["closure_provenance"] = {"reason": "synthetic_holiday"}
    return {**payload, **overrides}


def _request(*, sessions: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "exchange_session_calendar_contract_request_v1",
        "calendar_id": "XNYS_DAY_SYNTHETIC",
        "calendar_version": "v1",
        "timezone": "America/New_York",
        "sessions": sessions or [_session("2024-01-02"), _session("2024-01-03")],
        "holiday_policy": "EXPLICIT_CLOSED_SESSIONS",
        "partial_session_policy": "ALLOW_EXPLICIT_PARTIAL",
        "provenance": {"source": "unit_test"},
    }
    return {**payload, **overrides}


def _bar(bar_id: str, session_date: str, **overrides: object) -> dict[str, object]:
    return {
        "bar_id": bar_id,
        "session_date": session_date,
        "source_sha256": _SHA_B,
        "provenance": {"source": "unit_test"},
        **overrides,
    }


def _validation_request(bars: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    normalized = [
        {key: item[key] for key in sorted(item)}
        for item in bars
    ]
    payload: dict[str, object] = {
        "instrument_id": "spy-us",
        "calendar_id": "XNYS_DAY_SYNTHETIC",
        "bars": bars,
        "bar_interval": "P1D",
        "bar_timestamp_semantics": "SESSION_DATE",
        "expected_bars_source_sha256": _canonical_sha256(bars),
        "expected_normalized_bars_sha256": _canonical_sha256(normalized),
        "coverage_start": "2024-01-02",
        "coverage_end": "2024-01-03",
        "missing_session_policy": "FAIL_ON_ANY_MISSING",
        "unexpected_session_policy": "FAIL_ON_ANY_UNEXPECTED",
        "provenance": {"source": "unit_test"},
    }
    return {**payload, **overrides}


def _run(request: dict[str, object]) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("exchange_session_calendar_contract_v1", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_exchange_session_calendar_contract(copy.deepcopy(request))


def test_regular_partial_closed_sessions_and_safety_flags_are_normalized():
    result = _run(
        _request(
            sessions=[
                _session("2024-01-02"),
                _session("2024-01-03", "PARTIAL", close_timestamp="2024-01-03T13:00:00"),
                _session("2024-01-04", "CLOSED"),
            ]
        )
    )

    assert result["regular_session_count"] == 1
    assert result["partial_session_count"] == 1
    assert result["closed_session_count"] == 1
    assert result["UTC_session_boundaries"][0]["open_timestamp_utc"] == "2024-01-02T14:30:00Z"
    assert result["UTC_session_boundaries"][1]["close_timestamp_utc"] == "2024-01-03T18:00:00Z"
    assert result["validation_status"] == "NOT_EVALUATED"
    assert result["provider_calls_used"] == 0
    assert result["network_used"] is False
    assert result["filesystem_writes_performed"] is False
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_performed"] is False
    assert result["production_runtime_supported"] is False


@pytest.mark.parametrize(
    ("session_date", "expected_open_utc"),
    [("2024-01-02", "2024-01-02T14:30:00Z"), ("2024-07-02", "2024-07-02T13:30:00Z")],
)
def test_zoneinfo_normalizes_standard_and_daylight_time(session_date: str, expected_open_utc: str):
    result = _run(_request(sessions=[_session(session_date)]))
    assert result["UTC_session_boundaries"][0]["open_timestamp_utc"] == expected_open_utc


@pytest.mark.parametrize(
    ("session_date", "timestamp"),
    [
        ("2024-03-10", "2024-03-10T02:30:00"),
        ("2024-11-03", "2024-11-03T01:30:00"),
        ("2024-11-03", "2024-11-03T01:30:00-04:00"),
    ],
)
def test_rejects_nonexistent_and_ambiguous_dst_local_timestamps(session_date: str, timestamp: str):
    request = _request(sessions=[_session(session_date, open_timestamp=timestamp, close_timestamp=f"{session_date}T16:00:00")])
    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        _run(request)


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda request: request.update(timezone="Not/AZone"), "timezone"),
        (lambda request: request["sessions"].append(_session("2024-01-02")), "duplicate session_date"),
        (lambda request: request.update(sessions=[_session("2024-01-03"), _session("2024-01-02")]), "chronological order"),
        (lambda request: request.update(sessions=[_session("2024-01-02"), _session("2024-01-02", "PARTIAL", close_timestamp="2024-01-02T13:00:00")]), "duplicate session_date"),
        (lambda request: request.update(sessions=[_session("2024-01-02", close_timestamp="2024-01-02T09:30:00")]), "before close"),
        (lambda request: request.update(sessions=[_session("2024-01-02", open_timestamp="2024-01-02T17:00:00")]), "before close"),
        (lambda request: request.update(sessions=[_session("2024-01-02", open_timestamp=None)]), "open_timestamp"),
        (lambda request: request.update(sessions=[_session("2024-01-02", "PARTIAL", close_timestamp=None)]), "close_timestamp"),
        (lambda request: request.update(sessions=[_session("2024-01-02", "CLOSED", open_timestamp="2024-01-02T09:30:00")]), "CLOSED"),
        (lambda request: request.update(sessions=[_session("2024-07-02", open_timestamp="2024-07-02T09:30:00-05:00")]), "timezone offset"),
    ],
)
def test_rejects_malformed_or_inconsistent_sessions(mutator, match: str):
    request = _request()
    mutator(request)
    with pytest.raises(ValueError, match=match):
        _run(request)


def test_partial_policy_rejects_explicit_partial_and_unknown_fields_fail_closed():
    with pytest.raises(ValueError, match="partial_session_policy"):
        _run(_request(sessions=[_session("2024-01-02", "PARTIAL")], partial_session_policy="REJECT_PARTIAL"))

    request = _request()
    request["unknown"] = True
    with pytest.raises(ValueError, match="unknown field"):
        _run(request)


def test_tradable_sessions_reject_closure_only_provenance():
    with pytest.raises(ValueError, match="closure_provenance"):
        _run(_request(sessions=[_session("2024-01-02", closure_provenance={"reason": "invalid"})]))


def test_daily_session_date_validation_reports_complete_coverage():
    bars = [_bar("bar-a", "2024-01-02"), _bar("bar-b", "2024-01-03")]
    result = _run(_request(validation_request=_validation_request(bars)))

    summary = result["validation_summary"]
    assert result["validation_status"] == "COMPLETE"
    assert summary["expected_session_count"] == 2
    assert summary["observed_session_count"] == 2
    assert summary["matched_session_count"] == 2
    assert summary["calendar_coverage_ratio"] == 1.0
    assert summary["missing_sessions"] == []
    assert summary["unexpected_sessions"] == []


@pytest.mark.parametrize(
    "bars,validation_overrides,status,field",
    [
        ([_bar("bar-a", "2024-01-02")], {"missing_session_policy": "REPORT_ONLY"}, "PARTIAL", "missing_sessions"),
        ([_bar("bar-a", "2024-01-02")], {"missing_session_policy": "FAIL_ON_ANY_MISSING"}, "FAILED_VALIDATION", "missing_sessions"),
        ([_bar("bar-a", "2024-01-02")], {"missing_session_policy": "ALLOW_EXPLICIT_THRESHOLD", "maximum_missing_count": 1}, "PARTIAL", "missing_sessions"),
        ([_bar("bar-a", "2024-01-02"), _bar("bar-b", "2024-01-03"), _bar("bar-c", "2024-01-04")], {"unexpected_session_policy": "REPORT_ONLY"}, "PARTIAL", "unexpected_sessions"),
        ([_bar("bar-a", "2024-01-02"), _bar("bar-b", "2024-01-03"), _bar("bar-c", "2024-01-04")], {"unexpected_session_policy": "FAIL_ON_ANY_UNEXPECTED"}, "FAILED_VALIDATION", "unexpected_sessions"),
    ],
)
def test_missing_and_unexpected_policies_have_explicit_deterministic_results(bars, validation_overrides, status: str, field: str):
    result = _run(_request(validation_request=_validation_request(bars, **validation_overrides)))
    assert result["validation_status"] == status
    assert result["validation_summary"][field]


def test_validation_classifies_duplicate_closed_outside_and_boundary_bars():
    sessions = [_session("2024-01-02"), _session("2024-01-03", "CLOSED")]
    bars = [
        _bar("bar-a", "2024-01-02", timestamp="2024-01-02T14:29:00Z"),
        _bar("bar-b", "2024-01-02", timestamp="2024-01-02T21:01:00Z"),
        _bar("bar-c", "2024-01-03"),
        _bar("bar-d", "2024-01-04"),
        _bar("bar-a", "2024-01-02"),
    ]
    validation = _validation_request(
        bars,
        coverage_end="2024-01-04",
        unexpected_session_policy="REPORT_ONLY",
        missing_session_policy="REPORT_ONLY",
    )
    result = _run(_request(sessions=sessions, validation_request=validation))

    summary = result["validation_summary"]
    assert summary["duplicate_observed_sessions"] == ["2024-01-02"]
    assert summary["bars_on_closed_sessions"] == ["bar-c"]
    assert summary["bars_outside_calendar_coverage"] == ["bar-d"]
    assert summary["bars_before_open"] == ["bar-a"]
    assert summary["bars_after_close"] == ["bar-b"]


def test_open_and_close_timestamp_semantics_require_declared_boundary():
    open_bar = _bar("open", "2024-01-02", timestamp="2024-01-02T14:30:00Z")
    close_bar = _bar("close", "2024-01-02", timestamp="2024-01-02T21:00:00Z")

    open_result = _run(_request(validation_request=_validation_request([open_bar], bar_interval="PT1M", bar_timestamp_semantics="BAR_OPEN_TIME", coverage_end="2024-01-02")))
    close_result = _run(_request(validation_request=_validation_request([close_bar], bar_interval="PT1M", bar_timestamp_semantics="BAR_CLOSE_TIME", coverage_end="2024-01-02")))

    assert open_result["validation_status"] == "COMPLETE"
    assert close_result["validation_status"] == "COMPLETE"


@pytest.mark.parametrize(
    "override,match",
    [
        ({"calendar_id": "OTHER"}, "calendar_id"),
        ({"expected_bars_source_sha256": _SHA_A}, "source hash"),
        ({"expected_normalized_bars_sha256": _SHA_A}, "normalized bars hash"),
        ({"bar_timestamp_semantics": "UNKNOWN"}, "bar_timestamp_semantics"),
        ({"missing_session_policy": "ALLOW_EXPLICIT_THRESHOLD"}, "threshold"),
        ({"unexpected": True}, "unknown field"),
    ],
)
def test_validation_request_strictly_binds_identity_hashes_and_policies(override: dict[str, object], match: str):
    bars = [_bar("bar-a", "2024-01-02"), _bar("bar-b", "2024-01-03")]
    with pytest.raises(ValueError, match=match):
        _run(_request(validation_request=_validation_request(bars, **override)))


def test_repeated_results_are_deterministic_immutable_and_network_free():
    request = _request(validation_request=_validation_request([_bar("bar-a", "2024-01-02"), _bar("bar-b", "2024-01-03")]))
    original = copy.deepcopy(request)

    first = _run(request)
    second = _run(request)

    assert request == original
    assert first == second
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports.extend(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert not any(name == root or name.startswith(root + ".") for name in imports for root in ("requests", "urllib", "http", "socket", "aiohttp"))


def test_contract_builder_is_exported_from_execution_package():
    from research_lab.execution import __all__, build_exchange_session_calendar_contract

    assert callable(build_exchange_session_calendar_contract)
    assert "build_exchange_session_calendar_contract" in __all__
