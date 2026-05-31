import pandas as pd

from research_lab.strategies.baselines import StrategySpec, baseline_strategies, build_weights


def _panel(prices: dict[str, list[float]]) -> pd.DataFrame:
    index = pd.bdate_range("2026-01-01", periods=len(next(iter(prices.values()))))
    frames = {}
    for symbol, close in prices.items():
        series = pd.Series(close, index=index, dtype=float)
        frames[symbol] = pd.DataFrame(
            {
                "open": series,
                "high": series * 1.01,
                "low": series * 0.99,
                "close": series,
                "volume": 1000,
            },
            index=index,
        )
    return pd.concat(frames, axis=1).sort_index()


def _spec(short_name: str, builder: str, parameters: dict) -> StrategySpec:
    return StrategySpec(
        family="LONGTERM" if short_name.startswith("TREND") else "ROTATION",
        asset_class="ETF",
        timeframe="1D",
        short_name=short_name,
        hypothesis="test",
        parameters=parameters,
        rules="test",
        builder=builder,
    )


def test_baseline_strategies_register_additive_etf_risk_variants_after_originals():
    specs = baseline_strategies()

    assert [spec.short_name for spec in specs[:4]] == [
        "TREND_FILTER",
        "DUAL_MOMENTUM",
        "RSI_PULLBACK",
        "VWAP_RSI_RECLAIM",
    ]
    assert {
        "TREND_STRICT_CASH",
        "TREND_VOL_CAP",
        "DUAL_MOMENTUM_DD_CB",
        "DEFENSIVE_ROTATION",
    }.issubset({spec.short_name for spec in specs})


def test_strict_cash_filter_requires_fast_trend_confirmation():
    panel = _panel({"SPY": [100, 100, 100, 70, 90, 100]})
    spec = _spec(
        "TREND_STRICT_CASH",
        "long_term_strict_cash_filter",
        {"symbol": "SPY", "sma": 3, "confirmation_sma": 2},
    )

    weights = build_weights(spec, panel)

    assert weights["SPY"].iloc[4] == 0.0
    assert weights["SPY"].iloc[5] == 1.0


def test_volatility_target_cap_never_exceeds_fixed_max_weight():
    panel = _panel({"SPY": [100, 101, 98, 104, 99, 108, 102, 112]})
    spec = _spec(
        "TREND_VOL_CAP",
        "long_term_vol_target_cap",
        {"symbol": "SPY", "sma": 2, "vol_window": 2, "target_vol": 0.20, "max_weight": 0.60},
    )

    weights = build_weights(spec, panel)

    assert float(weights["SPY"].max()) <= 0.60 + 1e-12
    assert weights["SPY"].iloc[-1] > 0.0


def test_rotation_circuit_breaker_moves_fully_to_cash_after_equity_drawdown_breach():
    panel = _panel(
        {
            "SPY": [100, 105, 95, 84, 83],
            "QQQ": [100, 104, 96, 86, 85],
            "TLT": [100, 101, 102, 103, 104],
            "GLD": [100, 99, 98, 97, 96],
        }
    )
    spec = _spec(
        "DUAL_MOMENTUM_DD_CB",
        "rotation_momentum_circuit_breaker",
        {
            "symbols": ["SPY", "QQQ", "TLT", "GLD"],
            "lookback": 1,
            "top_n": 1,
            "risk_symbol": "SPY",
            "drawdown_threshold": -0.10,
            "recovery_sma": 2,
        },
    )

    weights = build_weights(spec, panel)

    assert weights.iloc[-1].sum() == 0.0


def test_defensive_rotation_uses_tlt_or_gld_when_equity_risk_filter_is_off():
    panel = _panel(
        {
            "SPY": [100, 98, 96, 94],
            "QQQ": [100, 97, 95, 93],
            "TLT": [100, 101, 103, 106],
            "GLD": [100, 99, 98, 97],
        }
    )
    spec = _spec(
        "DEFENSIVE_ROTATION",
        "defensive_asset_rotation",
        {
            "risk_assets": ["SPY", "QQQ"],
            "defensive_assets": ["TLT", "GLD"],
            "lookback": 1,
            "top_n": 1,
            "risk_symbol": "SPY",
            "risk_sma": 2,
        },
    )

    weights = build_weights(spec, panel)

    assert weights["TLT"].iloc[-1] == 1.0
    assert weights["SPY"].iloc[-1] == 0.0
    assert weights["QQQ"].iloc[-1] == 0.0
