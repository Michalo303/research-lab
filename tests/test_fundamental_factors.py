import math

from research_lab.fundamentals_fmp import FmpFundamentalRecord
from research_lab.fundamental_factors import build_fundamental_factor_snapshot


def _record(
    *,
    symbol="AAPL",
    statement_type="income_statement",
    period_type="annual",
    period_end_date="2025-12-31",
    available_date="2026-02-15",
    field_name="revenue",
    value=100.0,
    timestamp_safe=True,
    source_endpoint="/stable/income-statement",
):
    return FmpFundamentalRecord(
        symbol=symbol,
        provider="FMP",
        statement_type=statement_type,
        period_type=period_type,
        fiscal_year=int(period_end_date[:4]),
        fiscal_period="FY",
        period_end_date=period_end_date,
        filing_date=available_date,
        accepted_date=available_date,
        available_date=available_date,
        field_name=field_name,
        value=float(value),
        currency="USD",
        source_url="",
        source_endpoint=source_endpoint,
        provider_record_id=f"{symbol}:{statement_type}:{period_end_date}:{field_name}",
        ingestion_timestamp="2026-06-02T10:00:00Z",
        timestamp_safe=timestamp_safe,
        timestamp_confidence="acceptedDate" if timestamp_safe else "unsafe_missing_accepted_or_filing_date",
        timestamp_source="acceptedDate" if timestamp_safe else "missing",
    )


def _income(period_end_date, available_date, **fields):
    return [
        _record(
            statement_type="income_statement",
            period_end_date=period_end_date,
            available_date=available_date,
            field_name=name,
            value=value,
            source_endpoint="/stable/income-statement",
        )
        for name, value in fields.items()
    ]


def _balance(period_end_date, available_date, **fields):
    return [
        _record(
            statement_type="balance_sheet",
            period_end_date=period_end_date,
            available_date=available_date,
            field_name=name,
            value=value,
            source_endpoint="/stable/balance-sheet-statement",
        )
        for name, value in fields.items()
    ]


def _cash_flow(period_end_date, available_date, **fields):
    return [
        _record(
            statement_type="cash_flow",
            period_end_date=period_end_date,
            available_date=available_date,
            field_name=name,
            value=value,
            source_endpoint="/stable/cash-flow-statement",
        )
        for name, value in fields.items()
    ]


def _snapshot(records, asof_date="2026-03-01"):
    result = build_fundamental_factor_snapshot(records, asof_date, period_type="annual")
    assert len(result.snapshots) == 1
    return result.snapshots[0], result.diagnostics


def test_asof_snapshot_excludes_future_available_date_records():
    records = (
        _income("2024-12-31", "2025-02-15", revenue=100, grossProfit=50)
        + _income("2025-12-31", "2026-02-15", revenue=200, grossProfit=180)
    )

    snapshot, diagnostics = _snapshot(records, asof_date="2025-12-31")

    assert snapshot.symbol == "AAPL"
    assert snapshot.asof_date == "2025-12-31"
    assert snapshot.factors["gross_margin"] == 0.5
    assert diagnostics["latest_available_date_used"] == "2025-02-15"


def test_timestamp_unsafe_records_are_excluded_by_default():
    safe = _income("2024-12-31", "2025-02-15", revenue=100, grossProfit=50)
    unsafe = [
        _record(
            statement_type="income_statement",
            period_end_date="2025-12-31",
            available_date=None,
            field_name="revenue",
            value=200,
            timestamp_safe=False,
        ),
        _record(
            statement_type="income_statement",
            period_end_date="2025-12-31",
            available_date=None,
            field_name="grossProfit",
            value=180,
            timestamp_safe=False,
        ),
    ]

    snapshot, diagnostics = _snapshot(safe + unsafe, asof_date="2026-03-01")

    assert snapshot.factors["gross_margin"] == 0.5
    assert diagnostics["timestamp_unsafe_records_excluded"] == 2


def test_latest_available_period_is_selected_correctly():
    records = (
        _income("2024-12-31", "2025-02-15", revenue=100, netIncome=10)
        + _income("2025-12-31", "2026-02-15", revenue=200, netIncome=50)
    )

    snapshot, diagnostics = _snapshot(records, asof_date="2026-03-01")

    assert snapshot.factors["net_margin"] == 0.25
    assert diagnostics["latest_period_end_date_used"]["AAPL"]["income_statement"] == "2025-12-31"


def test_yoy_revenue_growth_is_computed_only_with_prior_year_comparable_period():
    records = (
        _income("2024-12-31", "2025-02-15", revenue=100)
        + _income("2025-12-31", "2026-02-15", revenue=120)
    )

    snapshot, diagnostics = _snapshot(records, asof_date="2026-03-01")

    assert snapshot.factors["revenue_growth_yoy"] == 0.2
    assert "revenue_growth_yoy" in diagnostics["factor_fields_computed"]


def test_yoy_growth_does_not_use_future_data():
    records = (
        _income("2024-12-31", "2026-04-01", revenue=100)
        + _income("2025-12-31", "2026-02-15", revenue=120)
    )

    snapshot, diagnostics = _snapshot(records, asof_date="2026-03-01")

    assert math.isnan(snapshot.factors["revenue_growth_yoy"])
    assert "AAPL:revenue_growth_yoy" in diagnostics["insufficient_history_for_yoy_growth"]


def test_gross_operating_and_net_margins_compute_correctly():
    records = _income("2025-12-31", "2026-02-15", revenue=200, grossProfit=80, operatingIncome=50, netIncome=30)

    snapshot, _diagnostics = _snapshot(records)

    assert snapshot.factors["gross_margin"] == 0.4
    assert snapshot.factors["operating_margin"] == 0.25
    assert snapshot.factors["net_margin"] == 0.15


def test_roa_and_roe_compute_when_balance_sheet_data_exists():
    records = _income("2025-12-31", "2026-02-15", netIncome=30) + _balance(
        "2025-12-31",
        "2026-02-15",
        totalAssets=300,
        totalStockholdersEquity=150,
    )

    snapshot, _diagnostics = _snapshot(records)

    assert snapshot.factors["return_on_assets"] == 0.1
    assert snapshot.factors["return_on_equity"] == 0.2


def test_debt_to_assets_and_debt_to_equity_compute_correctly():
    records = _balance("2025-12-31", "2026-02-15", totalDebt=60, totalAssets=300, totalStockholdersEquity=150)

    snapshot, _diagnostics = _snapshot(records)

    assert snapshot.factors["debt_to_assets"] == 0.2
    assert snapshot.factors["debt_to_equity"] == 0.4


def test_free_cash_flow_and_fcf_margin_compute_correctly():
    records = _income("2025-12-31", "2026-02-15", revenue=200) + _cash_flow(
        "2025-12-31",
        "2026-02-15",
        netCashProvidedByOperatingActivities=70,
        capitalExpenditure=-20,
    )

    snapshot, _diagnostics = _snapshot(records)

    assert snapshot.factors["free_cash_flow"] == 50
    assert snapshot.factors["fcf_margin"] == 0.25


def test_missing_fields_produce_nan_and_diagnostics_not_zero():
    records = _income("2025-12-31", "2026-02-15", revenue=200)

    snapshot, diagnostics = _snapshot(records)

    assert math.isnan(snapshot.factors["gross_margin"])
    assert "AAPL:gross_margin" in diagnostics["factor_fields_missing"]


def test_zero_denominators_produce_nan_and_diagnostics():
    records = _income("2025-12-31", "2026-02-15", revenue=0, grossProfit=80)

    snapshot, diagnostics = _snapshot(records)

    assert math.isnan(snapshot.factors["gross_margin"])
    assert "AAPL:gross_margin:revenue" in diagnostics["denominator_zero_warnings"]


def test_dilution_is_marked_unavailable_if_share_count_fields_are_missing():
    records = _income("2025-12-31", "2026-02-15", revenue=200)

    snapshot, diagnostics = _snapshot(records)

    assert math.isnan(snapshot.factors["share_count_growth_yoy"])
    assert "AAPL" in diagnostics["unavailable_dilution_fields"]


def test_unsupported_external_endpoints_are_not_used():
    records = [
        _record(
            statement_type="key_metrics",
            period_end_date="2025-12-31",
            available_date="2026-02-15",
            field_name="enterpriseValue",
            value=999,
            source_endpoint="/stable/key-metrics",
        )
    ]

    result = build_fundamental_factor_snapshot(records, "2026-03-01", period_type="annual")

    assert result.snapshots == []
    assert result.diagnostics["unsupported_external_records_excluded"] == 1


def test_no_ranking_score_weight_or_strategy_output_is_produced():
    records = _income("2025-12-31", "2026-02-15", revenue=200, netIncome=30)

    snapshot, _diagnostics = _snapshot(records)

    forbidden_terms = ("rank", "ranking", "score", "weight", "strategy", "backtest")
    output_keys = set(snapshot.factors) | set(snapshot.__dict__)
    assert not any(term in key.lower() for key in output_keys for term in forbidden_terms)
