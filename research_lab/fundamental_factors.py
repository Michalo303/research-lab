from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from research_lab.fundamentals_fmp import FmpFundamentalRecord, fundamentals_asof


CORE_STATEMENT_TYPES = {"income_statement", "balance_sheet", "cash_flow"}

FACTOR_FIELDS = (
    "revenue_growth_yoy",
    "net_income_growth_yoy",
    "operating_income_growth_yoy",
    "fcf_growth_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_assets",
    "return_on_equity",
    "debt_to_assets",
    "debt_to_equity",
    "net_debt",
    "interest_coverage",
    "free_cash_flow",
    "fcf_margin",
    "share_count_growth_yoy",
)

FIELD_ALIASES = {
    "revenue": ("revenue", "totalRevenue"),
    "gross_profit": ("grossProfit", "grossProfitLoss"),
    "operating_income": ("operatingIncome", "operatingIncomeLoss", "incomeFromOperations"),
    "net_income": ("netIncome", "netIncomeLoss", "netIncomeCommonStockholders"),
    "total_assets": ("totalAssets",),
    "total_equity": (
        "totalStockholdersEquity",
        "totalShareholdersEquity",
        "stockholdersEquity",
        "totalEquity",
    ),
    "total_debt": ("totalDebt",),
    "short_term_debt": ("shortTermDebt", "shortTermDebtAndCurrentPortionOfLongTermDebt"),
    "long_term_debt": ("longTermDebt", "longTermDebtNoncurrent"),
    "cash": ("cashAndCashEquivalents", "cashAndShortTermInvestments", "cash"),
    "interest_expense": ("interestExpense", "interestExpenseNonOperating"),
    "operating_cash_flow": (
        "netCashProvidedByOperatingActivities",
        "operatingCashFlow",
        "cashFlowFromOperations",
    ),
    "capital_expenditure": ("capitalExpenditure", "capitalExpenditures", "capitalExpenditureReported"),
    "free_cash_flow": ("freeCashFlow",),
    "shares": (
        "weightedAverageShsOut",
        "weightedAverageShsOutDil",
        "weightedAverageSharesOutstanding",
        "weightedAverageDilutedSharesOutstanding",
        "commonStockSharesOutstanding",
    ),
}


@dataclass(frozen=True)
class FundamentalFactorSnapshot:
    symbol: str
    asof_date: str
    period_type: str
    factors: dict[str, float]


@dataclass(frozen=True)
class FundamentalFactorSnapshotResult:
    asof_date: str
    period_type: str
    snapshots: list[FundamentalFactorSnapshot]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _FieldValue:
    value: float
    period_end_date: date
    available_date: date | None
    field_name: str
    statement_type: str


def build_fundamental_factor_snapshot(
    records: Iterable[FmpFundamentalRecord],
    asof_date: str | date | datetime,
    *,
    period_type: str = "annual",
) -> FundamentalFactorSnapshotResult:
    """Build timestamp-safe fundamental factor inputs from normalized FMP core rows."""
    record_list = list(records)
    requested_symbols = _ordered_symbols(record.symbol for record in record_list if record.symbol)
    safe_asof = fundamentals_asof(record_list, asof_date, include_unsafe=False)
    eligible = [
        record
        for record in safe_asof
        if record.period_type == period_type and record.statement_type in CORE_STATEMENT_TYPES
    ]
    symbols_with_records = _ordered_symbols(record.symbol for record in eligible if record.symbol)
    diagnostics = _initial_diagnostics(
        requested_symbols=requested_symbols,
        symbols_with_records=symbols_with_records,
        record_list=record_list,
        eligible=eligible,
        safe_asof=safe_asof,
    )

    snapshots = [
        FundamentalFactorSnapshot(
            symbol=symbol,
            asof_date=_date_label(asof_date),
            period_type=period_type,
            factors=_build_symbol_factors(symbol, eligible, diagnostics),
        )
        for symbol in symbols_with_records
    ]
    diagnostics["factor_fields_computed"] = sorted(diagnostics["factor_fields_computed"])
    diagnostics["factor_fields_missing"] = sorted(diagnostics["factor_fields_missing"])
    diagnostics["insufficient_history_for_yoy_growth"] = sorted(diagnostics["insufficient_history_for_yoy_growth"])
    diagnostics["unavailable_dilution_fields"] = sorted(diagnostics["unavailable_dilution_fields"])
    diagnostics["denominator_zero_warnings"] = sorted(diagnostics["denominator_zero_warnings"])
    return FundamentalFactorSnapshotResult(
        asof_date=_date_label(asof_date),
        period_type=period_type,
        snapshots=snapshots,
        diagnostics=diagnostics,
    )


def _build_symbol_factors(
    symbol: str,
    records: list[FmpFundamentalRecord],
    diagnostics: dict[str, Any],
) -> dict[str, float]:
    factors: dict[str, float] = {}
    symbol_records = [record for record in records if _normalize_symbol(record.symbol) == symbol]

    revenue = _latest(symbol_records, "income_statement", FIELD_ALIASES["revenue"])
    gross_profit = _latest(symbol_records, "income_statement", FIELD_ALIASES["gross_profit"])
    operating_income = _latest(symbol_records, "income_statement", FIELD_ALIASES["operating_income"])
    net_income = _latest(symbol_records, "income_statement", FIELD_ALIASES["net_income"])
    total_assets = _latest(symbol_records, "balance_sheet", FIELD_ALIASES["total_assets"])
    total_equity = _latest(symbol_records, "balance_sheet", FIELD_ALIASES["total_equity"])
    total_debt = _latest_debt(symbol_records)
    cash = _latest(symbol_records, "balance_sheet", FIELD_ALIASES["cash"])
    interest_expense = _latest(symbol_records, "income_statement", FIELD_ALIASES["interest_expense"])
    free_cash_flow = _latest_free_cash_flow(symbol_records)
    shares = _latest(symbol_records, "income_statement", FIELD_ALIASES["shares"])

    factors["revenue_growth_yoy"] = _growth_yoy(symbol, "revenue_growth_yoy", revenue, symbol_records, "income_statement", FIELD_ALIASES["revenue"], diagnostics)
    factors["net_income_growth_yoy"] = _growth_yoy(symbol, "net_income_growth_yoy", net_income, symbol_records, "income_statement", FIELD_ALIASES["net_income"], diagnostics)
    factors["operating_income_growth_yoy"] = _growth_yoy(symbol, "operating_income_growth_yoy", operating_income, symbol_records, "income_statement", FIELD_ALIASES["operating_income"], diagnostics)
    factors["fcf_growth_yoy"] = _fcf_growth_yoy(symbol, free_cash_flow, symbol_records, diagnostics)

    factors["gross_margin"] = _ratio(symbol, "gross_margin", gross_profit, revenue, "revenue", diagnostics)
    factors["operating_margin"] = _ratio(symbol, "operating_margin", operating_income, revenue, "revenue", diagnostics)
    factors["net_margin"] = _ratio(symbol, "net_margin", net_income, revenue, "revenue", diagnostics)
    factors["return_on_assets"] = _ratio(symbol, "return_on_assets", net_income, total_assets, "total_assets", diagnostics)
    factors["return_on_equity"] = _ratio(symbol, "return_on_equity", net_income, total_equity, "total_equity", diagnostics)
    factors["debt_to_assets"] = _ratio(symbol, "debt_to_assets", total_debt, total_assets, "total_assets", diagnostics)
    factors["debt_to_equity"] = _ratio(symbol, "debt_to_equity", total_debt, total_equity, "total_equity", diagnostics)
    factors["net_debt"] = _difference(symbol, "net_debt", total_debt, cash, diagnostics)
    factors["interest_coverage"] = _ratio(
        symbol,
        "interest_coverage",
        operating_income,
        _absolute_field_value(interest_expense),
        "interest_expense",
        diagnostics,
    )
    factors["free_cash_flow"] = _direct(symbol, "free_cash_flow", free_cash_flow, diagnostics)
    factors["fcf_margin"] = _ratio(symbol, "fcf_margin", free_cash_flow, revenue, "revenue", diagnostics)
    factors["share_count_growth_yoy"] = _share_count_growth_yoy(symbol, shares, symbol_records, diagnostics)
    return factors


def _initial_diagnostics(
    *,
    requested_symbols: list[str],
    symbols_with_records: list[str],
    record_list: list[FmpFundamentalRecord],
    eligible: list[FmpFundamentalRecord],
    safe_asof: list[FmpFundamentalRecord],
) -> dict[str, Any]:
    available_dates = sorted(
        parsed for record in eligible if (parsed := _parse_date(record.available_date)) is not None
    )
    latest_periods: dict[str, dict[str, str]] = {}
    for record in eligible:
        symbol = _normalize_symbol(record.symbol)
        period_end = _parse_date(record.period_end_date)
        if not symbol or period_end is None:
            continue
        by_statement = latest_periods.setdefault(symbol, {})
        current = _parse_date(by_statement.get(record.statement_type))
        if current is None or period_end > current:
            by_statement[record.statement_type] = period_end.isoformat()

    return {
        "symbols_requested": requested_symbols,
        "symbols_with_factor_snapshot": symbols_with_records,
        "missing_symbols": [symbol for symbol in requested_symbols if symbol not in set(symbols_with_records)],
        "factor_fields_computed": set(),
        "factor_fields_missing": set(),
        "insufficient_history_for_yoy_growth": set(),
        "unavailable_dilution_fields": set(),
        "denominator_zero_warnings": set(),
        "timestamp_unsafe_records_excluded": sum(1 for record in record_list if not record.timestamp_safe),
        "unsupported_external_records_excluded": sum(
            1 for record in safe_asof if record.statement_type not in CORE_STATEMENT_TYPES
        ),
        "earliest_available_date_used": available_dates[0].isoformat() if available_dates else None,
        "latest_available_date_used": available_dates[-1].isoformat() if available_dates else None,
        "latest_period_end_date_used": latest_periods,
    }


def _latest(
    records: list[FmpFundamentalRecord],
    statement_type: str,
    aliases: tuple[str, ...],
) -> _FieldValue | None:
    alias_keys = {_field_key(alias) for alias in aliases}
    candidates = [
        _field_value(record)
        for record in records
        if record.statement_type == statement_type and _field_key(record.field_name) in alias_keys
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.period_end_date, item.available_date or date.min, item.field_name))


def _latest_debt(records: list[FmpFundamentalRecord]) -> _FieldValue | None:
    total_debt = _latest(records, "balance_sheet", FIELD_ALIASES["total_debt"])
    if total_debt is not None:
        return total_debt
    short_term = _latest(records, "balance_sheet", FIELD_ALIASES["short_term_debt"])
    long_term = _latest(records, "balance_sheet", FIELD_ALIASES["long_term_debt"])
    if short_term is None or long_term is None or short_term.period_end_date != long_term.period_end_date:
        return None
    return _FieldValue(
        value=short_term.value + long_term.value,
        period_end_date=short_term.period_end_date,
        available_date=max(short_term.available_date or date.min, long_term.available_date or date.min),
        field_name="shortTermDebt+longTermDebt",
        statement_type="balance_sheet",
    )


def _latest_free_cash_flow(records: list[FmpFundamentalRecord]) -> _FieldValue | None:
    direct = _latest(records, "cash_flow", FIELD_ALIASES["free_cash_flow"])
    if direct is not None:
        return direct
    operating_cash_flow = _latest(records, "cash_flow", FIELD_ALIASES["operating_cash_flow"])
    capex = _latest(records, "cash_flow", FIELD_ALIASES["capital_expenditure"])
    if operating_cash_flow is None or capex is None or operating_cash_flow.period_end_date != capex.period_end_date:
        return None
    capex_outflow = capex.value if capex.value < 0 else -capex.value
    return _FieldValue(
        value=operating_cash_flow.value + capex_outflow,
        period_end_date=operating_cash_flow.period_end_date,
        available_date=max(operating_cash_flow.available_date or date.min, capex.available_date or date.min),
        field_name="netCashProvidedByOperatingActivities+capitalExpenditure",
        statement_type="cash_flow",
    )


def _field_value(record: FmpFundamentalRecord) -> _FieldValue | None:
    period_end = _parse_date(record.period_end_date)
    if period_end is None:
        return None
    return _FieldValue(
        value=record.value,
        period_end_date=period_end,
        available_date=_parse_date(record.available_date),
        field_name=record.field_name,
        statement_type=record.statement_type,
    )


def _prior_year_value(
    current: _FieldValue,
    records: list[FmpFundamentalRecord],
    statement_type: str,
    aliases: tuple[str, ...],
) -> _FieldValue | None:
    try:
        target = current.period_end_date.replace(year=current.period_end_date.year - 1)
    except ValueError:
        return None
    alias_keys = {_field_key(alias) for alias in aliases}
    candidates = [
        _field_value(record)
        for record in records
        if record.statement_type == statement_type
        and _field_key(record.field_name) in alias_keys
        and _parse_date(record.period_end_date) == target
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.available_date or date.min, item.field_name))


def _prior_year_free_cash_flow(
    current: _FieldValue,
    records: list[FmpFundamentalRecord],
) -> _FieldValue | None:
    try:
        target = current.period_end_date.replace(year=current.period_end_date.year - 1)
    except ValueError:
        return None
    period_records = [record for record in records if _parse_date(record.period_end_date) == target]
    return _latest_free_cash_flow(period_records)


def _growth_yoy(
    symbol: str,
    factor_name: str,
    current: _FieldValue | None,
    records: list[FmpFundamentalRecord],
    statement_type: str,
    aliases: tuple[str, ...],
    diagnostics: dict[str, Any],
) -> float:
    if current is None:
        return _missing(symbol, factor_name, diagnostics)
    prior = _prior_year_value(current, records, statement_type, aliases)
    if prior is None:
        diagnostics["insufficient_history_for_yoy_growth"].add(f"{symbol}:{factor_name}")
        return _missing(symbol, factor_name, diagnostics)
    return _ratio_delta(symbol, factor_name, current.value, prior.value, "prior_year_value", diagnostics)


def _fcf_growth_yoy(
    symbol: str,
    current: _FieldValue | None,
    records: list[FmpFundamentalRecord],
    diagnostics: dict[str, Any],
) -> float:
    if current is None:
        return _missing(symbol, "fcf_growth_yoy", diagnostics)
    prior = _prior_year_free_cash_flow(current, records)
    if prior is None:
        diagnostics["insufficient_history_for_yoy_growth"].add(f"{symbol}:fcf_growth_yoy")
        return _missing(symbol, "fcf_growth_yoy", diagnostics)
    return _ratio_delta(symbol, "fcf_growth_yoy", current.value, prior.value, "prior_year_fcf", diagnostics)


def _share_count_growth_yoy(
    symbol: str,
    current: _FieldValue | None,
    records: list[FmpFundamentalRecord],
    diagnostics: dict[str, Any],
) -> float:
    if current is None:
        diagnostics["unavailable_dilution_fields"].add(symbol)
        return _missing(symbol, "share_count_growth_yoy", diagnostics)
    prior = _prior_year_value(current, records, "income_statement", FIELD_ALIASES["shares"])
    if prior is None:
        diagnostics["insufficient_history_for_yoy_growth"].add(f"{symbol}:share_count_growth_yoy")
        return _missing(symbol, "share_count_growth_yoy", diagnostics)
    return _ratio_delta(symbol, "share_count_growth_yoy", current.value, prior.value, "prior_year_shares", diagnostics)


def _ratio(
    symbol: str,
    factor_name: str,
    numerator: _FieldValue | None,
    denominator: _FieldValue | None,
    denominator_label: str,
    diagnostics: dict[str, Any],
) -> float:
    if numerator is None or denominator is None:
        return _missing(symbol, factor_name, diagnostics)
    if denominator.value == 0:
        diagnostics["denominator_zero_warnings"].add(f"{symbol}:{factor_name}:{denominator_label}")
        return _missing(symbol, factor_name, diagnostics)
    value = numerator.value / denominator.value
    diagnostics["factor_fields_computed"].add(factor_name)
    return value


def _ratio_delta(
    symbol: str,
    factor_name: str,
    current_value: float,
    prior_value: float,
    denominator_label: str,
    diagnostics: dict[str, Any],
) -> float:
    if prior_value == 0:
        diagnostics["denominator_zero_warnings"].add(f"{symbol}:{factor_name}:{denominator_label}")
        return _missing(symbol, factor_name, diagnostics)
    diagnostics["factor_fields_computed"].add(factor_name)
    return (current_value - prior_value) / abs(prior_value)


def _difference(
    symbol: str,
    factor_name: str,
    left: _FieldValue | None,
    right: _FieldValue | None,
    diagnostics: dict[str, Any],
) -> float:
    if left is None or right is None:
        return _missing(symbol, factor_name, diagnostics)
    diagnostics["factor_fields_computed"].add(factor_name)
    return left.value - right.value


def _direct(
    symbol: str,
    factor_name: str,
    value: _FieldValue | None,
    diagnostics: dict[str, Any],
) -> float:
    if value is None:
        return _missing(symbol, factor_name, diagnostics)
    diagnostics["factor_fields_computed"].add(factor_name)
    return value.value


def _absolute_field_value(value: _FieldValue | None) -> _FieldValue | None:
    if value is None:
        return None
    return _FieldValue(
        value=abs(value.value),
        period_end_date=value.period_end_date,
        available_date=value.available_date,
        field_name=value.field_name,
        statement_type=value.statement_type,
    )


def _missing(symbol: str, factor_name: str, diagnostics: dict[str, Any]) -> float:
    diagnostics["factor_fields_missing"].add(f"{symbol}:{factor_name}")
    return math.nan


def _ordered_symbols(symbols: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").split(".", 1)[0].strip().upper()


def _field_key(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(field_name).lower())


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _date_label(value: str | date | datetime) -> str:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed is not None else str(value)
