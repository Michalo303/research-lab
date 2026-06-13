import json

from research_lab.runner import _persist_hypothesis_result
from research_lab.strategies.baselines import queued_daily_symbols, queued_hypothesis_strategies


def _hermes_record(**overrides):
    item = {
        "hypothesis_id": "HERMES_RUN_001",
        "title": "Conservative capped trend",
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "builder": "long_term_vol_target_cap",
        "rationale": "Reduce drawdown.",
        "parameters": {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.65},
        "risk_controls": {
            "volatility_targeting": "target portfolio volatility",
            "drawdown_circuit_breakers": "move to cash after drawdown threshold",
            "cash_defensive_regimes": "hold cash in risk-off regimes",
            "exposure_caps": "max 65%",
            "correlation_aware_portfolio_risk": "avoid correlated sleeves",
            "crisis_period_diagnostics": "test crisis windows",
            "cost_slippage_stress": "double cost stress",
            "parameter_neighborhood_stability": "test adjacent parameters",
        },
        "source_title": "hermes",
        "source_key": "hermes:fingerprint",
        "llm_generated": True,
        "hermes_run_id": "run-1",
        "hermes_provider": "command",
        "used_note_ids": ["note-1111111111111111"],
    }
    item.update(overrides)
    return item


def _write_queue(root, items):
    path = root / "registry" / "hypothesis_queue.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")


def test_maps_validated_hermes_record_to_exact_whitelisted_builder(tmp_path):
    _write_queue(tmp_path, [_hermes_record()])

    [spec] = queued_hypothesis_strategies(tmp_path, limit=4)

    assert spec.builder == "long_term_vol_target_cap"
    assert spec.family == "LONGTERM"
    assert spec.parameters["max_weight"] == 0.65
    assert spec.parameters["source_hypothesis_id"] == "HERMES_RUN_001"
    assert spec.parameters["source_hermes_run_id"] == "run-1"
    assert spec.parameters["source_hermes_provider"] == "command"
    assert spec.parameters["source_used_note_ids"] == ["note-1111111111111111"]


def test_skips_tampered_hermes_record_before_daily_execution(tmp_path):
    _write_queue(tmp_path, [_hermes_record(builder="arbitrary_python")])

    assert queued_hypothesis_strategies(tmp_path, limit=4) == []


def test_legacy_queue_mapping_remains_available(tmp_path):
    _write_queue(
        tmp_path,
        [{"hypothesis_id": "legacy", "family": "SWING", "ticker": "QQQ", "title": "Legacy pullback"}],
    )

    [spec] = queued_hypothesis_strategies(tmp_path, limit=4)

    assert spec.builder == "swing_trend_filtered_pullback"
    assert spec.parameters["symbol"] == "QQQ"


def test_hypothesis_result_preserves_hermes_provenance(tmp_path):
    result = {
        "strategy_id": "LONGTERM_ETF_1D_HERMES_TEST_20260612_001",
        "tier": "Rejected",
        "tier_reason": "Unseen max drawdown exceeds 15%.",
        "family": "LONGTERM",
        "parameters": {
            "source_hypothesis_id": "HERMES_RUN_001",
            "source_hermes_run_id": "run-1",
            "source_hermes_provider": "command",
            "source_used_note_ids": ["note-1111111111111111"],
        },
        "split_metrics": {"unseen": {"cagr": 0.01, "max_drawdown": -0.20}},
    }

    _persist_hypothesis_result(tmp_path, result)

    row = json.loads((tmp_path / "registry" / "hypothesis_results.jsonl").read_text().splitlines()[0])
    assert row["hermes_run_id"] == "run-1"
    assert row["hermes_provider"] == "command"
    assert row["used_note_ids"] == ["note-1111111111111111"]


def test_daily_symbol_discovery_skips_intraday_hermes_symbols_and_honors_limit(tmp_path):
    records = [
        _hermes_record(
            hypothesis_id="intraday",
            family="INTRADAY",
            asset_class="CRYPTO",
            timeframe="15M",
            builder="intraday_vwap_rsi_reclaim",
            parameters={"symbol": "BTCUSDT", "rsi_washout": 30, "rsi_reclaim": 45},
        ),
        _hermes_record(
            hypothesis_id="rotation",
            family="ROTATION",
            builder="active_momentum_rotation",
            parameters={"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
        ),
    ]
    _write_queue(tmp_path, records)

    symbols = queued_daily_symbols(tmp_path, limit=3)

    assert "BTCUSDT" not in symbols
    assert len(symbols) == 3
