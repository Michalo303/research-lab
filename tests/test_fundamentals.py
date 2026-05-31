import json

from research_lab.fundamentals import (
    FundamentalRow,
    dedupe_fundamental_rows,
    enrich_smartmoney_fundamentals,
    filter_fundamentals_as_of,
    fundamental_coverage_rows,
    is_timestamp_safe,
    reject_timestamp_unsafe_rows,
)


def test_fundamental_coverage_missing_does_not_create_fake_values():
    rows = fundamental_coverage_rows([{"ticker": "SPY", "family": "SWING"}], fundamentals_by_ticker={})

    row = rows[0]
    assert row["ticker"] == "SPY"
    assert row["coverage_status"] == "missing"
    assert row["valuation"] == {}
    assert row["quality"] == {}
    assert row["debt"] == {}
    assert row["growth"] == {}


def test_enrich_smartmoney_fundamentals_writes_missing_coverage(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({"ticker": "SPY", "tags": ["smart_money"], "family": "SWING"}) + "\n", encoding="utf-8")

    result = enrich_smartmoney_fundamentals(tmp_path, "2026-W21")

    assert result["csv_path"].exists()
    assert result["report_path"].exists()
    assert result["rows"][0]["coverage_status"] == "missing"
    assert result["rows"][0]["valuation"] == {}


def test_period_end_date_alone_is_not_timestamp_safe():
    row = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        field_name="revenue",
        value=100.0,
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )

    assert is_timestamp_safe(row) is False


def test_explicit_filing_or_asof_date_makes_row_timestamp_safe():
    filing_row = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        filing_date="2025-11-01",
        field_name="revenue",
        value=100.0,
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )
    asof_row = FundamentalRow(
        symbol="MSFT",
        provider="unit",
        statement_type="balance_sheet",
        fiscal_period="Q1",
        fiscal_year=2026,
        period_end_date="2025-09-30",
        asof_date="2025-10-28",
        field_name="cash",
        value=50.0,
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )

    assert is_timestamp_safe(filing_row) is True
    assert is_timestamp_safe(asof_row) is True


def test_reject_timestamp_unsafe_rows_reports_missing_availability_anchor():
    safe = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        filing_date="2025-11-01",
        field_name="revenue",
        value=100.0,
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )
    unsafe = FundamentalRow(
        symbol="MSFT",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-06-30",
        field_name="revenue",
        value=90.0,
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )

    accepted, rejected = reject_timestamp_unsafe_rows([safe, unsafe])

    assert accepted == [safe]
    assert rejected == [
        {
            "symbol": "MSFT",
            "provider": "unit",
            "field_name": "revenue",
            "reason": "missing explicit filing/accepted/available/as-of date",
        }
    ]


def test_filter_fundamentals_as_of_excludes_future_filings_without_using_period_end_date():
    rows = [
        FundamentalRow(
            symbol="AAPL",
            provider="unit",
            statement_type="income_statement",
            fiscal_period="FY",
            fiscal_year=2024,
            period_end_date="2024-09-30",
            filing_date="2024-11-01",
            field_name="revenue",
            value=90.0,
            ingestion_timestamp="2026-01-01T00:00:00Z",
        ),
        FundamentalRow(
            symbol="AAPL",
            provider="unit",
            statement_type="income_statement",
            fiscal_period="FY",
            fiscal_year=2025,
            period_end_date="2025-09-30",
            filing_date="2025-11-01",
            field_name="revenue",
            value=100.0,
            ingestion_timestamp="2026-01-01T00:00:00Z",
        ),
        FundamentalRow(
            symbol="MSFT",
            provider="unit",
            statement_type="income_statement",
            fiscal_period="FY",
            fiscal_year=2025,
            period_end_date="2025-06-30",
            field_name="revenue",
            value=80.0,
            ingestion_timestamp="2026-01-01T00:00:00Z",
        ),
    ]

    filtered = filter_fundamentals_as_of(rows, "2025-10-31")

    assert [row.symbol for row in filtered] == ["AAPL"]
    assert filtered[0].fiscal_year == 2024


def test_dedupe_fundamental_rows_prefers_latest_available_restatement_then_ingestion_time():
    older = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        filing_date="2025-11-01",
        field_name="eps",
        value=6.0,
        provider_record_id="old",
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )
    restated = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        filing_date="2025-12-15",
        field_name="eps",
        value=6.2,
        provider_record_id="restated",
        ingestion_timestamp="2026-01-01T00:00:00Z",
    )
    duplicate = FundamentalRow(
        symbol="AAPL",
        provider="unit",
        statement_type="income_statement",
        fiscal_period="FY",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        filing_date="2025-12-15",
        field_name="eps",
        value=6.3,
        provider_record_id="duplicate",
        ingestion_timestamp="2026-01-02T00:00:00Z",
    )

    selected = dedupe_fundamental_rows([older, duplicate, restated])

    assert selected == [duplicate]
