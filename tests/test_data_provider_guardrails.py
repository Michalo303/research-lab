from types import SimpleNamespace

import pytest

from research_lab.data import load_daily_universe
from research_lab.config import LabConfig
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
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "1")

    try:
        run_daily_research(tmp_path)
    except ValueError as exc:
        assert "MASSIVE_API_KEY" in str(exc)
    else:
        raise AssertionError("Massive provider without API key must fail instead of using synthetic data")
