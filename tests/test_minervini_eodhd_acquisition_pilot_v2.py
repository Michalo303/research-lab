from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from research_lab.research.minervini_eodhd_acquisition_pilot_v2 import (
    build_minervini_eodhd_acquisition_plan_v2,
    estimate_minervini_atomic_acquisition_v2,
    run_minervini_eodhd_acquisition_pilot_v2,
    validate_minervini_symbol_splits_v2,
)
from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    replay_minervini_pilot_artifacts_v1,
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
    return _rows("ACT", 10, anchor="AAPL")


def _delisted_rows() -> list[dict[str, object]]:
    return _rows("DEL", 10, anchor="ATVI")


def test_v2_plan_uses_only_eod_plan_capabilities():
    plan = build_minervini_eodhd_acquisition_plan_v2(
        active_rows=_active_rows(),
        delisted_rows=_delisted_rows(),
    )
    reversed_plan = build_minervini_eodhd_acquisition_plan_v2(
        active_rows=list(reversed(_active_rows())),
        delisted_rows=list(reversed(_delisted_rows())),
    )

    assert plan["version"] == "minervini_eodhd_acquisition_plan_v2"
    assert plan["provider_request_limit"] == 24
    assert len(plan["sample_symbols"]) == 11
    assert plan["sample_symbols"][0] == "SPY.US"
    assert "AAPL.US" in plan["sample_symbols"]
    assert "ATVI.US" in plan["sample_symbols"]
    assert len(plan["request_specs"]) == 22
    assert [item["kind"] for item in plan["request_specs"]] == [
        "eod",
        "splits",
    ] * 11
    assert all(
        "/symbol-change-history" not in item["endpoint_identity"]
        and "/calendar/" not in item["endpoint_identity"]
        and "api_token" not in item["endpoint_identity"]
        for item in plan["request_specs"]
    )
    assert plan["identity_continuity_mode"] == "ATOMIC_PROVIDER_TICKER"
    assert plan["rename_continuity_supported"] is False
    assert plan["output_payload_sha256"] == reversed_plan["output_payload_sha256"]


def test_v2_plan_reports_duplicates_and_blocks_cross_status_collision():
    active = _active_rows()
    active.append(copy.deepcopy(active[0]))
    delisted = _delisted_rows()
    delisted.append({**active[1], "Name": "Collision"})

    plan = build_minervini_eodhd_acquisition_plan_v2(
        active_rows=active,
        delisted_rows=delisted,
    )

    assert plan["universe"]["active_duplicate_count"] == 1
    assert plan["universe"]["active_delisted_collision_count"] == 1
    assert plan["blockers"] == ["ACTIVE_DELISTED_IDENTITY_COLLISION"]


def test_v2_plan_fills_sample_after_anchor_duplicates():
    active = _active_rows()
    active[1]["Code"] = "SPY"
    delisted = _delisted_rows()
    delisted[1]["Code"] = "AAPL"

    plan = build_minervini_eodhd_acquisition_plan_v2(
        active_rows=active,
        delisted_rows=delisted,
    )

    assert len(plan["sample_symbols"]) == 11
    assert len(set(plan["sample_symbols"])) == 11


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(Type="ETF"),
        lambda row: row.update(Code="../AAPL"),
        lambda row: row.update(Currency=""),
    ],
)
def test_v2_plan_rejects_ineligible_or_malformed_identity(mutation):
    active = _active_rows()
    mutation(active[0])

    with pytest.raises(ValueError):
        build_minervini_eodhd_acquisition_plan_v2(
            active_rows=active,
            delisted_rows=_delisted_rows(),
        )


def test_v2_split_validator_accepts_empty_and_valid_ordered_rows():
    empty = validate_minervini_symbol_splits_v2([])
    valid = validate_minervini_symbol_splits_v2(
        [
            {"date": "2014-06-09", "split": "7.000000/1.000000"},
            {"date": "2020-08-31", "split": "4.000000/1.000000"},
        ]
    )

    classification = "PROVIDER_REPORTED_EVENTS_NOT_COMPLETENESS_PROOF"
    assert empty == {
        "status": "VALID",
        "record_count": 0,
        "first_date": None,
        "last_date": None,
        "lineage_classification": classification,
    }
    assert valid == {
        "status": "VALID",
        "record_count": 2,
        "first_date": "2014-06-09",
        "last_date": "2020-08-31",
        "lineage_classification": classification,
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"date": "2020-01-01"}, "array"),
        ([{"date": "2020-01-01", "split": "0/1"}], "positive"),
        ([{"date": "2020-01-01", "split": "four/one"}], "ratio"),
        (
            [
                {"date": "2020-01-02", "split": "2/1"},
                {"date": "2020-01-01", "split": "3/1"},
            ],
            "ordered",
        ),
        (
            [
                {"date": "2020-01-01", "split": "2/1"},
                {"date": "2020-01-01", "split": "3/1"},
            ],
            "ordered",
        ),
        ([{"date": "2009-12-31", "split": "2/1"}], "frozen"),
    ],
)
def test_v2_split_validator_rejects_invalid_evidence(payload, message):
    with pytest.raises(ValueError, match=message):
        validate_minervini_symbol_splits_v2(payload)


def test_v2_estimate_is_exact_for_requests_and_sample_derived_for_storage():
    estimate = estimate_minervini_atomic_acquisition_v2(
        deduplicated_symbol_count=50_515,
        sample_summaries=[
            {
                "eod_row_count": 4_000,
                "eod_response_bytes": 600_000,
                "split_response_bytes": 120,
            },
            {
                "eod_row_count": 3_000,
                "eod_response_bytes": 510_000,
                "split_response_bytes": 80,
            },
        ],
    )

    assert estimate["full_history_eod_requests"] == 50_515
    assert estimate["per_symbol_split_requests"] == 50_515
    assert estimate["total_http_requests"] == 101_032
    assert estimate["total_call_units_exact"] is True
    assert estimate["minimum_acquisition_days_at_100000_units"] == 2
    assert estimate["storage_estimate_status"] == "ESTIMATED_FROM_SAMPLE"
    assert (
        estimate["raw_storage_bytes_upper_bound"]
        >= estimate["raw_storage_bytes_lower_bound"]
        > 0
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
    if parsed.path.startswith("/api/eod/"):
        return json.dumps(_eod_rows()).encode("utf-8")
    if parsed.path.startswith("/api/splits/"):
        symbol = parsed.path.rsplit("/", 1)[-1]
        rows = (
            [{"date": "2020-08-31", "split": "4.000000/1.000000"}]
            if symbol == "AAPL.US"
            else []
        )
        return json.dumps(rows).encode("utf-8")
    raise AssertionError(f"unexpected endpoint: {parsed.path}")


def test_v2_executor_uses_exactly_24_requests_in_frozen_pair_order(tmp_path):
    seen: list[str] = []

    def getter(url: str):
        seen.append(url)
        return _fixture_bytes_for(url), {"http_status": 200}

    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key="secret-value",
        output_dir=tmp_path / "pilot",
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )

    assert len(seen) == 24
    assert result["provider_requests_used"] == 24
    assert result["status"] == (
        "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL"
    )
    assert all(
        "/eod/" in seen[index] and "/splits/" in seen[index + 1]
        for index in range(2, 24, 2)
    )
    serialized = json.dumps(result)
    journal = (tmp_path / "pilot" / "request-journal.jsonl").read_text(
        encoding="utf-8"
    )
    assert "secret-value" not in serialized
    assert "secret-value" not in journal
    assert result["wide_acquisition_authorized"] is False
    assert result["broker_actions_used"] == 0


def test_v2_stops_on_first_403_and_classifies_capability(tmp_path):
    seen: list[str] = []

    def getter(url: str):
        seen.append(url)
        if len(seen) == 3:
            return b"Forbidden.", {"http_status": 403}
        return _fixture_bytes_for(url), {"http_status": 200}

    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key="secret-value",
        output_dir=tmp_path / "pilot",
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )

    assert len(seen) == 3
    assert result["status"] == "BLOCKED_PROVIDER_CAPABILITY"
    assert result["stopping_ordinal"] == 3
    assert result["blockers"] == ["PROVIDER_HTTP_403"]


def test_v2_missing_key_and_wrong_acknowledgement_make_zero_calls(tmp_path):
    seen: list[str] = []
    output_dir = tmp_path / "pilot"

    missing = run_minervini_eodhd_acquisition_pilot_v2(
        env={},
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=lambda url: seen.append(url),
    )
    assert missing["status"] == "MISSING_API_KEY"
    assert seen == []
    assert not output_dir.exists()

    with pytest.raises(ValueError, match="exactly 24"):
        run_minervini_eodhd_acquisition_pilot_v2(
            api_key="secret-value",
            output_dir=output_dir,
            expected_provider_requests=23,
            http_get=lambda url: seen.append(url),
        )
    assert seen == []
    assert not output_dir.exists()


def test_v2_partial_failure_remains_offline_replayable(tmp_path):
    seen: list[str] = []

    def getter(url: str):
        seen.append(url)
        if len(seen) == 8:
            raise OSError("bounded injected failure")
        return _fixture_bytes_for(url), {"http_status": 200}

    output_dir = tmp_path / "pilot"
    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key="secret-value",
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )
    calls_before_replay = len(seen)
    replay = replay_minervini_pilot_artifacts_v1(output_dir)

    assert result["status"] == "FAILED_VALIDATION"
    assert result["stopping_ordinal"] == 8
    assert replay["status"] == "VERIFIED"
    assert len(seen) == calls_before_replay


def test_v2_refuses_to_persist_provider_response_that_echoes_secret(tmp_path):
    secret = "secret-value"
    seen: list[str] = []

    def getter(url: str):
        seen.append(url)
        return f"provider echoed {secret}".encode(), {"http_status": 200}

    output_dir = tmp_path / "pilot"
    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key=secret,
        output_dir=output_dir,
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )

    persisted = b"".join(
        path.read_bytes() for path in output_dir.iterdir() if path.is_file()
    )
    assert len(seen) == 1
    assert result["status"] == "FAILED_VALIDATION"
    assert result["blockers"] == ["PROVIDER_RESPONSE_CONTAINED_SECRET"]
    assert secret.encode() not in persisted
