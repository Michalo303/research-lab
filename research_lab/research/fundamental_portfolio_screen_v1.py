from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from research_lab.research.massive_fundamental_catalog_v1 import FACTOR_DEFINITIONS_V1


SCREEN_VERSION = "fundamental_portfolio_screen_v1"
COSTS = {
    "base_bps_one_way": 15.0,
    "stress_bps_one_way": 30.0,
    "severe_bps_one_way": 50.0,
}
MINIMUM_CROSS_SECTION = 500
MINIMUM_WEEKS = 156
PORTFOLIO_SIZE = 15
_PANEL_COLUMNS = {*FACTOR_DEFINITIONS_V1, "MOM_12_1", "issuer_cik", "open", "close", "raw_close", "eligible"}
_PRICE_COLUMNS = {"open", "high", "low", "close", "volume", "raw_close", "dollar_volume", "eligible"}


def run_fundamental_portfolio_screen_v1(
    factor_panel: pd.DataFrame,
    price_frame: pd.DataFrame,
    spy_prices: pd.DataFrame,
    factor_metadata: dict[str, object],
) -> dict[str, object]:
    """Evaluate the ten frozen factors as directly tradable weekly portfolios."""

    return _run_fundamental_portfolio_screen_v1(
        factor_panel,
        price_frame,
        spy_prices,
        factor_metadata,
        minimum_cross_section=MINIMUM_CROSS_SECTION,
        minimum_weeks=MINIMUM_WEEKS,
        portfolio_size=PORTFOLIO_SIZE,
    )


def _run_fundamental_portfolio_screen_v1(
    factor_panel: pd.DataFrame,
    price_frame: pd.DataFrame,
    spy_prices: pd.DataFrame,
    factor_metadata: dict[str, object],
    *,
    minimum_cross_section: int,
    minimum_weeks: int,
    portfolio_size: int,
) -> dict[str, object]:
    factor_ids, catalog_sha256 = _validate_catalog(factor_metadata)
    panel = _validate_panel(factor_panel, factor_ids)
    prices = _validate_prices(price_frame)
    spy = _validate_spy(spy_prices)
    if minimum_cross_section <= 0 or minimum_weeks <= 0 or portfolio_size <= 0:
        raise ValueError("screen bounds must be positive.")
    signal_dates = panel.index.get_level_values("datetime").unique().sort_values()
    sessions = prices.index.get_level_values("datetime").unique().sort_values()
    end_date = signal_dates.max()
    signal_to_execution: dict[pd.Timestamp, pd.Timestamp] = {}
    for signal in signal_dates:
        position = int(sessions.searchsorted(signal, side="right"))
        if position < len(sessions) and sessions[position] <= end_date:
            signal_to_execution[pd.Timestamp(signal)] = pd.Timestamp(sessions[position])
    if not signal_to_execution:
        raise ValueError("no next-session executions are available.")

    universe_targets: dict[pd.Timestamp, dict[str, float]] = {}
    for signal, cross_section in panel.groupby(level="datetime", sort=True):
        if signal not in signal_to_execution:
            continue
        eligible = sorted(
            str(instrument)
            for instrument in cross_section.loc[cross_section["eligible"]].index.get_level_values("instrument")
        )
        universe_targets[pd.Timestamp(signal)] = (
            {instrument: 1.0 / len(eligible) for instrument in eligible} if eligible else {}
        )
    baseline_stress_sim = _simulate_weekly_targets(
        universe_targets,
        prices,
        signal_to_execution=signal_to_execution,
        cost_bps=COSTS["stress_bps_one_way"],
        end_date=end_date,
    )
    baseline_stress = _performance_metrics(baseline_stress_sim["daily_returns"])
    baseline_base = _performance_metrics(
        _simulate_weekly_targets(
            universe_targets,
            prices,
            signal_to_execution=signal_to_execution,
            cost_bps=COSTS["base_bps_one_way"],
            end_date=end_date,
        )["daily_returns"]
    )
    first_execution = min(signal_to_execution.values())
    spy_base = _performance_metrics(_simulate_buy_and_hold(spy, first_execution, end_date, COSTS["base_bps_one_way"]))
    spy_stress = _performance_metrics(_simulate_buy_and_hold(spy, first_execution, end_date, COSTS["stress_bps_one_way"]))

    factor_results: dict[str, dict[str, object]] = {}
    for factor_id in factor_ids:
        targets, coverage = _factor_targets(
            panel,
            factor_id=factor_id,
            signal_to_execution=signal_to_execution,
            portfolio_size=portfolio_size,
        )
        simulations = {
            "base": _simulate_weekly_targets(
                targets, prices, signal_to_execution=signal_to_execution,
                cost_bps=COSTS["base_bps_one_way"], end_date=end_date,
            ),
            "stress": _simulate_weekly_targets(
                targets, prices, signal_to_execution=signal_to_execution,
                cost_bps=COSTS["stress_bps_one_way"], end_date=end_date,
            ),
            "severe": _simulate_weekly_targets(
                targets, prices, signal_to_execution=signal_to_execution,
                cost_bps=COSTS["severe_bps_one_way"], end_date=end_date,
            ),
        }
        performance = {name: _performance_metrics(value["daily_returns"]) for name, value in simulations.items()}
        base = performance["base"]
        stress = performance["stress"]
        severe = performance["severe"]
        positive_year_count = sum(value > 0.0 for value in base["calendar_year_returns"].values())
        metrics: dict[str, object] = {
            "factor_id": factor_id,
            "weekly_observation_count": len(coverage),
            "minimum_weekly_coverage": min(coverage) if coverage else 0,
            "median_weekly_coverage": float(np.median(coverage)) if coverage else 0.0,
            "maximum_weekly_coverage": max(coverage) if coverage else 0,
            "base_net_cagr": base["net_cagr"],
            "stress_net_cagr": stress["net_cagr"],
            "severe_net_cagr": severe["net_cagr"],
            "base_net_total_return": base["net_total_return"],
            "stress_net_total_return": stress["net_total_return"],
            "severe_net_total_return": severe["net_total_return"],
            "base_max_drawdown": base["max_drawdown"],
            "stress_max_drawdown": stress["max_drawdown"],
            "severe_max_drawdown": severe["max_drawdown"],
            "base_sharpe": base["sharpe"],
            "base_sortino": base["sortino"],
            "base_calmar": base["calmar"],
            "worst_calendar_year_return": base["worst_calendar_year_return"],
            "worst_rolling_12_month_return": base["worst_rolling_12_month_return"],
            "calendar_year_returns": base["calendar_year_returns"],
            "positive_calendar_year_count": positive_year_count,
            "stress_active_cagr_vs_universe": stress["net_cagr"] - baseline_stress["net_cagr"],
            "base_active_cagr_vs_spy": base["net_cagr"] - spy_base["net_cagr"],
            "best_year_removed_cumulative_return": _best_year_removed_return(
                simulations["base"]["daily_returns"], base["calendar_year_returns"]
            ),
            "maximum_positive_instrument_contribution_share": _positive_concentration(
                {key: value for key, value in simulations["base"]["instrument_contributions"].items() if key != "__COSTS__"}
            ),
            "maximum_positive_year_return_share": _positive_concentration(base["calendar_year_returns"]),
            "annualized_turnover": simulations["base"]["annualized_turnover"],
            "total_turnover": simulations["base"]["total_turnover"],
            "average_holdings": float(np.mean([len(value) for value in targets.values()])) if targets else 0.0,
            "average_exposure": float(np.mean([sum(value.values()) for value in targets.values()])) if targets else 0.0,
            "forced_missing_open_loss_count": simulations["base"]["forced_missing_open_loss_count"],
        }
        failures = _continuation_failure_taxonomy(
            metrics,
            minimum_cross_section=minimum_cross_section,
            minimum_weeks=minimum_weeks,
        )
        metrics["decision"] = "FACTOR_CONTINUE" if not failures else "FACTOR_STOP"
        metrics["failure_taxonomy"] = failures
        factor_results[factor_id] = metrics

    continuing = [factor_id for factor_id in factor_ids if factor_results[factor_id]["decision"] == "FACTOR_CONTINUE"]
    result: dict[str, object] = {
        "version": SCREEN_VERSION,
        "status": "COMPLETED",
        "factor_catalog_sha256": catalog_sha256,
        "factor_count": len(factor_ids),
        "ordered_factor_ids": list(factor_ids),
        "costs": dict(COSTS),
        "portfolio_size": portfolio_size,
        "execution_timing": "signal_week_last_session_to_next_verified_session_open",
        "factors": factor_results,
        "continuing_factor_ids": continuing,
        "stopped_factor_ids": [factor_id for factor_id in factor_ids if factor_id not in continuing],
        "eligible_universe_baseline": {
            "base_net_cagr": baseline_base["net_cagr"],
            "stress_net_cagr": baseline_stress["net_cagr"],
            "base_max_drawdown": baseline_base["max_drawdown"],
        },
        "spy_baseline": {
            "base_net_cagr": spy_base["net_cagr"],
            "stress_net_cagr": spy_stress["net_cagr"],
            "base_max_drawdown": spy_base["max_drawdown"],
        },
        "sector_concentration_evaluated": False,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
        "broker_action_authorized": False,
        "registry_write_authorized": False,
        "deployment_authorized": False,
    }
    result["canonical_screen_sha256"] = _canonical_sha(result)
    return result


def _simulate_weekly_targets(
    targets: Mapping[pd.Timestamp, Mapping[str, float]],
    price_frame: pd.DataFrame,
    *,
    signal_to_execution: Mapping[pd.Timestamp, pd.Timestamp],
    cost_bps: float,
    end_date: pd.Timestamp,
) -> dict[str, object]:
    if cost_bps < 0.0 or not math.isfinite(cost_bps):
        raise ValueError("cost_bps is invalid.")
    execution_targets = {
        pd.Timestamp(signal_to_execution[signal]): dict(weights)
        for signal, weights in targets.items()
        if signal in signal_to_execution and pd.Timestamp(signal_to_execution[signal]) <= end_date
    }
    if not execution_targets:
        return _empty_simulation()
    first_execution = min(execution_targets)
    sessions = price_frame.index.get_level_values("datetime").unique().sort_values()
    sessions = sessions[(sessions >= first_execution) & (sessions <= end_date)]
    positions: dict[str, float] = {}
    last_close: dict[str, float] = {}
    cash = 1.0
    previous_equity = 1.0
    daily_returns: list[float] = []
    return_dates: list[pd.Timestamp] = []
    contributions: dict[str, float] = {}
    total_turnover = 0.0
    total_cost_fraction = 0.0
    forced_missing = 0

    for session in sessions:
        session = pd.Timestamp(session)
        is_execution = session in execution_targets
        if is_execution:
            for instrument in list(positions):
                row = _price_row(price_frame, session, instrument)
                if row is None or not _positive(row["open"]):
                    contributions[instrument] = contributions.get(instrument, 0.0) - positions[instrument]
                    positions[instrument] = 0.0
                    forced_missing += 1
                    continue
                prior = last_close.get(instrument)
                if prior is not None and _positive(prior):
                    old_value = positions[instrument]
                    positions[instrument] = old_value * float(row["open"]) / prior
                    contributions[instrument] = contributions.get(instrument, 0.0) + positions[instrument] - old_value
            equity_open = cash + sum(positions.values())
            current_weights = {
                instrument: value / equity_open
                for instrument, value in positions.items()
                if equity_open > 0.0 and value > 0.0
            }
            requested = dict(execution_targets[session])
            valid_target = {
                instrument: float(weight)
                for instrument, weight in requested.items()
                if weight > 0.0 and (row := _price_row(price_frame, session, instrument)) is not None and _positive(row["open"])
            }
            turnover = sum(
                abs(valid_target.get(instrument, 0.0) - current_weights.get(instrument, 0.0))
                for instrument in set(valid_target) | set(current_weights)
            )
            total_turnover += turnover
            cost = equity_open * (cost_bps / 10_000.0) * turnover
            total_cost_fraction += cost / previous_equity if previous_equity > 0.0 else 0.0
            contributions["__COSTS__"] = contributions.get("__COSTS__", 0.0) - cost
            equity_after_cost = max(0.0, equity_open - cost)
            positions = {instrument: equity_after_cost * weight for instrument, weight in valid_target.items()}
            cash = equity_after_cost * max(0.0, 1.0 - sum(valid_target.values()))
            last_close = {}
            for instrument in list(positions):
                row = _price_row(price_frame, session, instrument)
                opening = float(row["open"])
                closing = float(row["close"])
                old_value = positions[instrument]
                positions[instrument] = old_value * closing / opening
                contributions[instrument] = contributions.get(instrument, 0.0) + positions[instrument] - old_value
                last_close[instrument] = closing
        else:
            for instrument in list(positions):
                row = _price_row(price_frame, session, instrument)
                if row is None or not _positive(row["close"]):
                    continue
                prior = last_close.get(instrument)
                if prior is not None and _positive(prior):
                    old_value = positions[instrument]
                    positions[instrument] = old_value * float(row["close"]) / prior
                    contributions[instrument] = contributions.get(instrument, 0.0) + positions[instrument] - old_value
                last_close[instrument] = float(row["close"])
        equity = cash + sum(positions.values())
        daily_return = equity / previous_equity - 1.0 if previous_equity > 0.0 else -1.0
        daily_returns.append(max(-1.0, float(daily_return)))
        return_dates.append(session)
        previous_equity = equity
    series = pd.Series(daily_returns, index=pd.DatetimeIndex(return_dates), dtype="float64")
    return {
        "daily_returns": series,
        "first_execution_date": first_execution.date().isoformat(),
        "total_turnover": float(total_turnover),
        "annualized_turnover": float(total_turnover * 252.0 / max(1, len(series))),
        "total_cost_fraction": float(total_cost_fraction),
        "instrument_contributions": dict(sorted(contributions.items())),
        "forced_missing_open_loss_count": forced_missing,
    }


def _factor_targets(
    panel: pd.DataFrame,
    *,
    factor_id: str,
    signal_to_execution: Mapping[pd.Timestamp, pd.Timestamp],
    portfolio_size: int,
) -> tuple[dict[pd.Timestamp, dict[str, float]], list[int]]:
    targets: dict[pd.Timestamp, dict[str, float]] = {}
    coverage: list[int] = []
    for signal, cross_section in panel.groupby(level="datetime", sort=True):
        signal = pd.Timestamp(signal)
        if signal not in signal_to_execution:
            continue
        usable = cross_section.loc[
            cross_section["eligible"]
            & cross_section[factor_id].notna()
            & cross_section["issuer_cik"].astype(str).ne(""),
            [factor_id, "issuer_cik"],
        ].copy()
        usable["_instrument"] = usable.index.get_level_values("instrument").astype(str)
        usable["_issuer_cik"] = usable["issuer_cik"].astype(str)
        usable = usable.sort_values(
            [factor_id, "_instrument"],
            ascending=[False, True],
            kind="mergesort",
        )
        issuer_unique = usable.drop_duplicates("_issuer_cik", keep="first")
        names = issuer_unique.head(portfolio_size)["_instrument"].to_list()
        targets[signal] = {name: 1.0 / portfolio_size for name in names}
        coverage.append(len(issuer_unique))
    return targets, coverage


def _simulate_buy_and_hold(
    spy: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    cost_bps: float,
) -> pd.Series:
    visible = spy.loc[(spy.index >= start_date) & (spy.index <= end_date)]
    if visible.empty:
        return pd.Series(dtype="float64")
    returns = visible["close"].pct_change(fill_method=None)
    returns.iloc[0] = (1.0 - cost_bps / 10_000.0) * float(visible.iloc[0]["close"]) / float(visible.iloc[0]["open"]) - 1.0
    return returns.astype("float64")


def _performance_metrics(daily_returns: pd.Series) -> dict[str, object]:
    values = daily_returns.astype("float64").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {
            "net_total_return": -1.0,
            "net_cagr": -1.0,
            "max_drawdown": -1.0,
            "sharpe": -999.0,
            "sortino": -999.0,
            "calmar": -999.0,
            "calendar_year_returns": {},
            "worst_calendar_year_return": -1.0,
            "worst_rolling_12_month_return": -1.0,
        }
    equity = (1.0 + values).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    cagr = -1.0 if equity.iloc[-1] <= 0.0 else float(equity.iloc[-1] ** (252.0 / len(values)) - 1.0)
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    std = float(values.std(ddof=0))
    sharpe = float(values.mean() / std * math.sqrt(252.0)) if std > 0.0 else (999.0 if values.mean() > 0.0 else 0.0)
    downside = values.loc[values < 0.0]
    downside_std = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = float(values.mean() / downside_std * math.sqrt(252.0)) if downside_std > 0.0 else (999.0 if values.mean() > 0.0 else 0.0)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0.0 else (999.0 if cagr > 0.0 else 0.0)
    calendar = {
        str(year): _compound(group)
        for year, group in values.groupby(values.index.year, sort=True)
    }
    rolling = (1.0 + values).rolling(252).apply(np.prod, raw=True) - 1.0
    worst_rolling = float(rolling.dropna().min()) if rolling.notna().any() else total
    return {
        "net_total_return": total,
        "net_cagr": float(cagr),
        "max_drawdown": max_drawdown,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "calendar_year_returns": calendar,
        "worst_calendar_year_return": min(calendar.values()) if calendar else total,
        "worst_rolling_12_month_return": worst_rolling,
    }


def _continuation_failure_taxonomy(
    metrics: Mapping[str, object],
    *,
    minimum_cross_section: int,
    minimum_weeks: int,
) -> list[str]:
    failures: list[str] = []
    checks = (
        (float(metrics["median_weekly_coverage"]) < minimum_cross_section, "INSUFFICIENT_FUNDAMENTAL_COVERAGE"),
        (int(metrics["weekly_observation_count"]) < minimum_weeks, "INSUFFICIENT_WEEKLY_OBSERVATIONS"),
        (float(metrics["base_net_cagr"]) < 0.10, "NET_CAGR_BELOW_TARGET"),
        (float(metrics["stress_net_cagr"]) < 0.08, "STRESS_CAGR_BELOW_TARGET"),
        (float(metrics["base_max_drawdown"]) < -0.25, "DRAWDOWN_ABOVE_CONTINUATION_LIMIT"),
        (float(metrics["base_sharpe"]) < 0.75, "SHARPE_BELOW_CONTINUATION_LIMIT"),
        (float(metrics["base_calmar"]) < 0.50, "CALMAR_BELOW_CONTINUATION_LIMIT"),
        (int(metrics["positive_calendar_year_count"]) < 3, "CALENDAR_YEAR_INSTABILITY"),
        (float(metrics["stress_active_cagr_vs_universe"]) <= 0.0, "NO_STRESS_ACTIVE_EDGE"),
        (float(metrics["best_year_removed_cumulative_return"]) <= 0.0, "BEST_YEAR_DEPENDENCE"),
        (float(metrics["maximum_positive_instrument_contribution_share"]) > 0.25, "SINGLE_INSTRUMENT_DOMINANCE"),
        (float(metrics["maximum_positive_year_return_share"]) > 0.60, "SINGLE_YEAR_DOMINANCE"),
    )
    for failed, label in checks:
        if failed:
            failures.append(label)
    return failures


def _best_year_removed_return(daily_returns: pd.Series, calendar: Mapping[str, float]) -> float:
    if not calendar:
        return -1.0
    best_year = int(max(calendar, key=calendar.get))
    remaining = daily_returns.loc[daily_returns.index.year != best_year]
    return _compound(remaining) if len(remaining) else 0.0


def _positive_concentration(values: Mapping[str, float]) -> float:
    positive = [float(value) for value in values.values() if float(value) > 0.0]
    return max(positive) / sum(positive) if positive else 1.0


def _compound(values: Any) -> float:
    array = np.asarray(list(values), dtype="float64")
    return float(np.prod(1.0 + array) - 1.0) if len(array) else 0.0


def _price_row(frame: pd.DataFrame, timestamp: pd.Timestamp, instrument: str) -> pd.Series | None:
    try:
        row = frame.loc[(timestamp, instrument)]
    except KeyError:
        return None
    return row if isinstance(row, pd.Series) else None


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float, np.number)) and math.isfinite(float(value)) and float(value) > 0.0


def _empty_simulation() -> dict[str, object]:
    return {
        "daily_returns": pd.Series(dtype="float64"),
        "first_execution_date": None,
        "total_turnover": 0.0,
        "annualized_turnover": 0.0,
        "total_cost_fraction": 0.0,
        "instrument_contributions": {},
        "forced_missing_open_loss_count": 0,
    }


def _validate_catalog(raw: Any) -> tuple[tuple[str, ...], str]:
    if not isinstance(raw, dict):
        raise ValueError("factor metadata is invalid.")
    declared = raw.get("canonical_catalog_sha256")
    if declared != _canonical_sha({key: value for key, value in raw.items() if key != "canonical_catalog_sha256"}):
        raise ValueError("factor catalog hash mismatch.")
    ordered = raw.get("ordered_factor_ids")
    if raw.get("version") != "massive_fundamental_catalog_v1" or ordered != list(FACTOR_DEFINITIONS_V1) or raw.get("factor_count") != 10:
        raise ValueError("factor catalog identity is invalid.")
    return tuple(ordered), str(declared)


def _validate_panel(frame: Any, factor_ids: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != _PANEL_COLUMNS or set(factor_ids) != set(FACTOR_DEFINITIONS_V1):
        raise ValueError("factor panel columns are invalid.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"] or frame.index.has_duplicates:
        raise ValueError("factor panel index is invalid.")
    output = frame.sort_index(kind="mergesort").copy()
    if output.index.get_level_values("datetime").max() >= pd.Timestamp("2023-01-01"):
        raise ValueError("sealed OOS row exposed.")
    return output


def _validate_prices(frame: Any) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != _PRICE_COLUMNS:
        raise ValueError("price frame columns are invalid.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"] or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("price frame index is invalid.")
    if frame.index.get_level_values("datetime").max() >= pd.Timestamp("2023-01-01"):
        raise ValueError("sealed OOS price exposed.")
    return frame


def _validate_spy(frame: Any) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != {"open", "close"}:
        raise ValueError("SPY price columns are invalid.")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("SPY price index is invalid.")
    values = frame[["open", "close"]].to_numpy(dtype="float64")
    if not np.isfinite(values).all() or (values <= 0.0).any() or frame.index.max() >= pd.Timestamp("2023-01-01"):
        raise ValueError("SPY prices are invalid.")
    return frame


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
