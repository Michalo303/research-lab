import pytest
import pandas as pd

from research_lab.data_eodhd import EODHDConfigError, _sanitize_url, coverage_row, fetch_eodhd_eod, fetch_eodhd_eod_diagnostic, get_eodhd_api_key


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(EODHDConfigError):
        get_eodhd_api_key()


def test_fetch_eodhd_parses_payload(monkeypatch):
    sample = [
        {"date": "1995-01-03", "open": 1, "high": 2, "low": 0.5, "close": 1.2, "volume": 1000},
        {"date": "1995-01-04", "open": 1.2, "high": 2.1, "low": 1.1, "close": 1.9, "volume": 1200},
    ]
    monkeypatch.setattr("research_lab.data_eodhd._download_json", lambda *_: (sample, {"http_status": 200, "content_type": "application/json", "body_length": 10, "body_preview": "ok"}))
    df = fetch_eodhd_eod("SPY.US", api_key="x")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2


def test_coverage_years_computed():
    idx = pd.to_datetime(["1990-01-02", "2021-01-04"])
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2], "volume": [1, 2]}, index=idx)
    row = coverage_row("SPY.US", df, min_years_ok=30)
    assert row.coverage_years >= 30
    assert row.status == "OK"


def test_short_history_is_warning():
    idx = pd.to_datetime(["2020-01-02", "2024-01-02"])
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2], "volume": [1, 2]}, index=idx)
    row = coverage_row("NEW.US", df, min_years_ok=30)
    assert row.status == "WARNING"


def test_sanitize_url_masks_token():
    masked = _sanitize_url("https://eodhd.com/api/eod/SPY.US?api_token=secret&fmt=json")
    assert "secret" not in masked
    assert "api_token=***" in masked


def test_diagnostic_reports_rows(monkeypatch):
    sample = [{"date": "1995-01-03", "open": 1, "high": 2, "low": 0.5, "close": 1.2, "volume": 1000}]
    monkeypatch.setattr("research_lab.data_eodhd._download_json", lambda *_: (sample, {"http_status": 200, "content_type": "application/json", "body_length": 10, "body_preview": "ok"}))
    d = fetch_eodhd_eod_diagnostic("SPY.US", api_key="x")
    assert d.parsed_row_count == 1
    assert d.http_status == 200
