from __future__ import annotations

import copy
from pathlib import Path

import pytest

from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import (
    _last_spy_session_per_month,
    _normalize_identity_universe,
    build_eodhd_us_equity_acquisition_plan_v1,
)


def _request(tmp_path: Path) -> dict[str, object]:
    return {
        "version": "eodhd_us_equity_universe_acquisition_request_v1",
        "acquisition_id": "EODHD-US-EQUITY-2006-2022-V1",
        "output_dir": str((tmp_path / "final").resolve()),
        "provider": "EODHD",
        "approved_host": "eodhd.com",
        "start_date": "2006-01-01",
        "end_date": "2022-12-31",
        "maximum_call_units": 90_000,
        "maximum_attempts_per_request": 2,
        "history_concurrency": 8,
        "timeout_seconds": 90,
        "maximum_symbol_response_bytes": 2_000_000,
        "maximum_bulk_response_bytes": 20_000_000,
        "provenance": {"source": "operator_approved_eodhd_acquisition_v1"},
    }


def test_plan_freezes_contract_and_contains_no_credential_surface(tmp_path: Path) -> None:
    result = build_eodhd_us_equity_acquisition_plan_v1(_request(tmp_path))

    assert result["version"] == "eodhd_us_equity_universe_acquisition_plan_v1"
    assert result["acquisition_id"] == "EODHD-US-EQUITY-2006-2022-V1"
    assert result["interval"] == {"start": "2006-01-01", "end": "2022-12-31"}
    assert result["maximum_call_units"] == 90_000
    assert result["maximum_attempts_per_request"] == 2
    assert result["history_concurrency"] == 8
    assert result["supported_exchange_mics"] == {
        "AMEX": "XASE",
        "NASDAQ": "XNAS",
        "NYSE": "XNYS",
        "NYSE MKT": "XASE",
    }
    assert [item["kind"] for item in result["initial_requests"]] == [
        "ACTIVE_COMMON_STOCKS",
        "DELISTED_COMMON_STOCKS",
        "SPY_SESSION_PROXY",
    ]
    serialized = repr(result)
    assert "api_token" not in serialized
    assert "EODHD_API_KEY" not in serialized
    assert result["sealed_oos_opened"] is False
    assert len(result["request_sha256"]) == 64
    assert len(result["canonical_plan_sha256"]) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "OTHER"),
        ("approved_host", "example.com"),
        ("start_date", "2007-01-01"),
        ("end_date", "2023-01-01"),
        ("maximum_call_units", 89_999),
        ("maximum_attempts_per_request", 3),
        ("history_concurrency", 9),
    ],
)
def test_plan_rejects_frozen_contract_drift(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    request = _request(tmp_path)
    request[field] = value

    with pytest.raises(ValueError):
        build_eodhd_us_equity_acquisition_plan_v1(request)


def test_plan_rejects_unknown_fields_relative_and_repository_output(tmp_path: Path) -> None:
    unknown = _request(tmp_path)
    unknown["api_token"] = "must-not-be-accepted"
    with pytest.raises(ValueError, match="fields"):
        build_eodhd_us_equity_acquisition_plan_v1(unknown)

    relative = _request(tmp_path)
    relative["output_dir"] = "relative/output"
    with pytest.raises(ValueError, match="absolute"):
        build_eodhd_us_equity_acquisition_plan_v1(relative)

    repository = _request(tmp_path)
    repository["output_dir"] = str((Path(__file__).resolve().parents[1] / "private-output").resolve())
    with pytest.raises(ValueError, match="outside"):
        build_eodhd_us_equity_acquisition_plan_v1(repository)


def test_identity_normalization_filters_and_rejects_ambiguous_codes() -> None:
    active = [
        {"Code": "AAA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock", "Isin": "US1"},
        {"Code": "AAA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock", "Isin": "US1"},
        {"Code": "BBB", "Exchange": "NYSE", "Currency": "USD", "Type": "Common Stock", "Isin": "US2"},
        {"Code": "FUND", "Exchange": "NYSE", "Currency": "USD", "Type": "ETF", "Isin": "US3"},
        {"Code": "OTC", "Exchange": "PINK", "Currency": "USD", "Type": "Common Stock", "Isin": "US4"},
    ]
    delisted = [
        {"Code": "BBB", "Exchange": "NYSE", "Currency": "USD", "Type": "Common Stock", "Isin": "DIFFERENT"},
        {"Code": "CCC", "Exchange": "NYSE MKT", "Currency": "USD", "Type": "Common Stock", "Isin": None},
        {"Code": "EUR", "Exchange": "NASDAQ", "Currency": "EUR", "Type": "Common Stock", "Isin": "EU1"},
    ]

    result = _normalize_identity_universe(active, delisted)

    assert [item["code"] for item in result["identities"]] == ["AAA", "CCC"]
    assert result["identities"][0]["status"] == "ACTIVE"
    assert result["identities"][1]["exchange_mic"] == "XASE"
    assert result["exact_duplicate_count"] == 1
    assert result["ambiguous_codes"] == ["BBB"]
    assert result["filtered_row_count"] == 3


def test_spy_sessions_choose_last_observed_date_per_month_without_future_rows() -> None:
    rows = [
        {"date": "2022-01-03"},
        {"date": "2022-01-31"},
        {"date": "2022-02-01"},
        {"date": "2022-02-28"},
        {"date": "2022-12-30"},
    ]

    assert _last_spy_session_per_month(rows, start="2022-01-01", end="2022-12-31") == [
        "2022-01-31",
        "2022-02-28",
        "2022-12-30",
    ]

    future = copy.deepcopy(rows)
    future.append({"date": "2023-01-03"})
    with pytest.raises(ValueError, match="interval"):
        _last_spy_session_per_month(future, start="2022-01-01", end="2022-12-31")
