from research_lab.fundamentals import is_timestamp_safe
from research_lab.fundamentals_massive import classify_massive_payload, parse_massive_fundamentals


def test_massive_filing_date_normalizes_as_availability_anchor():
    payload = {
        "results": [
            {
                "company_name": "Apple Inc.",
                "tickers": ["AAPL"],
                "filing_date": "2025-11-01",
                "period_of_report_date": "2025-09-30",
                "fiscal_period": "FY",
                "fiscal_year": 2025,
                "financials": {
                    "income_statement": {
                        "revenues": {"value": 1000000, "unit": "USD", "label": "Revenues"},
                    }
                },
                "source_filing_url": "https://example.test/filing",
                "id": "filing-1",
            }
        ]
    }

    rows, diagnostics = parse_massive_fundamentals("AAPL", payload, ingestion_timestamp="2026-01-01T00:00:00Z")

    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].provider == "massive"
    assert rows[0].statement_type == "income_statement"
    assert rows[0].filing_date == "2025-11-01"
    assert rows[0].period_end_date == "2025-09-30"
    assert rows[0].value == 1000000.0
    assert rows[0].currency == "USD"
    assert rows[0].provider_record_id == "filing-1"
    assert is_timestamp_safe(rows[0]) is True
    assert diagnostics[0]["timestamp_safety"] == "timestamp_safe"


def test_massive_ratios_are_kept_separate_when_timestamp_source_is_untraceable():
    payload = {
        "results": [
            {
                "ticker": "AAPL",
                "filing_date": "2025-11-01",
                "period_end_date": "2025-09-30",
                "fiscal_year": 2025,
                "fiscal_period": "FY",
                "financials": {},
                "ratios": {"pe_ratio": 30.0},
            }
        ]
    }

    rows, diagnostics = parse_massive_fundamentals("AAPL", payload, ingestion_timestamp="2026-01-01T00:00:00Z")

    assert rows == []
    assert diagnostics[0]["ratios_status"] == "ignored_untraceable_timestamp_source"


def test_massive_limit_or_error_diagnostic_masks_secret():
    payload = {"status": "ERROR", "error": "API key demo-secret exceeded plan limit"}

    classified = classify_massive_payload(payload, request_url="https://api.massive.com/vX/reference/financials?apiKey=demo-secret")

    assert classified["status"] == "provider_error"
    assert "demo-secret" not in classified["request_url"]
    assert "demo-secret" not in classified["message"]
