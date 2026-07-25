from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from research_lab.research.minervini_eodhd_acquisition_pilot_v1 import (
    analyze_minervini_split_coverage_v1,
    analyze_minervini_symbol_changes_v1,
    build_minervini_eodhd_acquisition_plan_v1,
    estimate_minervini_wide_acquisition_v1,
    run_minervini_eodhd_acquisition_pilot_v1,
    validate_minervini_eod_sample_v1,
)


def _rows(prefix: str, count: int, *, anchor: str) -> list[dict[str, object]]:
    codes = [anchor, *(f"{prefix}{number:02d}" for number in range(count - 1))]
    return [
        {
            "Code": code,
            "Name": f"{code} Company",
            "Country": "USA",
            "Exchange": "NASDAQ" if number % 2 == 0 else "NYSE",
            "Currency": "USD",
            "Type": "Common Stock",
            "Isin": f"US{number:010d}",
        }
        for number, code in enumerate(codes)
    ]


def _active_rows() -> list[dict[str, object]]:
    return _rows("ACT", 14, anchor="AAPL")


def _delisted_rows() -> list[dict[str, object]]:
    return _rows("DEL", 14, anchor="ATVI")


def test_plan_has_exactly_24_secret_free_requests_and_deterministic_sample():
    plan = build_minervini_eodhd_acquisition_plan_v1(
        active_rows=_active_rows(),
        delisted_rows=_delisted_rows(),
    )
    reversed_plan = build_minervini_eodhd_acquisition_plan_v1(
        active_rows=list(reversed(_active_rows())),
        delisted_rows=list(reversed(_delisted_rows())),
    )

    assert plan["version"] == "minervini_eodhd_acquisition_plan_v1"
    assert plan["provider_request_limit"] == 24
    assert len(plan["sample_symbols"]) == 20
    assert plan["sample_symbols"][0] == "SPY.US"
    assert "AAPL.US" in plan["sample_symbols"]
    assert "ATVI.US" in plan["sample_symbols"]
    assert len(plan["request_specs"]) == 22
    assert all(
        "api_token" not in item["endpoint_identity"]
        for item in plan["request_specs"]
    )
    assert plan["output_payload_sha256"] == reversed_plan["output_payload_sha256"]


def test_plan_reports_duplicates_and_blocks_active_delisted_collision():
    active = _active_rows()
    active.append(dict(active[0]))
    delisted = _delisted_rows()
    delisted.append(
        {
            **active[1],
            "Name": "Colliding Identity",
        }
    )

    plan = build_minervini_eodhd_acquisition_plan_v1(
        active_rows=active,
        delisted_rows=delisted,
    )

    assert plan["universe"]["active_duplicate_count"] == 1
    assert plan["universe"]["active_delisted_collision_count"] == 1
    assert "ACTIVE_DELISTED_IDENTITY_COLLISION" in plan["blockers"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(Type="ETF"),
        lambda row: row.update(Code="../AAPL"),
        lambda row: row.update(Currency=""),
    ],
)
def test_plan_rejects_non_common_or_malformed_identity(mutation):
    active = _active_rows()
    mutation(active[0])

    with pytest.raises(ValueError):
        build_minervini_eodhd_acquisition_plan_v1(
            active_rows=active,
            delisted_rows=_delisted_rows(),
        )


def _eod_rows() -> list[dict[str, object]]:
    return [
        {
            "date": f"2025-01-0{day}",
            "open": 100.0 + day,
            "high": 102.0 + day,
            "low": 99.0 + day,
            "close": 101.0 + day,
            "adjusted_close": 100.5 + day,
            "volume": 1_000_000,
        }
        for day in range(2, 5)
    ]


def test_eod_validation_rejects_unordered_duplicate_and_invalid_ohlcv():
    unordered = _eod_rows()
    unordered.reverse()
    with pytest.raises(ValueError, match="strictly ordered"):
        validate_minervini_eod_sample_v1(unordered)

    invalid = _eod_rows()
    invalid[1]["high"] = 90.0
    with pytest.raises(ValueError, match="OHLC"):
        validate_minervini_eod_sample_v1(invalid)


def test_eod_validation_reports_usable_coverage():
    summary = validate_minervini_eod_sample_v1(_eod_rows())

    assert summary == {
        "status": "VALID",
        "first_date": "2025-01-02",
        "last_date": "2025-01-04",
        "row_count": 3,
        "gap_count": 0,
    }


def test_symbol_change_cycles_block_readiness():
    analysis = analyze_minervini_symbol_changes_v1(
        [
            {
                "old_symbol": "AAA",
                "new_symbol": "BBB",
                "effective": "2020-01-02",
                "exchange": "US",
            },
            {
                "old_symbol": "BBB",
                "new_symbol": "AAA",
                "effective": "2021-01-04",
                "exchange": "US",
            },
        ]
    )

    assert "SYMBOL_CHANGE_CYCLE" in analysis["blockers"]


def test_split_analysis_preserves_unknown_page_count():
    analysis = analyze_minervini_split_coverage_v1(
        {
            "type": "Splits",
            "from": "2010-01-01",
            "to": "2025-12-31",
            "splits": [
                {
                    "code": "AAPL.US",
                    "split_date": "2020-08-31",
                    "old_shares": 1,
                    "new_shares": 4,
                }
            ],
        }
    )

    assert analysis["coverage_complete"] is True
    assert analysis["page_count"] is None
    assert analysis["record_count"] == 1


def test_estimate_is_deterministic_and_never_claims_unknown_split_pages_exact():
    summaries = [
        {
            "row_count": 4_000,
            "response_bytes": 600_000 + number,
        }
        for number in range(20)
    ]

    estimate = estimate_minervini_wide_acquisition_v1(
        deduplicated_symbol_count=50_000,
        sample_summaries=summaries,
        split_metadata={"coverage_complete": False, "page_count": None},
    )

    assert estimate["full_history_eod_requests"] == 50_000
    assert estimate["split_request_upper_bound"] == 50_000
    assert estimate["total_call_units_exact"] is False
    assert estimate["minimum_runtime_seconds_at_1000_per_minute"] == 6_001
    assert estimate["conservative_runtime_seconds_at_5_per_second"] == 20_001


def _fixture_bytes_for(url: str) -> bytes:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path == "/api/exchange-symbol-list/US":
        rows = (
            _delisted_rows()
            if query.get("delisted") == ["1"]
            else _active_rows()
        )
        return json.dumps(rows).encode("utf-8")
    if parsed.path == "/api/symbol-change-history":
        return b"[]"
    if parsed.path == "/api/calendar/splits":
        return json.dumps(
            {
                "type": "Splits",
                "from": "2010-01-01",
                "to": "2025-12-31",
                "splits": [
                    {
                        "code": "AAPL.US",
                        "split_date": "2020-08-31",
                        "old_shares": 1,
                        "new_shares": 4,
                    }
                ],
            }
        ).encode("utf-8")
    if parsed.path.startswith("/api/eod/"):
        return json.dumps(_eod_rows()).encode("utf-8")
    raise AssertionError(f"unexpected endpoint: {parsed.path}")


def _successful_getter(seen: list[str]):
    def getter(url: str):
        seen.append(url)
        return _fixture_bytes_for(url), {
            "http_status": 200,
            "content_type": "application/json",
        }

    return getter


def test_executor_uses_exactly_24_requests_without_retry_and_redacts_secret(
    tmp_path,
):
    seen: list[str] = []
    output_dir = tmp_path / "pilot"

    result = run_minervini_eodhd_acquisition_pilot_v1(
        api_key="secret-value",
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=_successful_getter(seen),
        now_utc=lambda: "2026-07-25T20:00:00Z",
    )

    assert result["provider_requests_used"] == 24
    assert len(seen) == 24
    assert result["status"] == "READY_FOR_WIDE_ACQUISITION_APPROVAL"
    assert "secret-value" not in json.dumps(result)
    assert "secret-value" not in (
        output_dir / "request-journal.jsonl"
    ).read_text(encoding="utf-8")
    assert (output_dir / "pilot-result-manifest.json").is_file()


def test_executor_rejects_bad_ack_or_missing_key_before_calls_and_writes(
    tmp_path,
):
    calls: list[str] = []
    output_dir = tmp_path / "pilot"
    with pytest.raises(ValueError, match="24"):
        run_minervini_eodhd_acquisition_pilot_v1(
            api_key="secret-value",
            output_dir=output_dir,
            expected_provider_requests=23,
            http_get=lambda url: calls.append(url),
        )
    assert calls == []
    assert not output_dir.exists()

    result = run_minervini_eodhd_acquisition_pilot_v1(
        env={},
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=lambda url: calls.append(url),
    )
    assert result["status"] == "MISSING_API_KEY"
    assert calls == []
    assert not output_dir.exists()


def test_executor_stops_once_on_failure_and_finalizes_partial_evidence(tmp_path):
    calls: list[str] = []

    def getter(url: str):
        calls.append(url)
        if len(calls) == 3:
            raise RuntimeError("secret-value must not escape")
        return _fixture_bytes_for(url), {"http_status": 200}

    output_dir = tmp_path / "pilot"
    result = run_minervini_eodhd_acquisition_pilot_v1(
        api_key="secret-value",
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-25T20:00:00Z",
    )

    assert len(calls) == 3
    assert result["status"] == "FAILED_VALIDATION"
    assert result["stopping_ordinal"] == 3
    assert "secret-value" not in json.dumps(result)
    assert (output_dir / "pilot-result-manifest.json").is_file()
