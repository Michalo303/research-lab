import math

import pytest

from research_lab.hermes.schema import BUILDER_SCHEMAS, schema_prompt_text, validate_hypothesis


def _risk_controls():
    return {
        "volatility_targeting": "target portfolio volatility",
        "drawdown_circuit_breakers": "move to cash after drawdown threshold",
        "cash_defensive_regimes": "hold cash in risk-off regimes",
        "exposure_caps": "cap gross and single-asset exposure",
        "correlation_aware_portfolio_risk": "avoid correlated sleeves",
        "crisis_period_diagnostics": "test 2008 and 2020",
        "cost_slippage_stress": "double cost stress",
        "parameter_neighborhood_stability": "test adjacent values",
    }


def _valid(**overrides):
    item = {
        "title": "Conservative trend cap",
        "family": "LONGTERM",
        "builder": "long_term_vol_target_cap",
        "rationale": "Reduce drawdown before seeking return.",
        "parameters": {
            "symbol": "spy",
            "sma": 200,
            "vol_window": 63,
            "target_vol": 0.08,
            "max_weight": 0.65,
        },
        "risk_controls": _risk_controls(),
        "tags": ["trend", "risk-first"],
    }
    item.update(overrides)
    return item


def test_validates_and_normalizes_whitelisted_builder():
    result = validate_hypothesis(_valid())

    assert result.accepted is True
    assert result.reasons == []
    assert result.hypothesis["parameters"]["symbol"] == "SPY"
    assert result.hypothesis["parameters"]["max_weight"] == 0.65


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"builder": "provider_supplied_python"}, "builder_not_allowed"),
        ({"family": "ROTATION"}, "family_builder_mismatch"),
        ({"code": "print('no')"}, "unknown_field:code"),
        ({"risk_controls": {"exposure_caps": {"code": "no"}}}, "invalid_risk_control:exposure_caps"),
        ({"parameters": {"symbol": "SPY"}}, "missing_parameter:sma"),
        (
            {
                "parameters": {
                    "symbol": "SPY",
                    "sma": 200,
                    "vol_window": 63,
                    "target_vol": 0.08,
                    "max_weight": 0.65,
                    "python": "print('no')",
                }
            },
            "unknown_parameter:python",
        ),
        (
            {
                "parameters": {
                    "symbol": "DOGE",
                    "sma": 200,
                    "vol_window": 63,
                    "target_vol": 0.08,
                    "max_weight": 0.65,
                }
            },
            "invalid_parameter:symbol",
        ),
        (
            {
                "parameters": {
                    "symbol": "SPY",
                    "sma": 200,
                    "vol_window": 63,
                    "target_vol": math.inf,
                    "max_weight": 0.65,
                }
            },
            "invalid_parameter:target_vol",
        ),
        (
            {
                "parameters": {
                    "symbol": "SPY",
                    "sma": 200,
                    "vol_window": 63,
                    "target_vol": 0.08,
                    "max_weight": 1.25,
                }
            },
            "invalid_parameter:max_weight",
        ),
    ],
)
def test_rejects_untrusted_or_invalid_hypotheses(overrides, reason):
    result = validate_hypothesis(_valid(**overrides))

    assert result.accepted is False
    assert reason in result.reasons


def test_rotation_requires_strong_explicit_risk_overlay():
    item = {
        "title": "Generic rotation",
        "family": "ROTATION",
        "builder": "active_momentum_rotation",
        "rationale": "Rank assets.",
        "parameters": {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
        "risk_controls": {"exposure_caps": "none"},
    }

    result = validate_hypothesis(item)

    assert result.accepted is False
    assert "rotation_risk_overlay_required" in result.reasons


def test_schema_prompt_lists_only_existing_builders():
    prompt = schema_prompt_text()

    assert "long_term_vol_target_cap" in prompt
    assert "intraday_vwap_rsi_reclaim" in prompt
    assert "provider_supplied_python" not in prompt
    assert len(BUILDER_SCHEMAS) == 11


def test_all_whitelisted_builders_accept_compatible_parameter_shapes():
    examples = {
        "long_term_trend_filter": ("LONGTERM", {"symbol": "SPY", "sma": 200}),
        "long_term_vol_target": ("LONGTERM", {"symbol": "SPY", "sma": 150, "vol_window": 63, "target_vol": 0.10}),
        "long_term_strict_cash_filter": ("LONGTERM", {"symbol": "SPY", "sma": 200, "confirmation_sma": 50}),
        "long_term_vol_target_cap": (
            "LONGTERM",
            {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.65},
        ),
        "active_momentum_rotation": ("ROTATION", {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2}),
        "rotation_momentum_drawdown_filter": (
            "ROTATION",
            {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2, "risk_symbol": "SPY", "risk_sma": 200},
        ),
        "rotation_momentum_circuit_breaker": (
            "ROTATION",
            {
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "lookback": 126,
                "top_n": 2,
                "risk_symbol": "SPY",
                "drawdown_threshold": -0.12,
                "recovery_sma": 200,
            },
        ),
        "defensive_asset_rotation": (
            "ROTATION",
            {
                "risk_assets": ["SPY", "QQQ"],
                "defensive_assets": ["TLT", "GLD"],
                "lookback": 126,
                "top_n": 1,
                "risk_symbol": "SPY",
                "risk_sma": 200,
            },
        ),
        "swing_rsi_pullback": ("SWING", {"symbol": "QQQ", "trend_sma": 150, "rsi_entry": 35, "rsi_exit": 58}),
        "swing_trend_filtered_pullback": (
            "SWING",
            {"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40, "rsi_exit": 58, "atr_stop": 2.0, "max_exposure": 0.5},
        ),
        "intraday_vwap_rsi_reclaim": ("INTRADAY", {"symbol": "BTCUSDT", "rsi_washout": 30, "rsi_reclaim": 45}),
    }

    for builder, (family, parameters) in examples.items():
        result = validate_hypothesis(
            {
                "title": builder,
                "family": family,
                "builder": builder,
                "rationale": "schema compatibility test",
                "parameters": parameters,
                "risk_controls": _risk_controls(),
            }
        )
        assert result.accepted, (builder, result.reasons)
