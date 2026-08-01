from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research_lab.research.massive_fundamental_dataset_v1 import (
    build_point_in_time_fundamental_factor_panel_v1,
    load_massive_fundamental_histories_v1,
)


def _record(
    period_end: str,
    filing_date: str,
    index: int,
    *,
    gross_override: float | None = None,
) -> dict[str, object]:
    revenue = 80.0 + index * 10.0
    gross = revenue / 2.0 if gross_override is None else gross_override
    operating = revenue / 10.0
    net_income = 4.0 + index
    cash_flow = 6.0 + index
    record = {
        "cik": "0000000001",
        "requested_instrument_id": "I-AAA",
        "requested_ticker": "AAA",
        "reported_tickers": ["AAA"],
        "filing_date": filing_date,
        "period_end_date": period_end,
        "timeframe": "quarterly",
        "fiscal_year": int(period_end[:4]),
        "fiscal_period": f"Q{index % 4 + 1}",
        "source_filing_identity": "https://www.sec.gov/Archives/example",
        "statements": {
            "income_statement": {
                "revenues": {"value": revenue, "unit": "USD"},
                "gross_profit": {"value": gross, "unit": "USD"},
                "operating_income_loss": {"value": operating, "unit": "USD"},
                "net_income_loss": {"value": net_income, "unit": "USD"},
                "diluted_average_shares": {"value": 100.0, "unit": "shares"},
            },
            "balance_sheet": {
                "assets": {"value": 100.0 + index * 100.0, "unit": "USD"},
                "current_liabilities": {"value": 25.0 * (index + 1), "unit": "USD"},
                "liabilities": {"value": 50.0 * (index + 1), "unit": "USD"},
            },
            "cash_flow_statement": {
                "net_cash_flow_from_operating_activities": {"value": cash_flow, "unit": "USD"},
            },
        },
    }
    record["canonical_record_sha256"] = _canonical_sha(record)
    return record


def _history(records: list[dict[str, object]]) -> dict[str, object]:
    history = {
        "version": "massive_normalized_fundamental_history_v1",
        "instrument_id": "I-AAA",
        "lookup_ticker": "AAA",
        "cik": "0000000001",
        "listing_start": "2018-01-01",
        "listing_end": None,
        "records": records,
    }
    history["canonical_history_sha256"] = _canonical_sha(history)
    return history


def _price_frame(instruments: tuple[str, ...] = ("AAA",), end: str = "2021-03-31") -> pd.DataFrame:
    dates = pd.bdate_range("2018-01-02", end)
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    trend = np.repeat(np.arange(len(dates), dtype="float64"), len(instruments))
    close = 10.0 + trend * 0.01 + np.tile(np.arange(len(instruments)), len(dates))
    frame = pd.DataFrame(index=index)
    frame["open"] = close * 0.999
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    frame["close"] = close
    frame["volume"] = 1_000_000.0
    frame["raw_close"] = close
    frame["dollar_volume"] = close * frame["volume"]
    frame["eligible"] = True
    return frame


def _eight_quarters() -> list[dict[str, object]]:
    periods = [
        "2019-03-31",
        "2019-06-30",
        "2019-09-30",
        "2019-12-31",
        "2020-03-31",
        "2020-06-30",
        "2020-09-30",
        "2020-12-31",
    ]
    filings = [
        "2019-05-10",
        "2019-08-09",
        "2019-11-08",
        "2020-02-07",
        "2020-05-08",
        "2020-08-07",
        "2020-11-06",
        "2021-02-12",
    ]
    return [_record(period, filing, index) for index, (period, filing) in enumerate(zip(periods, filings))]


def test_factor_panel_uses_frozen_quarterly_equations() -> None:
    price = _price_frame()
    history = _history(_eight_quarters())

    panel, metadata = build_point_in_time_fundamental_factor_panel_v1(
        {"AAA": history}, price, development_start="2021-02-15", development_end="2021-03-31"
    )

    row = panel.loc[(panel.index.get_level_values("datetime").max(), "AAA")]
    assert row["GROSS_PROFITABILITY"] == pytest.approx(270.0 / 800.0)
    assert row["OPERATING_RETURN_ON_CAPITAL"] == pytest.approx(54.0 / 600.0)
    assert row["CASH_PROFITABILITY"] == pytest.approx(46.0 / 800.0)
    assert row["ACCRUAL_QUALITY"] == pytest.approx((46.0 - 38.0) / 800.0)
    assert row["REVENUE_GROWTH"] == pytest.approx((540.0 - 380.0) / 380.0)
    assert row["EARNINGS_IMPROVEMENT"] == pytest.approx((38.0 - 22.0) / 800.0)
    assert row["MARGIN_STABILITY"] == pytest.approx(0.0)
    assert row["LOW_LEVERAGE"] == pytest.approx(-400.0 / 800.0)
    assert row["LOW_ASSET_GROWTH"] == pytest.approx(-(800.0 - 400.0) / 400.0)
    assert np.isfinite(row["QUALITY_MOMENTUM"])
    assert metadata["sealed_oos_rows_read"] == 0
    assert metadata["provider_calls_used"] == 0


def test_filing_is_invisible_on_filing_session_and_restatement_never_rewrites_prior_week() -> None:
    price = _price_frame()
    records = _eight_quarters()
    restatement = _record("2020-12-31", "2021-03-01", 7, gross_override=1_000.0)
    history = _history([*records, restatement])

    panel, _ = build_point_in_time_fundamental_factor_panel_v1(
        {"AAA": history}, price, development_start="2021-02-01", development_end="2021-03-31"
    )

    friday = pd.Timestamp("2021-02-12")
    next_friday = pd.Timestamp("2021-02-19")
    before_restatement = pd.Timestamp("2021-02-26")
    after_restatement = pd.Timestamp("2021-03-05")
    assert panel.loc[(friday, "AAA"), "GROSS_PROFITABILITY"] == pytest.approx(250.0 / 700.0)
    assert panel.loc[(next_friday, "AAA"), "GROSS_PROFITABILITY"] == pytest.approx(270.0 / 800.0)
    assert panel.loc[(before_restatement, "AAA"), "GROSS_PROFITABILITY"] == pytest.approx(270.0 / 800.0)
    assert panel.loc[(after_restatement, "AAA"), "GROSS_PROFITABILITY"] == pytest.approx((1_000.0 + 60.0 + 65.0 + 70.0) / 800.0)


def test_missing_quarters_and_missing_components_remain_missing() -> None:
    price = _price_frame()
    records = _eight_quarters()[:7]
    del records[-1]["statements"]["cash_flow_statement"]
    records[-1]["canonical_record_sha256"] = _canonical_sha(
        {key: value for key, value in records[-1].items() if key != "canonical_record_sha256"}
    )
    history = _history(records)

    panel, _ = build_point_in_time_fundamental_factor_panel_v1(
        {"AAA": history}, price, development_start="2020-11-09", development_end="2020-12-31"
    )

    assert panel["REVENUE_GROWTH"].isna().all()
    assert panel["MARGIN_STABILITY"].isna().all()
    assert panel["CASH_PROFITABILITY"].isna().all()


def test_year_end_quarter_is_reconstructed_from_annual_minus_q1_to_q3_point_in_time() -> None:
    price = _price_frame()
    quarters = _eight_quarters()
    records: list[dict[str, object]] = []
    for year_start in (0, 4):
        q1_q3 = quarters[year_start : year_start + 3]
        q4 = quarters[year_start + 3]
        annual = copy.deepcopy(q4)
        annual["timeframe"] = "annual"
        annual["fiscal_period"] = "FY"
        for statement_name in ("income_statement", "cash_flow_statement"):
            annual_fields = annual["statements"][statement_name]
            for field_name, field in annual_fields.items():
                if field_name in {"diluted_average_shares", "basic_average_shares"}:
                    field["value"] = 100.0
                else:
                    field["value"] = field["value"] + sum(
                        item["statements"][statement_name][field_name]["value"]
                        for item in q1_q3
                    )
        annual["canonical_record_sha256"] = _canonical_sha(
            {key: value for key, value in annual.items() if key != "canonical_record_sha256"}
        )
        records.extend([*q1_q3, annual])
    history = _history(records)

    panel, _ = build_point_in_time_fundamental_factor_panel_v1(
        {"AAA": history}, price, development_start="2021-02-15", development_end="2021-03-31"
    )

    row = panel.loc[(panel.index.get_level_values("datetime").max(), "AAA")]
    assert row["GROSS_PROFITABILITY"] == pytest.approx(270.0 / 800.0)
    assert row["REVENUE_GROWTH"] == pytest.approx((540.0 - 380.0) / 380.0)
    assert row["LOW_ASSET_GROWTH"] == pytest.approx(-(800.0 - 400.0) / 400.0)


def test_loader_verifies_bundle_manifest_and_normalized_hashes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "bundle"
    normalized = root / "normalized" / "history.json.gz"
    normalized.parent.mkdir(parents=True)
    history = _history(_eight_quarters())
    compressed = __import__("gzip").compress((json.dumps(history, sort_keys=True, separators=(",", ":")) + "\n").encode(), mtime=0)
    normalized.write_bytes(compressed)
    manifest = {
        "version": "massive_fundamental_dataset_manifest_v1",
        "acquisition_id": "MASSIVE-FUNDAMENTALS-2009-2022-V1",
        "request_sha256": "a" * 64,
        "source_manifest_sha256": "b" * 64,
        "subject_identity_sha256": "c" * 64,
        "filing_interval": {"start": "2009-01-01", "end": "2022-12-31"},
        "records": [
            {
                "instrument_id": "I-AAA",
                "ordinal": 1,
                "lookup_ticker": "AAA",
                "status": "USABLE",
                "attempts": 1,
                "page_count": 1,
                "raw_paths": ["raw/a.json.gz"],
                "raw_sha256": ["d" * 64],
                "normalized_path": "normalized/history.json.gz",
                "normalized_sha256": hashlib.sha256(compressed).hexdigest(),
                "usable_record_count": 8,
                "rejected_record_count": 0,
                "cik": "0000000001",
                "failure_class": None,
            }
        ],
    }
    manifest["canonical_manifest_sha256"] = _canonical_sha(manifest)
    manifest_path = root / "fundamental_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    expected = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "research_lab.research.massive_fundamental_dataset_v1.verify_massive_fundamental_bundle_v1",
        lambda _: {"status": "PASS"},
    )

    histories, metadata = load_massive_fundamental_histories_v1(root, expected)

    assert list(histories) == ["AAA"]
    assert metadata["history_count"] == 1
    assert metadata["provider_calls_used"] == 0
    normalized.write_bytes(compressed + b"tamper")
    with pytest.raises(ValueError, match="normalized hash"):
        load_massive_fundamental_histories_v1(root, expected)


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
