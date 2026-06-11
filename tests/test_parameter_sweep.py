import pandas as pd
import pytest

from research_lab.config import LabConfig
from research_lab.data import DataBundle
from research_lab import parameter_sweep, runner
from research_lab.parameter_sweep import PARAMETER_SWEEP_COLUMNS, _parameter_variants, _row, _select_representatives, _variant_verdict, summarize_parameter_sweep


def test_parameter_variants_keep_base_first_and_bound_count():
    params = {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2}

    variants = _parameter_variants("DUAL_MOMENTUM", params, max_variants=5)

    assert variants[0] == params
    assert len(variants) == 5
    assert all("lookback" in item for item in variants)


def test_variant_verdict_requires_cost_survival():
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, False) == "fail"
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, True, 0.67) == "pass"
    assert _variant_verdict(-0.1, 0.1, 0.1, -0.05, True, 0.67) == "borderline"
    assert _variant_verdict(0.1, 0.1, 0.1, -0.05, True, 0.33) == "fail"


def test_summarize_parameter_sweep_reports_best_group():
    rows = [
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "pass", "unseen_cagr": 0.10},
        {"family": "ROTATION", "short_name": "DUAL_MOMENTUM", "verdict": "fail", "unseen_cagr": -0.02},
    ]

    lines = summarize_parameter_sweep(rows)

    assert any("parameter variants tested: 2" in line for line in lines)
    assert any("ROTATION/DUAL_MOMENTUM" in line for line in lines)


def test_parameter_sweep_columns_include_walk_forward_metrics():
    for column in [
        "wf_window_count",
        "wf_pass_rate",
        "wf_median_test_cagr",
        "wf_worst_test_drawdown",
        "wf_status",
        "final_verdict",
    ]:
        assert column in PARAMETER_SWEEP_COLUMNS


def test_parameter_sweep_selects_eodhd_representatives():
    results = [
        {
            "strategy_id": "EODHD1",
            "family": "ROTATION",
            "short_name": "DUAL_MOMENTUM",
            "data_manifest": {"source": "eodhd"},
            "tier": "C",
            "cost_stress": {"survives_double_cost": True},
            "split_metrics": {"unseen": {"cagr": 0.12, "max_drawdown": -0.08}},
        }
    ]

    selected = _select_representatives(results, max_groups=4)

    assert [item["strategy_id"] for item in selected] == ["EODHD1"]


def test_weekly_parameter_sweep_uses_daily_provider_fallback_path(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "massive")
    monkeypatch.setenv("EODHD_API_KEY", "eodhd-token")
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-token")

    def fake_eodhd(*args, **kwargs):
        raise RuntimeError("EODHD outage")

    def fake_massive(root, symbols, api_key, base_url, start_date, adjusted):
        return _bundle("massive", symbols, years=5.0)

    monkeypatch.setattr(parameter_sweep, "load_eodhd_daily_universe", fake_eodhd, raising=False)
    monkeypatch.setattr(parameter_sweep, "load_massive_daily_universe", fake_massive, raising=False)
    monkeypatch.setattr(runner, "load_eodhd_daily_universe", fake_eodhd)
    monkeypatch.setattr(runner, "load_massive_daily_universe", fake_massive)

    bundle = parameter_sweep._load_daily_bundle(LabConfig.from_env(tmp_path), ["SPY", "QQQ"])

    assert bundle.manifest["source"] == "massive"
    assert bundle.manifest["fallback_used"] is True
    assert "EODHD failed" in bundle.manifest["fallback_reason"]


def test_weekly_parameter_sweep_blocks_synthetic_without_explicit_dev_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", raising=False)

    with pytest.raises(RuntimeError, match="weekly deep research requires real EOD data"):
        parameter_sweep._load_daily_bundle(LabConfig.from_env(tmp_path), ["SPY"])


def test_weekly_parameter_sweep_allows_synthetic_with_explicit_dev_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "1")

    bundle = parameter_sweep._load_daily_bundle(LabConfig.from_env(tmp_path), ["SPY"])

    assert bundle.manifest["source"] == "synthetic"


def test_parameter_sweep_rejects_insufficient_history_before_downstream_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "1")
    representative = {
        "strategy_id": "EODHD1",
        "family": "ROTATION",
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": "DUAL_MOMENTUM",
        "hypothesis": "test",
        "rules": "test",
        "parameters": {"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
        "data_manifest": {"source": "eodhd", "years": 20.0},
        "tier": "B",
        "cost_stress": {"survives_double_cost": True},
        "split_metrics": {"unseen": {"cagr": 0.12, "max_drawdown": -0.08}},
    }
    monkeypatch.setattr(parameter_sweep, "load_backtest_results", lambda root: [representative])
    monkeypatch.setattr(parameter_sweep, "_load_daily_bundle", lambda config, symbols: _bundle("synthetic", symbols, years=0.0))
    monkeypatch.setattr(parameter_sweep, "_parameter_variants", lambda short_name, params, max_variants: [params])

    def fail_downstream(*args, **kwargs):
        raise AssertionError("downstream validation must not run after data-quality rejection")

    monkeypatch.setattr(parameter_sweep, "weighted_backtest", fail_downstream)
    monkeypatch.setattr(parameter_sweep, "cost_stress", fail_downstream)

    result = parameter_sweep.run_parameter_sweep(tmp_path, "2026-W01")

    assert result["rows"][0]["final_verdict"] == "fail"
    assert result["rows"][0]["rejection_reason"] == "insufficient_history"
    assert result["rows"][0]["wf_status"] == "rejected_prevalidation"


def test_parameter_row_exposes_walk_forward_metrics():
    class Spec:
        family = "ROTATION"
        short_name = "DUAL_MOMENTUM"

    split_metrics = {
        "train": {"cagr": 0.1},
        "validation": {"cagr": 0.08},
        "unseen": {"cagr": 0.07, "max_drawdown": -0.05},
    }
    walk_forward = {
        "status": "ok",
        "window_count": 4,
        "pass_rate": 0.75,
        "median_test_cagr": 0.04,
        "worst_test_drawdown": -0.08,
    }

    row = _row(Spec(), 1, {"lookback": 126}, split_metrics, {"survives_double_cost": True}, walk_forward, "B", "ok")

    assert row["wf_window_count"] == 4
    assert row["wf_pass_rate"] == 0.75
    assert row["wf_status"] == "ok"
    assert row["final_verdict"] == row["verdict"]


def _bundle(source, symbols, years):
    index = pd.bdate_range("2020-01-01", periods=5)
    frames = {
        symbol: pd.DataFrame(
            {
                "open": [100.0] * len(index),
                "high": [101.0] * len(index),
                "low": [99.0] * len(index),
                "close": [100.0] * len(index),
                "volume": [1000.0] * len(index),
            },
            index=index,
        )
        for symbol in symbols
    }
    panel = pd.concat(frames, axis=1).sort_index()
    return DataBundle(
        "daily_universe",
        "1D",
        panel,
        {
            "name": "daily_universe",
            "source": source,
            "symbols": list(symbols),
            "rows": len(panel),
            "start": str(index.min()),
            "end": str(index.max()),
            "years": years,
            "symbol_diagnostics": [
                {
                    "requested_symbol": symbol,
                    "selected_provider": source,
                    "fallback_used": False,
                    "first_date": str(index.min().date()),
                    "last_date": str(index.max().date()),
                    "daily_bars": len(index),
                    "history_years": years,
                }
                for symbol in symbols
            ],
        },
    )
