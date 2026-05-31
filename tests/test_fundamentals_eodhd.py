from research_lab.fundamentals import is_timestamp_safe
from research_lab.fundamentals_eodhd import classify_eodhd_payload, parse_eodhd_fundamentals


def test_eodhd_quarterly_and_annual_sections_without_filing_date_are_uncertain():
    payload = {
        "Financials": {
            "Income_Statement": {
                "yearly": {
                    "2025-12-31": {
                        "date": "2025-12-31",
                        "filing_date": None,
                        "currency_symbol": "USD",
                        "totalRevenue": "100",
                    }
                },
                "quarterly": {
                    "2025-09-30": {
                        "date": "2025-09-30",
                        "totalRevenue": "25",
                    }
                },
            }
        }
    }

    rows, diagnostics = parse_eodhd_fundamentals("AAPL.US", payload, ingestion_timestamp="2026-01-01T00:00:00Z")

    assert {row.fiscal_period for row in rows} == {"FY", "Q"}
    assert all(is_timestamp_safe(row) is False for row in rows)
    assert classify_eodhd_payload(payload)["timestamp_safety"] == "uncertain"
    assert {item["period_kind"] for item in diagnostics} == {"yearly", "quarterly"}


def test_eodhd_explicit_filing_date_is_timestamp_safe():
    payload = {
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-09-30": {
                        "date": "2025-09-30",
                        "filing_date": "2025-10-29",
                        "currency_symbol": "USD",
                        "cash": "50",
                    }
                }
            }
        }
    }

    rows, diagnostics = parse_eodhd_fundamentals("MSFT.US", payload, ingestion_timestamp="2026-01-01T00:00:00Z")

    assert len(rows) == 1
    assert rows[0].symbol == "MSFT"
    assert rows[0].statement_type == "balance_sheet"
    assert rows[0].filing_date == "2025-10-29"
    assert rows[0].value == 50.0
    assert is_timestamp_safe(rows[0]) is True
    assert diagnostics[0]["timestamp_safety"] == "timestamp_safe"


def test_eodhd_provider_error_diagnostic_masks_secret():
    payload = {"error": True, "message": "Invalid API token demo-secret"}

    classified = classify_eodhd_payload(payload, request_url="https://eodhd.com/api/fundamentals/AAPL.US?api_token=demo-secret&fmt=json")

    assert classified["status"] == "provider_error"
    assert "demo-secret" not in classified["request_url"]
    assert "demo-secret" not in classified["message"]
