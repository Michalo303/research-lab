from __future__ import annotations

from research_lab.research.massive_fundamental_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_massive_fundamental_catalog_metadata_v1,
)


FACTOR_IDS = (
    "GROSS_PROFITABILITY",
    "OPERATING_RETURN_ON_CAPITAL",
    "CASH_PROFITABILITY",
    "ACCRUAL_QUALITY",
    "REVENUE_GROWTH",
    "EARNINGS_IMPROVEMENT",
    "MARGIN_STABILITY",
    "LOW_LEVERAGE",
    "LOW_ASSET_GROWTH",
    "QUALITY_MOMENTUM",
)


def test_catalog_is_closed_ordered_and_hash_bound() -> None:
    assert tuple(FACTOR_DEFINITIONS_V1) == FACTOR_IDS
    assert all(
        definition["factor_id"] == factor_id
        for factor_id, definition in FACTOR_DEFINITIONS_V1.items()
    )
    assert all(definition["higher_is_better"] is True for definition in FACTOR_DEFINITIONS_V1.values())

    metadata = build_massive_fundamental_catalog_metadata_v1()

    assert metadata["version"] == "massive_fundamental_catalog_v1"
    assert metadata["factor_count"] == 10
    assert metadata["ordered_factor_ids"] == list(FACTOR_IDS)
    assert metadata["quarterly_flow_periods"] == 4
    assert metadata["margin_stability_periods"] == 8
    assert metadata["availability_lag"] == "first_verified_session_strictly_after_filing_date"
    assert metadata["quality_momentum_components"] == [
        "GROSS_PROFITABILITY",
        "OPERATING_RETURN_ON_CAPITAL",
        "CASH_PROFITABILITY",
        "ACCRUAL_QUALITY",
        "LOW_LEVERAGE",
        "MOM_12_1",
    ]
    assert len(metadata["canonical_catalog_sha256"]) == 64


def test_catalog_metadata_is_deterministic_and_returns_fresh_values() -> None:
    first = build_massive_fundamental_catalog_metadata_v1()
    second = build_massive_fundamental_catalog_metadata_v1()
    first["definitions"][0]["description"] = "mutated"

    assert first["canonical_catalog_sha256"] == second["canonical_catalog_sha256"]
    assert second["definitions"][0]["description"] != "mutated"
