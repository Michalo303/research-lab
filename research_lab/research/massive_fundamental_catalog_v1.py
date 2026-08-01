from __future__ import annotations

import copy
import hashlib
import json


CATALOG_VERSION = "massive_fundamental_catalog_v1"
QUARTERLY_FLOW_PERIODS = 4
MARGIN_STABILITY_PERIODS = 8
QUALITY_MOMENTUM_COMPONENTS = (
    "GROSS_PROFITABILITY",
    "OPERATING_RETURN_ON_CAPITAL",
    "CASH_PROFITABILITY",
    "ACCRUAL_QUALITY",
    "LOW_LEVERAGE",
    "MOM_12_1",
)

FACTOR_DEFINITIONS_V1: dict[str, dict[str, object]] = {
    "GROSS_PROFITABILITY": {
        "factor_id": "GROSS_PROFITABILITY",
        "family_id": "FUNDAMENTAL_PROFITABILITY",
        "description": "Trailing-four-quarter gross profit divided by latest total assets.",
        "higher_is_better": True,
    },
    "OPERATING_RETURN_ON_CAPITAL": {
        "factor_id": "OPERATING_RETURN_ON_CAPITAL",
        "family_id": "FUNDAMENTAL_PROFITABILITY",
        "description": "Trailing-four-quarter operating income divided by latest assets less current liabilities.",
        "higher_is_better": True,
    },
    "CASH_PROFITABILITY": {
        "factor_id": "CASH_PROFITABILITY",
        "family_id": "FUNDAMENTAL_CASH_QUALITY",
        "description": "Trailing-four-quarter operating cash flow divided by latest total assets.",
        "higher_is_better": True,
    },
    "ACCRUAL_QUALITY": {
        "factor_id": "ACCRUAL_QUALITY",
        "family_id": "FUNDAMENTAL_CASH_QUALITY",
        "description": "Trailing operating cash flow less trailing net income, divided by latest total assets.",
        "higher_is_better": True,
    },
    "REVENUE_GROWTH": {
        "factor_id": "REVENUE_GROWTH",
        "family_id": "FUNDAMENTAL_GROWTH",
        "description": "Trailing-four-quarter revenue change versus the preceding four quarters.",
        "higher_is_better": True,
    },
    "EARNINGS_IMPROVEMENT": {
        "factor_id": "EARNINGS_IMPROVEMENT",
        "family_id": "FUNDAMENTAL_GROWTH",
        "description": "Trailing net-income change versus the preceding four quarters, scaled by latest assets.",
        "higher_is_better": True,
    },
    "MARGIN_STABILITY": {
        "factor_id": "MARGIN_STABILITY",
        "family_id": "FUNDAMENTAL_STABILITY",
        "description": "Negative standard deviation of eight quarterly operating margins.",
        "higher_is_better": True,
    },
    "LOW_LEVERAGE": {
        "factor_id": "LOW_LEVERAGE",
        "family_id": "FUNDAMENTAL_BALANCE_SHEET",
        "description": "Negative latest total liabilities divided by latest total assets.",
        "higher_is_better": True,
    },
    "LOW_ASSET_GROWTH": {
        "factor_id": "LOW_ASSET_GROWTH",
        "family_id": "FUNDAMENTAL_INVESTMENT",
        "description": "Negative year-over-year growth in latest total assets.",
        "higher_is_better": True,
    },
    "QUALITY_MOMENTUM": {
        "factor_id": "QUALITY_MOMENTUM",
        "family_id": "FUNDAMENTAL_COMPOSITE",
        "description": "Equal-weight cross-sectional percentile mean of five frozen quality components and 12-1 momentum.",
        "higher_is_better": True,
    },
}


def build_massive_fundamental_catalog_metadata_v1() -> dict[str, object]:
    """Return immutable metadata for the ten predeclared fundamental trials."""

    result: dict[str, object] = {
        "version": CATALOG_VERSION,
        "factor_count": len(FACTOR_DEFINITIONS_V1),
        "ordered_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "definitions": copy.deepcopy(list(FACTOR_DEFINITIONS_V1.values())),
        "quarterly_flow_periods": QUARTERLY_FLOW_PERIODS,
        "margin_stability_periods": MARGIN_STABILITY_PERIODS,
        "availability_lag": "first_verified_session_strictly_after_filing_date",
        "quality_momentum_components": list(QUALITY_MOMENTUM_COMPONENTS),
        "missing_value_policy": "no_imputation",
        "vendor_ttm_policy": "excluded",
        "vendor_ratio_policy": "excluded",
    }
    result["canonical_catalog_sha256"] = _canonical_sha256(result)
    return result


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
