from __future__ import annotations

from pathlib import Path

import pandas as pd

from research_lab.data_quality_eodhd import (
    EODHD_VALIDATION_UNIVERSE,
    audit_history_rows,
    calculate_history_length_years,
    write_quality_outputs,
)


def test_eodhd_quality_audit_flags_duplicate_dates_and_bad_prices():
    rows = [
        {"date": "2020-01-02", "open": 100, "high": 105, "low": 99, "close": 100, "volume": 1000, "adjusted_close": 100},
        {"date": "2020-01-02", "open": 101, "high": 106, "low": 100, "close": 101, "volume": 1200, "adjusted_close": 101},
        {"date": "2020-01-06", "open": 0, "high": 107, "low": -1, "close": 200, "volume": None, "adjusted_close": 200},
    ]

    audit = audit_history_rows("SPY.US", rows, adjusted=True)

    assert audit["symbol"] == "SPY.US"
    assert audit["duplicate_dates"] == 1
    assert audit["missing_weekdays"] == 1
    assert audit["zero_price_rows"] == 1
    assert audit["negative_price_rows"] == 1
    assert audit["nan_ohlcv_rows"] == 1
    assert audit["adjusted_status"] == "adjusted"
    assert audit["coverage_status"] == "partial"


def test_eodhd_history_length_calculation():
    years = calculate_history_length_years(pd.Timestamp("2010-01-04"), pd.Timestamp("2020-01-03"))

    assert 9.9 < years < 10.1


def test_eodhd_quality_outputs_are_written(tmp_path: Path):
    audits = [
        audit_history_rows(
            "SPY.US",
            [
                {"date": "2019-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000, "adjusted_close": 100},
                {"date": "2024-01-02", "open": 150, "high": 151, "low": 149, "close": 150, "volume": 2000, "adjusted_close": 150},
            ],
            adjusted=True,
        )
    ]

    output = write_quality_outputs(tmp_path, audits)

    assert Path(output["manifest_path"]).exists()
    assert Path(output["quality_path"]).exists()
    quality = Path(output["quality_path"]).read_text(encoding="utf-8")
    manifest = Path(output["manifest_path"]).read_text(encoding="utf-8")
    assert "SPY.US" in quality
    assert "coverage_years" in quality
    assert "SPY.US" in manifest
    assert "research_only" in manifest


def test_eodhd_validation_universe_contains_requested_etfs():
    assert EODHD_VALIDATION_UNIVERSE == [
        "SPY.US",
        "QQQ.US",
        "IWM.US",
        "TLT.US",
        "GLD.US",
        "XLK.US",
        "XLF.US",
        "XLE.US",
        "SMH.US",
        "SOXX.US",
    ]
