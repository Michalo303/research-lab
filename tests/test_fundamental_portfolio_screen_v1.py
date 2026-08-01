from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_lab.research.fundamental_portfolio_screen_v1 import (
    _continuation_failure_taxonomy,
    _factor_targets,
    _run_fundamental_portfolio_screen_v1,
    _simulate_weekly_targets,
)
from research_lab.research.massive_fundamental_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_massive_fundamental_catalog_metadata_v1,
)


def _prices(periods: int = 180, instruments: tuple[str, ...] = ("AAA", "BBB", "CCC")) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-02", periods=periods)
    rows = []
    daily_growth = {"AAA": 1.002, "BBB": 1.001, "CCC": 0.999}
    for instrument in instruments:
        previous = 100.0
        for timestamp in dates:
            opening = previous
            closing = opening * daily_growth.get(instrument, 1.0)
            rows.append(
                {
                    "datetime": timestamp,
                    "instrument": instrument,
                    "open": opening,
                    "high": max(opening, closing) * 1.001,
                    "low": min(opening, closing) * 0.999,
                    "close": closing,
                    "volume": 1_000_000.0,
                    "raw_close": closing,
                    "dollar_volume": closing * 1_000_000.0,
                    "eligible": True,
                }
            )
            previous = closing
    return pd.DataFrame(rows).set_index(["datetime", "instrument"]).sort_index()


def _panel(prices: pd.DataFrame) -> pd.DataFrame:
    sessions = prices.index.get_level_values("datetime").unique()
    signals = pd.Series(sessions, index=sessions).groupby(sessions.to_period("W-FRI")).max().to_list()
    panel = prices.loc[prices.index.get_level_values("datetime").isin(signals), ["open", "close", "raw_close", "eligible"]].copy()
    score = {"AAA": 3.0, "AAB": 2.5, "BBB": 2.0, "CCC": 1.0}
    for factor_id in FACTOR_DEFINITIONS_V1:
        panel[factor_id] = [score[instrument] for instrument in panel.index.get_level_values("instrument")]
    panel["MOM_12_1"] = panel["GROSS_PROFITABILITY"]
    panel["issuer_cik"] = [f"CIK-{instrument}" for instrument in panel.index.get_level_values("instrument")]
    return panel[[*FACTOR_DEFINITIONS_V1, "MOM_12_1", "issuer_cik", "open", "close", "raw_close", "eligible"]]


def _spy(prices: pd.DataFrame) -> pd.DataFrame:
    sessions = prices.index.get_level_values("datetime").unique()
    values = np.full(len(sessions), 100.0)
    return pd.DataFrame({"open": values, "close": values}, index=sessions)


def test_simulator_executes_next_session_and_charges_one_way_turnover() -> None:
    prices = _prices(periods=6, instruments=("AAA", "BBB"))
    sessions = prices.index.get_level_values("datetime").unique()
    signal = sessions[0]
    execution = sessions[1]
    targets = {signal: {"AAA": 0.5, "BBB": 0.5}}

    result = _simulate_weekly_targets(
        targets,
        prices,
        signal_to_execution={signal: execution},
        cost_bps=100.0,
        end_date=sessions[-1],
    )

    assert result["first_execution_date"] == execution.date().isoformat()
    assert result["total_turnover"] == pytest.approx(1.0)
    assert result["total_cost_fraction"] == pytest.approx(0.01)
    assert result["daily_returns"].index.min() == execution
    assert result["daily_returns"].iloc[0] > -0.01


def test_screen_returns_direct_economics_for_all_ten_frozen_factors() -> None:
    prices = _prices()
    panel = _panel(prices)

    result = _run_fundamental_portfolio_screen_v1(
        panel,
        prices,
        _spy(prices),
        build_massive_fundamental_catalog_metadata_v1(),
        minimum_cross_section=2,
        minimum_weeks=20,
        portfolio_size=2,
    )

    assert result["status"] == "COMPLETED"
    assert result["ordered_factor_ids"] == list(FACTOR_DEFINITIONS_V1)
    assert result["factor_count"] == 10
    assert result["costs"] == {
        "base_bps_one_way": 15.0,
        "stress_bps_one_way": 30.0,
        "severe_bps_one_way": 50.0,
    }
    assert result["eligible_universe_baseline"]["stress_net_cagr"] < result["factors"]["GROSS_PROFITABILITY"]["stress_net_cagr"]
    assert result["spy_baseline"]["base_net_cagr"] <= 0.0
    factor = result["factors"]["GROSS_PROFITABILITY"]
    assert factor["base_net_cagr"] > factor["stress_net_cagr"] > factor["severe_net_cagr"]
    assert factor["base_max_drawdown"] <= 0.0
    assert factor["median_weekly_coverage"] == 3.0
    assert factor["average_holdings"] == pytest.approx(2.0)
    assert factor["average_exposure"] == pytest.approx(1.0)
    assert result["promotion_authorized"] is False
    assert result["sealed_oos_opened"] is False
    assert len(result["canonical_screen_sha256"]) == 64


def test_signal_session_price_mutation_does_not_change_next_open_execution_return() -> None:
    prices = _prices()
    panel = _panel(prices)
    changed = prices.copy()
    first_signal = panel.index.get_level_values("datetime").min()
    changed.loc[(first_signal, "AAA"), ["open", "high", "low", "close", "raw_close"]] *= 10.0

    baseline = _run_fundamental_portfolio_screen_v1(
        panel, prices, _spy(prices), build_massive_fundamental_catalog_metadata_v1(),
        minimum_cross_section=2, minimum_weeks=20, portfolio_size=2,
    )
    mutated = _run_fundamental_portfolio_screen_v1(
        panel, changed, _spy(prices), build_massive_fundamental_catalog_metadata_v1(),
        minimum_cross_section=2, minimum_weeks=20, portfolio_size=2,
    )

    assert baseline["factors"]["GROSS_PROFITABILITY"]["base_net_cagr"] == pytest.approx(
        mutated["factors"]["GROSS_PROFITABILITY"]["base_net_cagr"]
    )


def test_continuation_gate_reports_each_economic_veto() -> None:
    passing = {
        "median_weekly_coverage": 700.0,
        "weekly_observation_count": 200,
        "base_net_cagr": 0.15,
        "stress_net_cagr": 0.12,
        "base_max_drawdown": -0.14,
        "base_sharpe": 1.0,
        "base_calmar": 1.0,
        "positive_calendar_year_count": 4,
        "stress_active_cagr_vs_universe": 0.03,
        "best_year_removed_cumulative_return": 0.10,
        "maximum_positive_instrument_contribution_share": 0.20,
        "maximum_positive_year_return_share": 0.50,
    }
    assert _continuation_failure_taxonomy(passing, minimum_cross_section=500, minimum_weeks=156) == []
    expected = {
        "INSUFFICIENT_FUNDAMENTAL_COVERAGE",
        "INSUFFICIENT_WEEKLY_OBSERVATIONS",
        "NET_CAGR_BELOW_TARGET",
        "STRESS_CAGR_BELOW_TARGET",
        "DRAWDOWN_ABOVE_CONTINUATION_LIMIT",
        "SHARPE_BELOW_CONTINUATION_LIMIT",
        "CALMAR_BELOW_CONTINUATION_LIMIT",
        "CALENDAR_YEAR_INSTABILITY",
        "NO_STRESS_ACTIVE_EDGE",
        "BEST_YEAR_DEPENDENCE",
        "SINGLE_INSTRUMENT_DOMINANCE",
        "SINGLE_YEAR_DOMINANCE",
    }
    failing = dict(passing)
    failing.update(
        {
            "median_weekly_coverage": 499.0,
            "weekly_observation_count": 155,
            "base_net_cagr": 0.099,
            "stress_net_cagr": 0.079,
            "base_max_drawdown": -0.251,
            "base_sharpe": 0.749,
            "base_calmar": 0.499,
            "positive_calendar_year_count": 2,
            "stress_active_cagr_vs_universe": 0.0,
            "best_year_removed_cumulative_return": 0.0,
            "maximum_positive_instrument_contribution_share": 0.251,
            "maximum_positive_year_return_share": 0.601,
        }
    )
    assert set(_continuation_failure_taxonomy(failing, minimum_cross_section=500, minimum_weeks=156)) == expected


def test_ranking_keeps_at_most_one_share_class_per_issuer() -> None:
    prices = _prices(instruments=("AAA", "AAB", "CCC"))
    panel = _panel(prices)
    panel.loc[(slice(None), "AAB"), "issuer_cik"] = "CIK-AAA"
    signals = panel.index.get_level_values("datetime").unique()
    sessions = prices.index.get_level_values("datetime").unique()
    mapping = {
        signal: sessions[sessions.searchsorted(signal, side="right")]
        for signal in signals
        if sessions.searchsorted(signal, side="right") < len(sessions)
    }

    targets, coverage = _factor_targets(
        panel,
        factor_id="GROSS_PROFITABILITY",
        signal_to_execution=mapping,
        portfolio_size=2,
    )

    assert coverage and min(coverage) == 2
    assert all(set(target) == {"AAA", "CCC"} for target in targets.values())
