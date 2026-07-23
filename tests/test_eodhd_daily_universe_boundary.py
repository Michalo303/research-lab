import pandas as pd

from research_lab.data import load_eodhd_daily_universe


def test_eodhd_daily_universe_skips_intraday_only_btcusdt_symbol(tmp_path, monkeypatch):
    requested = []

    def fetch(symbol, **kwargs):
        requested.append(symbol)
        return pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
            index=pd.to_datetime(["2026-01-01"]),
        )

    monkeypatch.setattr("research_lab.data_eodhd.fetch_eodhd_eod", fetch)

    bundle = load_eodhd_daily_universe(tmp_path, ["SPY", "BTCUSDT"], api_key="test-key", start_date="2020-01-01")

    assert requested == ["SPY.US"]
    assert list(bundle.data.columns.get_level_values(0).unique()) == ["SPY"]
    assert bundle.manifest["requested_symbols"] == ["SPY", "BTCUSDT"]
    assert bundle.manifest["loaded_symbols"] == ["SPY"]
    assert bundle.manifest["excluded_intraday_symbols"] == ["BTCUSDT"]
