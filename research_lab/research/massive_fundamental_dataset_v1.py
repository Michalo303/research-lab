from __future__ import annotations

import gzip
import hashlib
import json
import math
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.research.massive_fundamental_acquisition_v1 import (
    verify_massive_fundamental_bundle_v1,
)
from research_lab.research.massive_fundamental_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    QUALITY_MOMENTUM_COMPONENTS,
)


_PRICE_COLUMNS = {"open", "high", "low", "close", "volume", "raw_close", "dollar_volume", "eligible"}
_FLOW_ALIASES = {
    "revenue": ("revenues", "revenue"),
    "gross_profit": ("gross_profit", "gross_profit_loss"),
    "operating_income": ("operating_income_loss", "operating_income"),
    "net_income": ("net_income_loss", "net_income_loss_available_to_common_stockholders_basic"),
    "operating_cash_flow": (
        "net_cash_flow_from_operating_activities",
        "net_cash_flow_from_operating_activities_continuing",
    ),
}
_BALANCE_ALIASES = {
    "assets": ("assets",),
    "current_liabilities": ("current_liabilities",),
    "liabilities": ("liabilities",),
}
_BASE_FACTOR_IDS = tuple(factor_id for factor_id in FACTOR_DEFINITIONS_V1 if factor_id != "QUALITY_MOMENTUM")


def load_massive_fundamental_histories_v1(
    bundle_root: str | Path,
    expected_manifest_sha256: str,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    """Load only checksum-verified normalized histories from a completed local bundle."""

    root = Path(bundle_root).resolve()
    verification = verify_massive_fundamental_bundle_v1(root)
    if verification.get("status") != "PASS":
        raise ValueError("fundamental bundle verification failed.")
    manifest_path = root / "fundamental_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("fundamental manifest is invalid.")
    body = manifest_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != _required_sha(expected_manifest_sha256):
        raise ValueError("fundamental manifest hash mismatch.")
    try:
        manifest = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("fundamental manifest is invalid.") from exc
    _validate_manifest(manifest)
    histories: dict[str, dict[str, object]] = {}
    file_hashes: dict[str, str] = {}
    for record in manifest["records"]:
        if record["status"] != "USABLE":
            continue
        relative = Path(str(record["normalized_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("normalized path is invalid.")
        unresolved = root / relative
        if unresolved.is_symlink():
            raise ValueError("normalized path is a symlink.")
        path = unresolved.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("normalized path escapes bundle.") from exc
        if not path.is_file() or _file_sha256(path) != record["normalized_sha256"]:
            raise ValueError("normalized hash mismatch.")
        try:
            history = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("normalized history is invalid.") from exc
        _validate_history(history, record)
        ticker = str(record["lookup_ticker"])
        if ticker in histories:
            raise ValueError("duplicate normalized ticker.")
        histories[ticker] = history
        file_hashes[ticker] = str(record["normalized_sha256"])
    metadata: dict[str, object] = {
        "version": "massive_fundamental_history_load_metadata_v1",
        "fundamental_manifest_sha256": expected_manifest_sha256,
        "fundamental_canonical_manifest_sha256": manifest["canonical_manifest_sha256"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "history_count": len(histories),
        "normalized_file_sha256": dict(sorted(file_hashes.items())),
        "provider_calls_used": 0,
        "sealed_oos_rows_read": 0,
    }
    metadata["canonical_metadata_sha256"] = _canonical_sha(metadata)
    return histories, metadata


def build_point_in_time_fundamental_factor_panel_v1(
    histories: dict[str, dict[str, object]],
    price_frame: pd.DataFrame,
    *,
    development_start: str,
    development_end: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Build weekly factor values using only filings effective before each signal."""

    _validate_price_frame(price_frame)
    start = pd.Timestamp(development_start)
    end = pd.Timestamp(development_end)
    if start.tz is not None or end.tz is not None or start > end or end >= pd.Timestamp("2023-01-01"):
        raise ValueError("development interval is invalid.")
    sessions = price_frame.index.get_level_values("datetime").unique().sort_values()
    if sessions.empty or sessions.max() >= pd.Timestamp("2023-01-01"):
        raise ValueError("sealed OOS row exposed.")
    development_sessions = sessions[(sessions >= start) & (sessions <= end)]
    if development_sessions.empty:
        raise ValueError("development sessions are missing.")
    signal_dates = (
        pd.Series(development_sessions, index=development_sessions)
        .groupby(development_sessions.to_period("W-FRI"), sort=True)
        .max()
    )
    signal_date_set = set(pd.DatetimeIndex(signal_dates.to_numpy()))
    weekly = price_frame.loc[
        price_frame.index.get_level_values("datetime").isin(signal_date_set)
    ].copy()
    weekly = weekly.sort_index(kind="mergesort")
    close = price_frame["close"].astype("float64")
    momentum = close.groupby(level="instrument", sort=False).shift(21) / close.groupby(
        level="instrument", sort=False
    ).shift(252) - 1.0

    rows: list[dict[str, object]] = []
    for instrument, instrument_weekly in weekly.groupby(level="instrument", sort=True):
        history = histories.get(str(instrument))
        event_dates: list[pd.Timestamp] = []
        event_values: list[dict[str, float]] = []
        if history is not None:
            _validate_history(history, None)
            event_dates, event_values = _factor_events(history, sessions)
        for index, price_row in instrument_weekly.iterrows():
            timestamp = pd.Timestamp(index[0])
            values = {name: math.nan for name in _BASE_FACTOR_IDS}
            event_index = bisect_right(event_dates, timestamp) - 1
            if event_index >= 0:
                values.update(event_values[event_index])
            raw_close = float(price_row["raw_close"])
            rows.append(
                {
                    "datetime": timestamp,
                    "instrument": str(instrument),
                    "issuer_cik": str(history["cik"]) if history is not None else "",
                    **values,
                    "MOM_12_1": float(momentum.loc[index]),
                    "open": float(price_row["open"]),
                    "close": float(price_row["close"]),
                    "raw_close": raw_close,
                    "eligible": bool(price_row["eligible"]),
                }
            )
    panel = pd.DataFrame.from_records(rows).set_index(["datetime", "instrument"]).sort_index(kind="mergesort")
    component_columns = list(QUALITY_MOMENTUM_COMPONENTS)
    ranks = panel[component_columns].where(panel["eligible"], np.nan).groupby(level="datetime").rank(
        method="average", pct=True
    )
    panel["QUALITY_MOMENTUM"] = ranks.mean(axis=1, skipna=False)
    ordered_columns = [*FACTOR_DEFINITIONS_V1, "MOM_12_1", "issuer_cik", "open", "close", "raw_close", "eligible"]
    panel = panel[ordered_columns]
    finite_counts = panel[list(FACTOR_DEFINITIONS_V1)].notna().groupby(level="datetime").sum()
    coverage = {
        factor_id: {
            "minimum": int(finite_counts[factor_id].min()),
            "median": float(finite_counts[factor_id].median()),
            "maximum": int(finite_counts[factor_id].max()),
        }
        for factor_id in FACTOR_DEFINITIONS_V1
    }
    metadata: dict[str, object] = {
        "version": "point_in_time_fundamental_factor_panel_metadata_v1",
        "development_interval": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "weekly_signal_count": len(signal_dates),
        "row_count": len(panel),
        "history_count": len(histories),
        "coverage_by_factor": coverage,
        "availability_lag": "first_verified_session_strictly_after_filing_date",
        "provider_calls_used": 0,
        "sealed_oos_rows_read": 0,
    }
    metadata["canonical_metadata_sha256"] = _canonical_sha(metadata)
    return panel, metadata


def _factor_events(
    history: dict[str, object],
    sessions: pd.DatetimeIndex,
) -> tuple[list[pd.Timestamp], list[dict[str, float]]]:
    by_effective: dict[pd.Timestamp, list[dict[str, object]]] = {}
    for record in history["records"]:
        if record["timeframe"] not in {"annual", "quarterly"}:
            continue
        filing = pd.Timestamp(record["filing_date"])
        position = int(sessions.searchsorted(filing, side="right"))
        if position >= len(sessions):
            continue
        by_effective.setdefault(pd.Timestamp(sessions[position]), []).append(record)
    quarterly_records: dict[str, dict[str, object]] = {}
    annual_records: dict[tuple[object, str], dict[str, object]] = {}
    event_dates: list[pd.Timestamp] = []
    event_values: list[dict[str, float]] = []
    for effective in sorted(by_effective):
        for record in sorted(by_effective[effective], key=lambda item: (str(item["filing_date"]), str(item["canonical_record_sha256"]))):
            if record["timeframe"] == "quarterly":
                quarterly_records[str(record["period_end_date"])] = record
            else:
                annual_records[(record.get("fiscal_year"), str(record["period_end_date"]))] = record
        period_records = dict(quarterly_records)
        for annual in annual_records.values():
            derived = _derive_fourth_quarter(annual, quarterly_records)
            if derived is not None and str(derived["period_end_date"]) not in period_records:
                period_records[str(derived["period_end_date"])] = derived
        event_dates.append(effective)
        event_values.append(_compute_snapshot(period_records))
    return event_dates, event_values


def _derive_fourth_quarter(
    annual: dict[str, object],
    quarterly_records: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    fiscal_year = annual.get("fiscal_year")
    prior_quarters: dict[str, dict[str, object]] = {}
    for record in quarterly_records.values():
        fiscal_period = str(record.get("fiscal_period") or "").upper()
        if record.get("fiscal_year") == fiscal_year and fiscal_period in {"Q1", "Q2", "Q3"}:
            prior_quarters[fiscal_period] = record
    if set(prior_quarters) != {"Q1", "Q2", "Q3"}:
        return None
    statements: dict[str, dict[str, dict[str, object]]] = {}
    annual_statements = annual.get("statements")
    if not isinstance(annual_statements, dict):
        return None
    for statement_name, annual_fields in annual_statements.items():
        if not isinstance(annual_fields, dict):
            continue
        fields: dict[str, dict[str, object]] = {}
        for field_name, annual_field in annual_fields.items():
            if not isinstance(annual_field, dict):
                continue
            annual_value = annual_field.get("value")
            if not isinstance(annual_value, (int, float)) or isinstance(annual_value, bool) or not math.isfinite(float(annual_value)):
                continue
            if statement_name == "balance_sheet":
                derived_value = float(annual_value)
            else:
                quarter_values: list[float] = []
                annual_unit = str(annual_field.get("unit") or "").upper()
                for fiscal_period in ("Q1", "Q2", "Q3"):
                    quarter_statements = prior_quarters[fiscal_period].get("statements")
                    quarter_statement = quarter_statements.get(statement_name) if isinstance(quarter_statements, dict) else None
                    quarter_field = quarter_statement.get(field_name) if isinstance(quarter_statement, dict) else None
                    raw_value = quarter_field.get("value") if isinstance(quarter_field, dict) else None
                    quarter_unit = str(quarter_field.get("unit") or "").upper() if isinstance(quarter_field, dict) else ""
                    if (
                        quarter_unit != annual_unit
                        or not isinstance(raw_value, (int, float))
                        or isinstance(raw_value, bool)
                        or not math.isfinite(float(raw_value))
                    ):
                        quarter_values = []
                        break
                    quarter_values.append(float(raw_value))
                if len(quarter_values) != 3:
                    continue
                if statement_name == "income_statement" and field_name in {
                    "diluted_average_shares",
                    "basic_average_shares",
                }:
                    derived_value = float(annual_value)
                else:
                    derived_value = float(annual_value) - sum(quarter_values)
            if math.isfinite(derived_value):
                fields[str(field_name)] = {
                    "value": derived_value,
                    "unit": str(annual_field.get("unit") or ""),
                }
        if fields:
            statements[str(statement_name)] = fields
    if not statements:
        return None
    return {
        "cik": annual.get("cik"),
        "requested_instrument_id": annual.get("requested_instrument_id"),
        "requested_ticker": annual.get("requested_ticker"),
        "reported_tickers": _copy_list(annual.get("reported_tickers")),
        "filing_date": annual.get("filing_date"),
        "period_end_date": annual.get("period_end_date"),
        "timeframe": "quarterly",
        "fiscal_year": fiscal_year,
        "fiscal_period": "Q4_DERIVED_FROM_ANNUAL",
        "source_filing_identity": annual.get("source_filing_identity"),
        "statements": statements,
        "canonical_record_sha256": _canonical_sha(
            {
                "source_annual_record_sha256": annual.get("canonical_record_sha256"),
                "source_quarter_record_sha256": [
                    prior_quarters[key].get("canonical_record_sha256") for key in ("Q1", "Q2", "Q3")
                ],
                "derivation": "annual_minus_q1_q2_q3",
            }
        ),
    }


def _copy_list(raw: Any) -> list[object]:
    return list(raw) if isinstance(raw, list) else []


def _compute_snapshot(period_records: dict[str, dict[str, object]]) -> dict[str, float]:
    output = {name: math.nan for name in _BASE_FACTOR_IDS}
    periods = sorted(period_records)
    if len(periods) < 4:
        return output
    latest_four = periods[-4:]
    if not _quarter_sequence(latest_four):
        return output
    latest_record = period_records[latest_four[-1]]
    current = {name: _flow_sum(period_records, latest_four, aliases) for name, aliases in _FLOW_ALIASES.items()}
    assets = _field(latest_record, "balance_sheet", _BALANCE_ALIASES["assets"])
    current_liabilities = _field(latest_record, "balance_sheet", _BALANCE_ALIASES["current_liabilities"])
    liabilities = _field(latest_record, "balance_sheet", _BALANCE_ALIASES["liabilities"])
    gross = current["gross_profit"]
    operating = current["operating_income"]
    net_income = current["net_income"]
    cash_flow = current["operating_cash_flow"]
    if _positive_finite(assets):
        output["GROSS_PROFITABILITY"] = _divide(gross, assets)
        capital = assets - current_liabilities if math.isfinite(current_liabilities) else math.nan
        output["OPERATING_RETURN_ON_CAPITAL"] = _divide(operating, capital)
        output["CASH_PROFITABILITY"] = _divide(cash_flow, assets)
        output["ACCRUAL_QUALITY"] = _divide(cash_flow - net_income, assets) if math.isfinite(cash_flow) and math.isfinite(net_income) else math.nan
        output["EARNINGS_IMPROVEMENT"] = math.nan
        output["LOW_LEVERAGE"] = -_divide(liabilities, assets)
    if len(periods) >= 5 and _quarter_sequence(periods[-5:]):
        prior_year_assets = _field(period_records[periods[-5]], "balance_sheet", _BALANCE_ALIASES["assets"])
        output["LOW_ASSET_GROWTH"] = -_growth(assets, prior_year_assets)
    if len(periods) >= 8:
        latest_eight = periods[-8:]
        if _quarter_sequence(latest_eight):
            prior_four = latest_eight[:4]
            prior_revenue = _flow_sum(period_records, prior_four, _FLOW_ALIASES["revenue"])
            prior_income = _flow_sum(period_records, prior_four, _FLOW_ALIASES["net_income"])
            output["REVENUE_GROWTH"] = _growth(current["revenue"], prior_revenue)
            if _positive_finite(assets) and math.isfinite(net_income) and math.isfinite(prior_income):
                output["EARNINGS_IMPROVEMENT"] = (net_income - prior_income) / assets
            margins = []
            for period in latest_eight:
                record = period_records[period]
                quarter_revenue = _field(record, "income_statement", _FLOW_ALIASES["revenue"])
                quarter_operating = _field(record, "income_statement", _FLOW_ALIASES["operating_income"])
                margins.append(_divide(quarter_operating, quarter_revenue))
            if all(math.isfinite(value) for value in margins):
                output["MARGIN_STABILITY"] = -float(np.std(margins, ddof=0))
    return output


def _flow_sum(
    period_records: dict[str, dict[str, object]],
    periods: list[str],
    aliases: tuple[str, ...],
) -> float:
    statement = "cash_flow_statement" if aliases is _FLOW_ALIASES["operating_cash_flow"] else "income_statement"
    values = [_field(period_records[period], statement, aliases) for period in periods]
    return float(sum(values)) if all(math.isfinite(value) for value in values) else math.nan


def _field(record: dict[str, object], statement: str, aliases: tuple[str, ...]) -> float:
    statements = record.get("statements")
    body = statements.get(statement) if isinstance(statements, dict) else None
    if not isinstance(body, dict):
        return math.nan
    for alias in aliases:
        field = body.get(alias)
        if isinstance(field, dict):
            raw = field.get("value")
            unit = str(field.get("unit") or "").upper()
            if (
                unit == "USD"
                and isinstance(raw, (int, float))
                and not isinstance(raw, bool)
                and math.isfinite(float(raw))
            ):
                return float(raw)
    return math.nan


def _quarter_sequence(periods: list[str]) -> bool:
    if len(periods) < 2:
        return True
    dates = [pd.Timestamp(value) for value in periods]
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    return all(60 <= gap <= 120 for gap in gaps)


def _divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if math.isfinite(numerator) and _positive_finite(denominator) else math.nan


def _growth(current: float, prior: float) -> float:
    return (current - prior) / abs(prior) if math.isfinite(current) and math.isfinite(prior) and prior != 0.0 else math.nan


def _positive_finite(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def _validate_price_frame(frame: Any) -> None:
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != _PRICE_COLUMNS:
        raise ValueError("price frame columns are invalid.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"]:
        raise ValueError("price frame index is invalid.")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("price frame index must be unique and sorted.")
    datetimes = frame.index.get_level_values("datetime")
    if not isinstance(datetimes, pd.DatetimeIndex) or datetimes.tz is not None:
        raise ValueError("price timestamps are invalid.")
    numeric = frame[sorted(_PRICE_COLUMNS - {"eligible"})].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all() or not pd.api.types.is_bool_dtype(frame["eligible"]):
        raise ValueError("price values are invalid.")


def _validate_manifest(raw: Any) -> None:
    if not isinstance(raw, dict) or raw.get("version") != "massive_fundamental_dataset_manifest_v1":
        raise ValueError("fundamental manifest version is invalid.")
    declared = raw.get("canonical_manifest_sha256")
    if declared != _canonical_sha({key: value for key, value in raw.items() if key != "canonical_manifest_sha256"}):
        raise ValueError("fundamental manifest canonical hash mismatch.")
    if raw.get("filing_interval") != {"start": "2009-01-01", "end": "2022-12-31"}:
        raise ValueError("fundamental filing interval is invalid.")
    if not isinstance(raw.get("records"), list):
        raise ValueError("fundamental manifest records are invalid.")


def _validate_history(raw: Any, manifest_record: dict[str, object] | None) -> None:
    if not isinstance(raw, dict) or raw.get("version") != "massive_normalized_fundamental_history_v1":
        raise ValueError("normalized history version is invalid.")
    declared = raw.get("canonical_history_sha256")
    if declared != _canonical_sha({key: value for key, value in raw.items() if key != "canonical_history_sha256"}):
        raise ValueError("normalized history canonical hash mismatch.")
    if manifest_record is not None and (
        raw.get("instrument_id") != manifest_record.get("instrument_id")
        or raw.get("lookup_ticker") != manifest_record.get("lookup_ticker")
        or raw.get("cik") != manifest_record.get("cik")
    ):
        raise ValueError("normalized history identity mismatch.")
    records = raw.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("normalized history records are invalid.")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("normalized record is invalid.")
        declared_record = record.get("canonical_record_sha256")
        if declared_record != _canonical_sha({key: value for key, value in record.items() if key != "canonical_record_sha256"}):
            raise ValueError("normalized record canonical hash mismatch.")
        if record.get("timeframe") not in {"annual", "quarterly"} or str(record.get("filing_date", "")) > "2022-12-31":
            raise ValueError("normalized record temporal boundary is invalid.")


def _required_sha(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError("expected manifest SHA-256 is invalid.")
    return raw


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
