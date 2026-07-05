from types import SimpleNamespace
import csv
import hashlib
import json
import urllib.request
import sys

import pandas as pd
import pytest

from research_lab import data
from research_lab import config as config_module
from research_lab.data import DataBundle, load_daily_universe
from research_lab.config import LabConfig
from research_lab import runner
from research_lab.registry import write_leaderboard
from research_lab.reports import write_daily_report
from research_lab.runner import run_daily_research


def _write_eodhd_cache(root, *, symbols=("SPY", "IEF"), manifest_overrides=None, panel=None):
    index = pd.bdate_range("2020-01-02", periods=4)
    if panel is None:
        frames = {}
        for offset, symbol in enumerate(symbols):
            frames[symbol] = pd.DataFrame(
                {
                    "open": [100.0 + offset, 101.0 + offset, 102.0 + offset, 103.0 + offset],
                    "high": [101.0 + offset, 102.0 + offset, 103.0 + offset, 104.0 + offset],
                    "low": [99.0 + offset, 100.0 + offset, 101.0 + offset, 102.0 + offset],
                    "close": [100.5 + offset, 101.5 + offset, 102.5 + offset, 103.5 + offset],
                    "volume": [1000.0, 1100.0, 1200.0, 1300.0],
                },
                index=index,
            )
        panel = pd.concat(frames, axis=1)
    csv_path = root / "data" / "processed" / "eodhd_daily_universe.csv"
    manifest_path = root / "data" / "manifests" / "daily_universe.json"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(csv_path)
    manifest = {
        "name": "daily_universe",
        "source": "eodhd",
        "provider": "eodhd",
        "symbols": list(symbols),
        "requested_symbols": list(symbols),
        "rows": len(panel),
        "start": str(panel.index.min()),
        "end": str(panel.index.max()),
        "years": 0.01,
        "stored_csv": str(csv_path),
        "fallback_used": False,
        "symbol_diagnostics": [
            {
                "requested_symbol": symbol,
                "selected_provider": "eodhd",
                "fallback_used": False,
                "first_date": str(panel.index.min()),
                "last_date": str(panel.index.max()),
                "daily_bars": len(panel),
                "history_years": 0.01,
            }
            for symbol in symbols
        ],
    }
    manifest.update(manifest_overrides or {})
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return csv_path, manifest_path, panel


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_eodhd_cache_config_never_looks_up_eodhd_api_key(tmp_path, monkeypatch):
    original_getenv = config_module.os.getenv

    def guarded_getenv(name, default=None):
        if name == "RESEARCH_LAB_DATA_PROVIDER":
            return "eodhd_cache"
        if name == "EODHD_API_KEY":
            raise AssertionError("eodhd_cache must not read EODHD_API_KEY")
        return original_getenv(name, default)

    monkeypatch.setattr(config_module.os, "getenv", guarded_getenv)

    config = LabConfig.from_env(tmp_path)

    assert config.data_provider == "eodhd_cache"
    assert config.eodhd_api_key == ""


def test_live_eodhd_config_still_loads_eodhd_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd")
    monkeypatch.setenv("EODHD_API_KEY", "live-provider-secret")

    config = LabConfig.from_env(tmp_path)

    assert config.data_provider == "eodhd"
    assert config.eodhd_api_key == "live-provider-secret"


def test_cached_eodhd_loader_reads_only_requested_symbols_in_requested_order(tmp_path):
    _write_eodhd_cache(tmp_path, symbols=("SPY", "IEF", "QQQ"))

    bundle = data.load_cached_eodhd_daily_universe(tmp_path, ["QQQ", "SPY"])

    assert bundle.data.columns.get_level_values(0).unique().tolist() == ["QQQ", "SPY"]
    assert bundle.manifest["source"] == "eodhd"
    assert bundle.manifest["provider"] == "eodhd"
    assert bundle.manifest["load_mode"] == "offline_cache"
    assert bundle.manifest["provider_request_made"] is False
    assert bundle.manifest["fallback_used"] is False
    assert bundle.manifest["requested_symbols"] == ["QQQ", "SPY"]


def test_cached_eodhd_loader_does_not_rewrite_cache_and_requires_no_key(tmp_path, monkeypatch):
    csv_path, manifest_path, _ = _write_eodhd_cache(tmp_path)
    before = (_sha256(csv_path), _sha256(manifest_path))
    monkeypatch.delenv("EODHD_API_KEY", raising=False)

    data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])

    assert (_sha256(csv_path), _sha256(manifest_path)) == before


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        {"source": "eodhd", "provider": None},
        {"source": None, "provider": "eodhd"},
        {"source": None, "provider": None},
        {"source": "eodhd", "provider": "synthetic"},
        {"source": "synthetic", "provider": "eodhd"},
        {"source": "unknown", "provider": "unknown"},
    ],
    ids=[
        "missing-provider",
        "missing-source",
        "missing-both",
        "provider-conflict",
        "source-conflict",
        "unknown",
    ],
)
def test_cached_eodhd_loader_requires_complete_canonical_provenance(tmp_path, manifest_overrides):
    _write_eodhd_cache(tmp_path, manifest_overrides=manifest_overrides)

    with pytest.raises(ValueError, match="provenance"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "EODHD"),
        ("source", "Eodhd"),
        ("source", " eodhd"),
        ("source", "eodhd "),
        ("source", " eodhd "),
        ("source", ""),
        ("source", " "),
        ("source", None),
        ("source", 123),
        ("source", True),
        ("source", []),
        ("source", {}),
        ("provider", "EODHD"),
        ("provider", "Eodhd"),
        ("provider", " eodhd"),
        ("provider", "eodhd "),
        ("provider", " eodhd "),
        ("provider", ""),
        ("provider", " "),
        ("provider", None),
        ("provider", 123),
        ("provider", True),
        ("provider", []),
        ("provider", {}),
        ("provider", "eodhd_cache"),
    ],
)
def test_cached_eodhd_loader_rejects_noncanonical_manifest_provenance_values(tmp_path, field, value):
    _write_eodhd_cache(tmp_path, manifest_overrides={field: value})

    with pytest.raises(ValueError, match="provenance"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


def test_cached_eodhd_loader_accepts_only_exact_canonical_manifest_provenance(tmp_path):
    _write_eodhd_cache(tmp_path, manifest_overrides={"source": "eodhd", "provider": "eodhd"})

    bundle = data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])

    assert bundle.manifest["source"] == "eodhd"
    assert bundle.manifest["provider"] == "eodhd"


def test_cached_eodhd_metadata_validation_does_not_parse_market_dataframe(tmp_path, monkeypatch):
    _write_eodhd_cache(tmp_path)

    def blocked(*args, **kwargs):
        raise AssertionError("metadata validation parsed the market CSV")

    monkeypatch.setattr(pd, "read_csv", blocked)

    metadata = data.validate_cached_eodhd_daily_universe_metadata(tmp_path, ["SPY"])

    assert metadata["source"] == "eodhd"
    assert metadata["provider"] == "eodhd"
    assert metadata["requested_symbols"] == ["SPY"]


@pytest.mark.parametrize("stored_csv", [None, "", "../outside/eodhd_daily_universe.csv"])
def test_cached_eodhd_loader_rejects_missing_or_escaping_manifest_csv_path(tmp_path, stored_csv):
    _write_eodhd_cache(tmp_path, manifest_overrides={"stored_csv": stored_csv})

    with pytest.raises(ValueError, match="stored CSV path"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


@pytest.mark.parametrize("fallback_used", ["true", 1, "false"])
def test_cached_eodhd_loader_rejects_ambiguous_fallback_markers(tmp_path, fallback_used):
    _write_eodhd_cache(tmp_path, manifest_overrides={"fallback_used": fallback_used})

    with pytest.raises(ValueError, match="fallback"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


def test_eodhd_cache_router_ignores_ambient_credentials_and_all_live_transports(tmp_path, monkeypatch):
    _write_eodhd_cache(tmp_path)
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    monkeypatch.setenv("EODHD_API_KEY", "ambient-eodhd")
    monkeypatch.setenv("MASSIVE_API_KEY", "ambient-massive")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic")

    def blocked(*args, **kwargs):
        raise AssertionError("live provider or transport path invoked")

    monkeypatch.setattr(runner, "load_eodhd_daily_universe", blocked)
    monkeypatch.setattr(runner, "load_massive_daily_universe", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr("research_lab.data_eodhd.fetch_eodhd_eod", blocked)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=blocked))

    bundle = runner._load_daily_data_bundle(LabConfig.from_env(tmp_path), symbols=["IEF"])

    assert bundle.manifest["load_mode"] == "offline_cache"
    assert bundle.manifest["requested_symbols"] == ["IEF"]


@pytest.mark.parametrize(
    ("missing_relative", "message"),
    [
        ("data/processed/eodhd_daily_universe.csv", "cached EODHD CSV"),
        ("data/manifests/daily_universe.json", "cached EODHD manifest"),
    ],
)
def test_cached_eodhd_loader_fails_closed_when_artifact_missing(tmp_path, missing_relative, message):
    _write_eodhd_cache(tmp_path)
    (tmp_path / missing_relative).unlink()

    with pytest.raises(ValueError, match=message):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        {"source": "synthetic", "provider": "synthetic"},
        {"fallback_used": True},
        {"fallback_reason": "synthetic fallback"},
        {"symbol_diagnostics": [{"requested_symbol": "SPY", "fallback_used": True}]},
    ],
)
def test_cached_eodhd_loader_rejects_non_eodhd_or_fallback_manifest(tmp_path, manifest_overrides):
    _write_eodhd_cache(tmp_path, manifest_overrides=manifest_overrides)

    with pytest.raises(ValueError, match="provenance|fallback"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


def test_cached_eodhd_loader_rejects_missing_requested_symbol(tmp_path):
    _write_eodhd_cache(tmp_path, symbols=("SPY",))

    with pytest.raises(ValueError, match="IEF"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["IEF"])


def test_cached_eodhd_loader_rejects_missing_ohlcv_field(tmp_path):
    _, _, panel = _write_eodhd_cache(tmp_path, symbols=("SPY",))
    panel = panel.drop(columns=[("SPY", "volume")])
    _write_eodhd_cache(tmp_path, symbols=("SPY",), panel=panel)

    with pytest.raises(ValueError, match="volume"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


@pytest.mark.parametrize("index", [["bad-date", "2020-01-03"], ["2020-01-02", "2020-01-02"]])
def test_cached_eodhd_loader_rejects_malformed_or_duplicate_date_index(tmp_path, index):
    frame = pd.DataFrame(
        {("SPY", field): [1.0, 2.0] for field in ("open", "high", "low", "close", "volume")},
        index=index,
    )
    _write_eodhd_cache(tmp_path, symbols=("SPY",), panel=frame)

    with pytest.raises(ValueError, match="date index|unique"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


def test_cached_eodhd_loader_rejects_unusable_numeric_fields(tmp_path):
    _, _, panel = _write_eodhd_cache(tmp_path, symbols=("SPY",))
    panel[("SPY", "close")] = "not-a-number"
    _write_eodhd_cache(tmp_path, symbols=("SPY",), panel=panel)

    with pytest.raises(ValueError, match="numeric.*close|close.*numeric"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


def test_cached_eodhd_loader_rejects_infinite_ohlcv_values(tmp_path):
    _, _, panel = _write_eodhd_cache(tmp_path, symbols=("SPY",))
    panel.loc[panel.index[0], ("SPY", "close")] = float("inf")
    _write_eodhd_cache(tmp_path, symbols=("SPY",), panel=panel)

    with pytest.raises(ValueError, match="numeric.*close|close.*numeric"):
        data.load_cached_eodhd_daily_universe(tmp_path, ["SPY"])


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


def test_synthetic_router_ignores_ambient_provider_credentials_and_stays_local(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_USE_YFINANCE", "0")
    monkeypatch.setenv("EODHD_API_KEY", "fake-eodhd")
    monkeypatch.setenv("MASSIVE_API_KEY", "fake-massive")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    def blocked(*args, **kwargs):
        raise AssertionError("live provider or transport path invoked")

    monkeypatch.setattr(runner, "load_eodhd_daily_universe", blocked)
    monkeypatch.setattr(runner, "load_massive_daily_universe", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=blocked))

    bundle = runner._load_daily_data_bundle(LabConfig.from_env(tmp_path), symbols=["SPY"])
    assert bundle.manifest["source"] == "synthetic"
    assert bundle.manifest["symbols"] == ["SPY"]


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
