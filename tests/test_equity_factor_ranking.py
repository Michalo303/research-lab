import numpy as np
import pandas as pd
import pytest

from research_lab.equity_factor_ranking import (
    OHLCV_RANKING_PROFILES,
    calculate_price_factors,
    combine_factor_groups,
    construct_equal_weight_portfolio,
    missing_fundamentals_diagnostics,
    percentile_rank,
    score_factor_groups,
    select_top_n,
    simple_market_regime_signal,
    validate_ohlcv_panel,
)


def _panel(days: int = 320, symbols: tuple[str, ...] = ("AAA", "BBB", "CCC")) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    frames = {}
    for i, symbol in enumerate(symbols):
        slope = 0.0008 * (i + 1)
        wave = 0.002 * np.sin(np.linspace(0, 8 * np.pi, days) + i)
        close = 50.0 * (1.0 + slope + wave).cumprod()
        frames[symbol] = pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": np.full(days, 1_000_000 * (i + 1), dtype=float),
            },
            index=dates,
        )
    return pd.concat(frames, axis=1)


def test_ohlcv_validation_accepts_valid_panels():
    result = validate_ohlcv_panel(_panel())

    assert result["valid"] is True
    assert result["symbols"] == ["AAA", "BBB", "CCC"]


def test_ohlcv_validation_rejects_missing_close():
    panel = _panel().drop(columns=[("AAA", "close")])

    with pytest.raises(ValueError, match="missing required OHLCV fields"):
        validate_ohlcv_panel(panel)


def test_momentum_factors_are_deterministic():
    panel = _panel()

    first = calculate_price_factors(panel)["momentum_6m"]
    second = calculate_price_factors(panel)["momentum_6m"]

    pd.testing.assert_frame_equal(first, second)
    assert first.iloc[-1]["CCC"] > first.iloc[-1]["AAA"]


def test_volatility_safety_factors_are_deterministic():
    panel = _panel()

    first = calculate_price_factors(panel)["realized_volatility_63d"]
    second = calculate_price_factors(panel)["realized_volatility_63d"]

    pd.testing.assert_frame_equal(first, second)
    assert first.iloc[-1].notna().all()


def test_percentile_ranking_higher_is_better():
    scores = percentile_rank(pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}), higher_is_better=True)

    assert scores["C"] == pytest.approx(1.0)
    assert scores["A"] < scores["B"] < scores["C"]


def test_percentile_ranking_lower_is_better():
    scores = percentile_rank(pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}), higher_is_better=False)

    assert scores["A"] == pytest.approx(1.0)
    assert scores["A"] > scores["B"] > scores["C"]


def test_missing_values_do_not_crash_scoring():
    panel = _panel()
    panel.loc[panel.index[-5:], ("BBB", "close")] = np.nan

    factors = calculate_price_factors(panel)
    group_scores = score_factor_groups(factors)
    composite = combine_factor_groups(group_scores, OHLCV_RANKING_PROFILES["OHLCV_COMPOSITE_RANKING"])

    assert "BBB" in composite.columns
    assert composite["AAA"].notna().any()


def test_missing_fundamental_groups_are_reported_as_skipped_diagnostics():
    diagnostics = missing_fundamentals_diagnostics()

    assert diagnostics["fundamental_data_available"] is False
    assert diagnostics["skipped_factor_groups"] == ["growth", "quality", "valuation", "capital_allocation"]
    assert diagnostics["active_factor_groups"] == ["momentum", "volatility", "liquidity", "trend"]


def test_composite_score_uses_only_active_available_factor_groups():
    dates = pd.bdate_range("2026-01-02", periods=2)
    group_scores = {
        "momentum": pd.DataFrame({"A": [0.2, 0.8], "B": [0.8, 0.2]}, index=dates),
        "trend": pd.DataFrame({"A": [1.0, 1.0], "B": [0.0, 0.0]}, index=dates),
    }
    profile = {"momentum": 0.30, "trend": 0.20, "quality": 0.50}

    composite = combine_factor_groups(group_scores, profile)

    expected = (group_scores["momentum"] * 0.60) + (group_scores["trend"] * 0.40)
    pd.testing.assert_frame_equal(composite, expected)


def test_top_n_selection_returns_expected_symbols_in_toy_dataset():
    scores = pd.DataFrame(
        {"AAA": [0.8], "BBB": [0.7], "CCC": [0.9]},
        index=pd.to_datetime(["2026-01-02"]),
    )

    selected = select_top_n(scores, "2026-01-02", top_n=2)

    assert selected.index.tolist() == ["CCC", "AAA"]


def test_equal_weights_sum_to_target_exposure():
    scores = pd.DataFrame(
        {"AAA": [0.8], "BBB": [0.7], "CCC": [0.9]},
        index=pd.to_datetime(["2026-01-02"]),
    )

    result = construct_equal_weight_portfolio(
        scores,
        "2026-01-02",
        top_n=3,
        min_assets=3,
        target_exposure=0.9,
        max_position_weight=0.4,
    )

    assert result["status"] == "ok"
    assert result["weights"].sum() == pytest.approx(0.9)
    assert set(result["weights"].index) == {"AAA", "BBB", "CCC"}


def test_max_position_cap_is_respected():
    scores = pd.DataFrame(
        {"AAA": [0.8], "BBB": [0.7], "CCC": [0.9]},
        index=pd.to_datetime(["2026-01-02"]),
    )

    result = construct_equal_weight_portfolio(scores, "2026-01-02", top_n=3, min_assets=3, max_position_weight=0.2)

    assert result["status"] == "ok"
    assert result["weights"].max() <= 0.2
    assert result["weights"].sum() == pytest.approx(0.6)


def test_min_assets_failure_returns_diagnostic_rejection():
    scores = pd.DataFrame(
        {"AAA": [0.8], "BBB": [0.7]},
        index=pd.to_datetime(["2026-01-02"]),
    )

    result = construct_equal_weight_portfolio(scores, "2026-01-02", top_n=2, min_assets=3)

    assert result["status"] == "rejected_min_assets"
    assert result["weights"].empty
    assert result["diagnostics"]["eligible_assets"] == 2


def test_market_regime_helper_reduces_exposure_in_risk_off_toy_data():
    dates = pd.bdate_range("2025-01-02", periods=260)
    benchmark = pd.Series(np.r_[np.linspace(100, 130, 220), np.linspace(90, 80, 40)], index=dates)

    regime = simple_market_regime_signal(benchmark, window=20, risk_off_exposure=0.3)

    assert regime.iloc[-1]["risk_on"] is False
    assert regime.iloc[-1]["target_exposure"] == pytest.approx(0.3)


def test_rolling_indicators_do_not_use_future_bars():
    panel = _panel(days=260, symbols=("AAA", "BBB"))
    signal_date = panel.index[-5]
    changed_future = panel.copy()
    changed_future.loc[changed_future.index[-4:], ("AAA", "close")] = 10_000.0

    original = calculate_price_factors(panel)["momentum_3m"].loc[signal_date]
    mutated = calculate_price_factors(changed_future)["momentum_3m"].loc[signal_date]

    pd.testing.assert_series_equal(original, mutated)


def test_trend_indicators_wait_for_enough_history():
    panel = _panel(days=120, symbols=("AAA", "BBB"))

    factor = calculate_price_factors(panel)["close_above_200dma"]

    assert factor.isna().all().all()
