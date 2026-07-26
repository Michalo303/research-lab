from __future__ import annotations

import copy

import pytest

from research_lab.research.minervini_eodhd_acquisition_pilot_v2 import (
    build_minervini_eodhd_acquisition_plan_v2,
    estimate_minervini_atomic_acquisition_v2,
    validate_minervini_symbol_splits_v2,
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
