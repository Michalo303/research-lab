import json

from research_lab.fmp_access_diagnostics import (
    FMP_ENDPOINTS,
    classify_fmp_endpoint_payload,
    run_fmp_access_diagnostics,
)
from scripts import check_fmp_access


SECRET = "fmp-secret-value"


def _meta(status, body_preview="{}"):
    return {
        "http_status": status,
        "content_type": "application/json",
        "body_length": len(body_preview),
        "body_preview": body_preview,
    }


def _first_endpoint(result, name):
    return next(endpoint for endpoint in result["endpoints"] if endpoint["endpoint_name"] == name)


def test_missing_api_key_is_explicit_and_skips_http(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    calls = []

    result = run_fmp_access_diagnostics(env={}, http_get=lambda url: calls.append(url))

    assert result["api_key"] == {"name": "FMP_API_KEY", "present": False}
    assert len(result["endpoints"]) == len(FMP_ENDPOINTS)
    assert {endpoint["status"] for endpoint in result["endpoints"]} == {"missing_api_key"}
    assert {endpoint["credential_present"] for endpoint in result["endpoints"]} == {False}
    assert {endpoint["authorized"] for endpoint in result["endpoints"]} == {False}
    assert {endpoint["reachable"] for endpoint in result["endpoints"]} == {False}
    assert calls == []


def test_invalid_key_401_is_unauthorized_and_non_fatal():
    def http_get(url):
        return {"error": True, "message": f"Invalid API key {SECRET}"}, _meta(401)

    result = run_fmp_access_diagnostics(env={"FMP_API_KEY": SECRET}, http_get=http_get)
    endpoint = result["endpoints"][0]

    assert endpoint["http_status"] == 401
    assert endpoint["status"] == "unauthorized"
    assert endpoint["authorized"] is False
    assert endpoint["reachable"] is True
    assert endpoint["plan_limited"] is False


def test_forbidden_403_is_unauthorized_and_non_fatal():
    def http_get(url):
        return {"error": True, "message": "Forbidden"}, _meta(403)

    result = run_fmp_access_diagnostics(env={"FMP_API_KEY": SECRET}, http_get=http_get)
    endpoint = result["endpoints"][0]

    assert endpoint["http_status"] == 403
    assert endpoint["status"] == "unauthorized"
    assert endpoint["authorized"] is False
    assert endpoint["reachable"] is True
    assert endpoint["plan_limited"] is False


def test_plan_limited_402_is_non_fatal():
    def http_get(url):
        return {"error": True, "message": "Upgrade your plan"}, _meta(402)

    result = run_fmp_access_diagnostics(env={"FMP_API_KEY": SECRET}, http_get=http_get)
    endpoint = result["endpoints"][0]

    assert endpoint["http_status"] == 402
    assert endpoint["status"] == "plan_limited"
    assert endpoint["authorized"] is True
    assert endpoint["reachable"] is True
    assert endpoint["plan_limited"] is True


def test_core_statement_with_accepted_date_is_timestamp_safe_candidate():
    payload = [
        {
            "date": "2025-09-30",
            "symbol": "AAPL",
            "acceptedDate": "2025-10-31 18:01:20",
            "filingDate": "2025-10-31",
            "fiscalYear": "2025",
            "period": "FY",
            "revenue": 100,
        }
    ]

    diagnostic = classify_fmp_endpoint_payload(
        endpoint_name="income statement annual",
        endpoint_url_sanitized="https://financialmodelingprep.com/stable/income-statement?apikey=REDACTED",
        symbol="AAPL",
        period="annual",
        credential_present=True,
        payload=payload,
        meta=_meta(200),
        api_key=SECRET,
    )

    assert diagnostic["payload_type"] == "array"
    assert diagnostic["record_keys"] == list(payload[0])
    assert diagnostic["date_fields_present"] == ["acceptedDate", "filingDate", "date", "period", "fiscalYear"]
    assert diagnostic["has_accepted_date"] is True
    assert diagnostic["has_filing_date"] is True
    assert diagnostic["timestamp_safe_candidate"] is True


def test_core_statement_with_filing_date_only_is_timestamp_safe_candidate():
    payload = [{"date": "2025-09-30", "symbol": "AAPL", "filingDate": "2025-10-31", "period": "FY"}]

    diagnostic = classify_fmp_endpoint_payload(
        endpoint_name="cash flow quarterly",
        endpoint_url_sanitized="https://financialmodelingprep.com/stable/cash-flow-statement?apikey=REDACTED",
        symbol="AAPL",
        period="quarter",
        credential_present=True,
        payload=payload,
        meta=_meta(200),
        api_key=SECRET,
    )

    assert diagnostic["has_accepted_date"] is False
    assert diagnostic["has_filing_date"] is True
    assert diagnostic["timestamp_safe_candidate"] is True


def test_date_period_fiscal_year_only_is_not_timestamp_safe_candidate():
    payload = [{"date": "2025-09-30", "period": "FY", "fiscalYear": "2025"}]

    diagnostic = classify_fmp_endpoint_payload(
        endpoint_name="income statement annual",
        endpoint_url_sanitized="https://financialmodelingprep.com/stable/income-statement?apikey=REDACTED",
        symbol="AAPL",
        period="annual",
        credential_present=True,
        payload=payload,
        meta=_meta(200),
        api_key=SECRET,
    )

    assert diagnostic["date_fields_present"] == ["date", "period", "fiscalYear"]
    assert diagnostic["timestamp_safe_candidate"] is False
    assert "date alone is not an availability date" in diagnostic["warnings"]


def test_key_metrics_and_ratios_without_filing_dates_are_unsafe_or_uncertain():
    payload = [{"symbol": "AAPL", "date": "2025-09-30", "fiscalYear": "2025", "period": "FY", "peRatio": 30}]

    for endpoint_name in ("key metrics annual", "ratios annual"):
        diagnostic = classify_fmp_endpoint_payload(
            endpoint_name=endpoint_name,
            endpoint_url_sanitized="https://financialmodelingprep.com/stable/key-metrics?apikey=REDACTED",
            symbol="AAPL",
            period="annual",
            credential_present=True,
            payload=payload,
            meta=_meta(200),
            api_key=SECRET,
        )

        assert diagnostic["timestamp_safe_candidate"] is False
        assert "no acceptedDate/filingDate/fillingDate availability field present" in diagnostic["warnings"]


def test_enterprise_values_and_shares_float_with_date_only_are_unsafe_or_uncertain():
    cases = [
        ("enterprise values", [{"symbol": "AAPL", "date": "2025-09-30", "enterpriseValue": 1}]),
        ("shares float", [{"symbol": "AAPL", "date": "2025-09-30", "floatShares": 1}]),
    ]

    for endpoint_name, payload in cases:
        diagnostic = classify_fmp_endpoint_payload(
            endpoint_name=endpoint_name,
            endpoint_url_sanitized="https://financialmodelingprep.com/stable/enterprise-values?apikey=REDACTED",
            symbol="AAPL",
            period=None,
            credential_present=True,
            payload=payload,
            meta=_meta(200),
            api_key=SECRET,
        )

        assert diagnostic["date_fields_present"] == ["date"]
        assert diagnostic["timestamp_safe_candidate"] is False


def test_as_reported_without_filing_dates_is_unsafe_or_uncertain():
    payload = [{"symbol": "AAPL", "fiscalYear": "2025", "period": "FY", "reportedCurrency": "USD", "date": "2025-09-30", "data": {}}]

    diagnostic = classify_fmp_endpoint_payload(
        endpoint_name="as-reported income annual",
        endpoint_url_sanitized="https://financialmodelingprep.com/stable/income-statement-as-reported?apikey=REDACTED",
        symbol="AAPL",
        period="annual",
        credential_present=True,
        payload=payload,
        meta=_meta(200),
        api_key=SECRET,
    )

    assert diagnostic["record_keys"] == list(payload[0])
    assert diagnostic["timestamp_safe_candidate"] is False


def test_secret_redaction_in_url_message_body_preview_and_exception():
    def http_get(url):
        raise RuntimeError(f"request failed for {url} with key {SECRET}")

    result = run_fmp_access_diagnostics(
        env={"FMP_API_KEY": SECRET},
        symbols=["AAPL"],
        http_get=http_get,
    )

    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert "apikey=REDACTED" in serialized
    assert result["endpoints"][0]["status"] == "request_error"

    payload = {"error": True, "message": f"key={SECRET}"}
    diagnostic = classify_fmp_endpoint_payload(
        endpoint_name="income statement annual",
        endpoint_url_sanitized=f"https://example.test?apikey={SECRET}",
        symbol="AAPL",
        period="annual",
        credential_present=True,
        payload=payload,
        meta=_meta(402, body_preview=f"apikey={SECRET}"),
        api_key=SECRET,
    )
    assert SECRET not in json.dumps(diagnostic)


def test_run_diagnostics_reports_all_required_endpoint_classes():
    def http_get(url):
        return [{"date": "2025-09-30"}], _meta(200)

    result = run_fmp_access_diagnostics(env={"FMP_API_KEY": SECRET}, http_get=http_get)

    assert [endpoint["endpoint_name"] for endpoint in result["endpoints"]] == [endpoint.name for endpoint in FMP_ENDPOINTS]
    assert _first_endpoint(result, "income statement annual")["period"] == "annual"
    assert _first_endpoint(result, "income statement quarterly")["period"] == "quarter"
    assert _first_endpoint(result, "enterprise values")["period"] is None
    assert _first_endpoint(result, "shares float")["period"] is None


def test_script_main_exits_successfully_for_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("FMP_API_KEY", raising=False)

    exit_code = check_fmp_access.main([])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "missing_api_key" in output


def test_script_main_exits_successfully_for_402_and_403(monkeypatch, capsys):
    monkeypatch.setenv("FMP_API_KEY", SECRET)

    def fake_download(url):
        if "income-statement" in url:
            return {"error": True, "message": f"Forbidden {SECRET}"}, _meta(403)
        return {"error": True, "message": f"Upgrade plan {SECRET}"}, _meta(402)

    monkeypatch.setattr("research_lab.fmp_access_diagnostics._download_json", fake_download)

    exit_code = check_fmp_access.main(["--symbols", "AAPL"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert SECRET not in output
    assert "unauthorized" in output
    assert "plan_limited" in output
