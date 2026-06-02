import json

import pytest

from research_lab.fundamentals_fmp import (
    SUPPORTED_FMP_CORE_ENDPOINTS,
    coverage_diagnostics,
    fundamentals_asof,
    normalize_fmp_core_statement_payload,
)


SECRET = "fmp-secret-value"


def _income_payload(**overrides):
    record = {
        "symbol": "aapl",
        "date": "2025-09-30",
        "fiscalYear": "2025",
        "period": "FY",
        "reportedCurrency": "USD",
        "acceptedDate": "2025-10-31 18:01:20",
        "filingDate": "2025-10-31",
        "revenue": 391035000000,
        "grossProfit": 180683000000,
        "companyName": "Apple Inc.",
        "link": f"https://example.test/filing?apikey={SECRET}",
    }
    record.update(overrides)
    return [record]


def test_income_statement_annual_normalization_creates_long_numeric_records():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert [record.field_name for record in records] == ["revenue", "grossProfit"]
    assert {record.value for record in records} == {391035000000.0, 180683000000.0}
    first = records[0]
    assert first.symbol == "AAPL"
    assert first.provider == "FMP"
    assert first.statement_type == "income_statement"
    assert first.period_type == "annual"
    assert first.fiscal_year == 2025
    assert first.fiscal_period == "FY"
    assert first.period_end_date == "2025-09-30"
    assert first.currency == "USD"
    assert first.source_endpoint == "/stable/income-statement"
    assert first.ingestion_timestamp == "2026-06-02T10:00:00Z"


def test_income_statement_quarterly_normalization_preserves_quarter_period_type():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="quarterly",
        payload=_income_payload(period="Q1", fiscalYear="2026", revenue=100, grossProfit=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert len(records) == 1
    assert records[0].period_type == "quarterly"
    assert records[0].fiscal_period == "Q1"
    assert records[0].fiscal_year == 2026


def test_balance_sheet_normalization_uses_balance_sheet_statement_type():
    payload = [
        {
            "symbol": "MSFT",
            "date": "2025-06-30",
            "fiscalYear": "2025",
            "period": "FY",
            "reportedCurrency": "USD",
            "filingDate": "2025-07-30",
            "cashAndCashEquivalents": 9500000000,
            "totalAssets": "411976000000",
            "companyName": "Microsoft",
        }
    ]

    records = normalize_fmp_core_statement_payload(
        symbol="MSFT",
        statement_type="balance_sheet",
        period_type="annual",
        payload=payload,
        source_endpoint="/stable/balance-sheet-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert [record.field_name for record in records] == ["cashAndCashEquivalents", "totalAssets"]
    assert {record.statement_type for record in records} == {"balance_sheet"}
    assert records[1].value == 411976000000.0


def test_cash_flow_normalization_uses_cash_flow_statement_type():
    payload = [
        {
            "symbol": "NVDA",
            "date": "2026-01-25",
            "calendarYear": "2026",
            "period": "FY",
            "reportedCurrency": "USD",
            "acceptedDate": "2026-02-26T17:00:00Z",
            "netCashProvidedByOperatingActivities": 64089000000,
            "freeCashFlow": 60853000000,
        }
    ]

    records = normalize_fmp_core_statement_payload(
        symbol="NVDA",
        statement_type="cash_flow",
        period_type="annual",
        payload=payload,
        source_endpoint="/stable/cash-flow-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert [record.field_name for record in records] == [
        "netCashProvidedByOperatingActivities",
        "freeCashFlow",
    ]
    assert {record.statement_type for record in records} == {"cash_flow"}


def test_accepted_date_is_preferred_over_filing_date_for_availability():
    record = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate="2025-10-31 18:01:20", filingDate="2025-10-30"),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )[0]

    assert record.available_date == "2025-10-31 18:01:20"
    assert record.timestamp_safe is True
    assert record.timestamp_source == "acceptedDate"
    assert record.timestamp_confidence == "acceptedDate"


def test_filing_date_is_used_when_accepted_date_is_missing():
    record = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate=None, fillingDate="2025-10-29", filingDate=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )[0]

    assert record.available_date == "2025-10-29"
    assert record.timestamp_safe is True
    assert record.timestamp_source == "filingDate"


def test_missing_accepted_and_filing_dates_creates_timestamp_unsafe_records():
    record = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate=None, filingDate=None, fillingDate=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )[0]

    assert record.available_date is None
    assert record.timestamp_safe is False
    assert record.timestamp_source == "missing"
    assert record.timestamp_confidence == "unsafe_missing_accepted_or_filing_date"


def test_date_alone_is_period_end_date_not_available_date():
    record = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate=None, filingDate=None, fillingDate=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )[0]

    assert record.period_end_date == "2025-09-30"
    assert record.available_date is None


def test_fundamentals_asof_excludes_records_available_after_asof_date():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate="2025-11-01 00:00:00"),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert fundamentals_asof(records, "2025-10-31") == []


def test_fundamentals_asof_includes_records_available_on_or_before_asof_date():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate="2025-10-31 00:00:00"),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert fundamentals_asof(records, "2025-10-31") == records


def test_unsafe_records_are_excluded_by_default():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate=None, filingDate=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert fundamentals_asof(records, "2026-01-01") == []


def test_include_unsafe_is_explicit_and_diagnostic_only():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(acceptedDate=None, filingDate=None),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    assert fundamentals_asof(records, "2026-01-01", include_unsafe=True) == records


def test_coverage_diagnostics_count_timestamp_safe_and_unsafe_records():
    safe = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(symbol="AAPL", acceptedDate="2025-10-31 18:01:20"),
        source_endpoint="/stable/income-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )
    filing_only = normalize_fmp_core_statement_payload(
        symbol="MSFT",
        statement_type="balance_sheet",
        period_type="quarterly",
        payload=[
            {
                "symbol": "MSFT",
                "date": "2025-03-31",
                "period": "Q3",
                "fiscalYear": "2025",
                "filingDate": "2025-04-25",
                "cashAndCashEquivalents": 100,
            }
        ],
        source_endpoint="/stable/balance-sheet-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )
    unsafe = normalize_fmp_core_statement_payload(
        symbol="TSLA",
        statement_type="cash_flow",
        period_type="annual",
        payload=[
            {
                "symbol": "TSLA",
                "date": "2025-12-31",
                "period": "FY",
                "fiscalYear": "2025",
                "freeCashFlow": 100,
            }
        ],
        source_endpoint="/stable/cash-flow-statement",
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )

    diagnostic = coverage_diagnostics(
        symbols_requested=["AAPL", "MSFT", "TSLA", "META"],
        records=safe + filing_only + unsafe,
    )

    assert diagnostic["provider"] == "FMP"
    assert diagnostic["symbols_requested"] == ["AAPL", "MSFT", "TSLA", "META"]
    assert diagnostic["symbols_returned"] == ["AAPL", "MSFT", "TSLA"]
    assert diagnostic["missing_symbols"] == ["META"]
    assert diagnostic["statement_types_present"] == ["balance_sheet", "cash_flow", "income_statement"]
    assert diagnostic["period_types_present"] == ["annual", "quarterly"]
    assert diagnostic["total_records"] == 4
    assert diagnostic["timestamp_safe_records"] == 3
    assert diagnostic["timestamp_unsafe_records"] == 1
    assert diagnostic["records_with_acceptedDate"] == 2
    assert diagnostic["records_with_filingDate_only"] == 1
    assert diagnostic["records_missing_available_date"] == 1
    assert diagnostic["earliest_available_date"] == "2025-04-25"
    assert diagnostic["latest_available_date"] == "2025-10-31"
    assert "timestamp-unsafe records are diagnostics-only" in diagnostic["warnings"]


def test_api_key_does_not_appear_in_diagnostics_or_source_urls():
    records = normalize_fmp_core_statement_payload(
        symbol="AAPL",
        statement_type="income_statement",
        period_type="annual",
        payload=_income_payload(),
        source_endpoint=f"/stable/income-statement?apikey={SECRET}",
        source_url=f"https://financialmodelingprep.com/stable/income-statement?symbol=AAPL&apikey={SECRET}",
        api_key=SECRET,
        ingestion_timestamp="2026-06-02T10:00:00Z",
    )
    diagnostic = coverage_diagnostics(symbols_requested=["AAPL"], records=records)

    serialized = json.dumps({"records": [record.__dict__ for record in records], "diagnostic": diagnostic})
    assert SECRET not in serialized
    assert "apikey=REDACTED" in serialized


@pytest.mark.parametrize(
    ("statement_type", "source_endpoint"),
    [
        ("key_metrics", "/stable/key-metrics"),
        ("ratios", "/stable/ratios"),
        ("enterprise_values", "/stable/enterprise-values"),
        ("shares_float", "/stable/shares-float"),
        ("as_reported_income_statement", "/stable/income-statement-as-reported"),
    ],
)
def test_non_core_statement_targets_are_not_ingested(statement_type, source_endpoint):
    with pytest.raises(ValueError, match="unsupported FMP core statement target"):
        normalize_fmp_core_statement_payload(
            symbol="AAPL",
            statement_type=statement_type,
            period_type="annual",
            payload=_income_payload(),
            source_endpoint=source_endpoint,
            ingestion_timestamp="2026-06-02T10:00:00Z",
        )

    assert set(SUPPORTED_FMP_CORE_ENDPOINTS) == {
        "/stable/income-statement",
        "/stable/balance-sheet-statement",
        "/stable/cash-flow-statement",
    }
