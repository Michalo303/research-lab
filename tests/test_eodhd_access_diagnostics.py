import json

from research_lab.eodhd_access_diagnostics import run_eodhd_access_diagnostics


SECRET = "eodhd-secret-value"


def _meta(status):
    return {
        "http_status": status,
        "content_type": "application/json",
        "body_length": 2,
        "body_preview": "{}",
    }


def _daily_payload():
    return [{"date": "2026-05-29", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}]


def _fundamentals_payload(filing_date=None):
    record = {
        "date": "2025-12-31",
        "currency_symbol": "USD",
        "totalRevenue": "100",
    }
    if filing_date:
        record["filing_date"] = filing_date
    return {"Financials": {"Income_Statement": {"yearly": {"2025-12-31": record}}}}


def test_missing_api_key_reports_missing_and_skips_http(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    calls = []

    result = run_eodhd_access_diagnostics(http_get=lambda url: calls.append(url))

    assert result["api_key"]["present"] is False
    assert result["daily_ohlcv"]["status"] == "missing_api_key"
    assert result["daily_ohlcv"]["authorized"] is False
    assert result["fundamentals"]["status"] == "missing_api_key"
    assert result["fundamentals"]["authorized"] is False
    assert result["fundamentals"]["fatal"] is False
    assert calls == []


def test_invalid_key_401_reports_unauthorized(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        return {"error": True, "message": f"Invalid API token {SECRET}"}, _meta(401)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert result["api_key"]["present"] is True
    assert result["daily_ohlcv"]["status"] == "unauthorized"
    assert result["daily_ohlcv"]["authorized"] is False
    assert result["daily_ohlcv"]["http_status"] == 401
    assert result["fundamentals"]["status"] == "unauthorized"
    assert result["fundamentals"]["authorized"] is False
    assert result["fundamentals"]["http_status"] == 401


def test_fundamentals_forbidden_403_is_non_fatal(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/fundamentals/" in url:
            return {"error": True, "message": f"Forbidden token {SECRET}"}, _meta(403)
        return _daily_payload(), _meta(200)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert result["fundamentals"]["status"] == "forbidden"
    assert result["fundamentals"]["authorized"] is False
    assert result["fundamentals"]["http_status"] == 403
    assert result["fundamentals"]["fatal"] is False


def test_daily_ohlcv_ok_but_fundamentals_forbidden(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/eod/" in url:
            return _daily_payload(), _meta(200)
        return {"error": True, "message": "Forbidden"}, _meta(403)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert result["daily_ohlcv"]["status"] == "ok"
    assert result["daily_ohlcv"]["authorized"] is True
    assert result["daily_ohlcv"]["parsed_row_count"] == 1
    assert result["fundamentals"]["status"] == "forbidden"
    assert result["fundamentals"]["authorized"] is False
    assert result["fundamentals"]["fatal"] is False


def test_fundamentals_ok_with_timestamp_fields(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/eod/" in url:
            return _daily_payload(), _meta(200)
        return _fundamentals_payload(filing_date="2026-02-15"), _meta(200)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert result["fundamentals"]["status"] == "ok"
    assert result["fundamentals"]["authorized"] is True
    assert result["fundamentals"]["timestamp_safety"] == "timestamp_safe"
    assert result["fundamentals"]["timestamp_safe_rows"] == 1
    assert result["fundamentals"]["uncertain_rows"] == 0


def test_fundamentals_ok_without_timestamp_safe_fields(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/eod/" in url:
            return _daily_payload(), _meta(200)
        return _fundamentals_payload(), _meta(200)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert result["fundamentals"]["status"] == "ok"
    assert result["fundamentals"]["authorized"] is True
    assert result["fundamentals"]["timestamp_safety"] == "uncertain"
    assert result["fundamentals"]["timestamp_safe_rows"] == 0
    assert result["fundamentals"]["uncertain_rows"] == 1


def test_diagnostics_never_include_api_key_value(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/eod/" in url:
            return _daily_payload(), _meta(200) | {"body_preview": f"token={SECRET}"}
        return {"error": True, "message": f"Forbidden for {SECRET}"}, _meta(403)

    result = run_eodhd_access_diagnostics(http_get=http_get)

    assert SECRET not in json.dumps(result)
    assert result["api_key"] == {"name": "EODHD_API_KEY", "present": True}


def test_missing_or_forbidden_fundamentals_are_non_fatal(monkeypatch):
    monkeypatch.setenv("EODHD_API_KEY", SECRET)

    def http_get(url):
        if "/eod/" in url:
            return _daily_payload(), _meta(200)
        return {"error": True, "message": "Forbidden"}, _meta(403)

    forbidden = run_eodhd_access_diagnostics(http_get=http_get)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    missing = run_eodhd_access_diagnostics(http_get=http_get)

    assert forbidden["fundamentals"]["fatal"] is False
    assert missing["fundamentals"]["fatal"] is False
