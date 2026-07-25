from __future__ import annotations

import pytest

from research_lab.research.minervini_eodhd_acquisition_pilot_v1 import (
    build_minervini_eodhd_acquisition_plan_v1,
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
