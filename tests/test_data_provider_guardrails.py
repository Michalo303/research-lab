from types import SimpleNamespace
import csv
import json

import pandas as pd
import pytest

from research_lab.data import DataBundle, load_daily_universe
from research_lab.config import LabConfig
from research_lab import runner
from research_lab.registry import write_leaderboard
from research_lab.reports import write_daily_report
from research_lab.runner import run_daily_research


def test_yfinance_failure_does_not_fall_back_to_synthetic_by_default(tmp_path, monkeypatch):
    fake_yfinance = SimpleNamespace(download=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down")))
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yfinance)
    monkeypatch.delenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", raising=False)

    with pytest.raises(RuntimeError, match="synthetic fallback is disabled"):
        load_daily_universe(tmp_path, ["SPY"], use_yfinance=True)


def test_yfinance_failure_can_use_explicit_synthetic_fallback(tmp_path, monkeypatch):
    fake_yfinance = SimpleNamespace(download=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down")))
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yfinance)
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "1")

    bundle = load_daily_universe(tmp_path, ["SPY"], use_yfinance=True)

    assert bundle.manifest["source"] == "synthetic"


def test_yfinance_provider_enables_yfinance_loader(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "yfinance")
    monkeypatch.delenv("RESEARCH_LAB_USE_YFINANCE", raising=False)

    config = LabConfig.from_env(tmp_path)

    assert config.data_provider == "yfinance"
    assert config.use_yfinance is True


def test_massive_provider_missing_key_fails_without_synthetic_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "massive")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "1")

    try:
        run_daily_research(tmp_path)
    except ValueError as exc:
        assert "MASSIVE_API_KEY" in str(exc)
    else:
        raise AssertionError("Massive provider without API key must fail instead of using synthetic data")


def test_eodhd_credentials_take_precedence_over_massive(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "massive")
    monkeypatch.setenv("EODHD_API_KEY", "eodhd-token")
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-token")
    calls = []

    def fake_eodhd(root, symbols, api_key, start_date):
        calls.append(("eodhd", symbols, api_key, start_date))
        return _daily_bundle("eodhd", symbols, fallback_used=False)

    def fake_massive(*args, **kwargs):
        calls.append(("massive", args, kwargs))
        raise AssertionError("Massive must not be selected before EODHD when EODHD credentials exist")

    monkeypatch.setattr(runner, "load_eodhd_daily_universe", fake_eodhd)
    monkeypatch.setattr(runner, "load_massive_daily_universe", fake_massive)

    bundle = runner._load_daily_data_bundle(LabConfig.from_env(tmp_path))

    assert bundle.manifest["source"] == "eodhd"
    assert [call[0] for call in calls] == ["eodhd"]
    assert all(row["selected_provider"] == "eodhd" for row in bundle.manifest["symbol_diagnostics"])
    assert all(row["fallback_used"] is False for row in bundle.manifest["symbol_diagnostics"])


def test_massive_fallback_happens_when_eodhd_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "massive")
    monkeypatch.setenv("EODHD_API_KEY", "eodhd-token")
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-token")

    def fake_eodhd(*args, **kwargs):
        raise RuntimeError("EODHD outage")

    def fake_massive(root, symbols, api_key, base_url, start_date, adjusted):
        return _daily_bundle("massive", symbols, fallback_used=False)

    monkeypatch.setattr(runner, "load_eodhd_daily_universe", fake_eodhd)
    monkeypatch.setattr(runner, "load_massive_daily_universe", fake_massive)

    bundle = runner._load_daily_data_bundle(LabConfig.from_env(tmp_path))

    assert bundle.manifest["source"] == "massive"
    assert all(row["selected_provider"] == "massive" for row in bundle.manifest["symbol_diagnostics"])
    assert all(row["fallback_used"] is True for row in bundle.manifest["symbol_diagnostics"])
    assert "WARNING: EODHD credentials exist but EODHD was not selected" in capsys.readouterr().out


def test_registry_leaderboard_and_report_preserve_actual_provider(tmp_path):
    result = _result("eodhd")

    runner._persist_result(tmp_path, result)
    registry_row = json.loads((tmp_path / "registry" / "strategy_registry.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert registry_row["data_source"] == "eodhd"

    write_leaderboard(tmp_path / "registry" / "leaderboard.csv", [runner._leaderboard_row(result)])
    with (tmp_path / "registry" / "leaderboard.csv").open(newline="", encoding="utf-8") as handle:
        leaderboard_rows = list(csv.DictReader(handle))
    assert leaderboard_rows[0]["data_source"] == "eodhd"

    write_daily_report(tmp_path / "reports" / "daily.md", [result])
    report = (tmp_path / "reports" / "daily.md").read_text(encoding="utf-8")
    assert "- data sources: eodhd" in report
    assert "| S1 | LONGTERM | ETF | 1D | eodhd |" in report


def _daily_bundle(source, symbols, fallback_used):
    index = pd.bdate_range("1990-01-02", periods=5)
    frames = {}
    for offset, symbol in enumerate(symbols):
        frames[symbol] = pd.DataFrame(
            {
                "open": [100 + offset] * len(index),
                "high": [101 + offset] * len(index),
                "low": [99 + offset] * len(index),
                "close": [100 + offset] * len(index),
                "volume": [1000] * len(index),
            },
            index=index,
        )
    panel = pd.concat(frames, axis=1).sort_index()
    diagnostics = [
        {
            "requested_symbol": symbol,
            "selected_provider": source,
            "fallback_used": fallback_used,
            "first_date": str(index.min().date()),
            "last_date": str(index.max().date()),
            "daily_bars": len(index),
            "history_years": 0.02,
        }
        for symbol in symbols
    ]
    return DataBundle(
        "daily_universe",
        "1D",
        panel,
        {
            "name": "daily_universe",
            "source": source,
            "symbols": symbols,
            "rows": len(panel),
            "start": str(index.min()),
            "end": str(index.max()),
            "years": 0.02,
            "symbol_diagnostics": diagnostics,
        },
    )


def _result(source):
    return {
        "strategy_id": "S1",
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": "TREND",
        "hypothesis": "test",
        "rules": "test",
        "parameters": {},
        "data_manifest": {
            "source": source,
            "start": "1990-01-02",
            "end": "2026-01-02",
            "rows": 9000,
            "years": 36.0,
            "symbols": ["SPY"],
        },
        "data_source": source,
        "cost_stress": {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": True,
            "double_unseen_cagr": 0.05,
        },
        "split_metrics": {
            "train": {"cagr": 0.1},
            "validation": {"cagr": 0.1},
            "unseen": {
                "cagr": 0.1,
                "sharpe": 1.0,
                "mar": 1.0,
                "max_drawdown": -0.05,
                "profit_factor": 1.2,
                "trade_count": 10,
            },
        },
        "tier": "C",
        "tier_reason": "test",
        "average_exposure": 1.0,
        "average_turnover": 0.1,
    }
