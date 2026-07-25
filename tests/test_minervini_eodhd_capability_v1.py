from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from research_lab.research.minervini_eodhd_capability_v1 import (
    run_minervini_eodhd_capability_v1,
)
from scripts.check_minervini_eodhd_capability_v1 import main


def _successful_getter(seen: list[str]):
    def getter(url: str):
        seen.append(url)
        parsed = urlparse(url)
        if parsed.path == "/api/exchange-symbol-list/US":
            payload = [
                {
                    "Code": "AAPL",
                    "Name": "Apple Inc",
                    "Country": "USA",
                    "Exchange": "NASDAQ",
                    "Currency": "USD",
                    "Type": "Common Stock",
                    "Isin": "US0378331005",
                }
            ]
        elif parsed.path == "/api/eod/AAPL.US":
            payload = [
                {
                    "date": "2025-01-02",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "adjusted_close": 100.0,
                    "volume": 1_000_000,
                }
            ]
        elif parsed.path == "/api/splits/AAPL.US":
            payload = [
                {"date": "2020-08-31", "split": "4.000000/1.000000"}
            ]
        else:
            raise AssertionError(f"unexpected endpoint: {parsed.path}")
        return payload, {"http_status": 200, "content_type": "application/json"}

    return getter


def test_capability_uses_exactly_four_bounded_probes_and_redacts_key():
    seen: list[str] = []

    result = run_minervini_eodhd_capability_v1(
        api_key="secret-value",
        http_get=_successful_getter(seen),
    )

    assert result["status"] == "CAPABLE"
    assert result["provider_calls_used"] == 4
    assert result["active_symbols_available"] is True
    assert result["delisted_symbols_available"] is True
    assert result["daily_adjusted_ohlcv_available"] is True
    assert result["splits_available"] is True
    assert len(seen) == 4
    assert "secret-value" not in json.dumps(result)
    parsed = [urlparse(url) for url in seen]
    assert [item.path for item in parsed] == [
        "/api/exchange-symbol-list/US",
        "/api/exchange-symbol-list/US",
        "/api/eod/AAPL.US",
        "/api/splits/AAPL.US",
    ]
    assert parse_qs(parsed[0].query)["type"] == ["common_stock"]
    assert "delisted" not in parse_qs(parsed[0].query)
    assert parse_qs(parsed[1].query)["delisted"] == ["1"]
    assert result["broker_actions_used"] == 0
    assert result["registry_write_performed"] is False


def test_capability_missing_key_makes_no_calls():
    calls: list[str] = []

    result = run_minervini_eodhd_capability_v1(
        env={},
        http_get=lambda url: calls.append(url),
    )

    assert result["status"] == "MISSING_API_KEY"
    assert result["provider_calls_used"] == 0
    assert calls == []


def test_capability_fails_closed_for_bad_status_or_payload():
    def getter(url: str):
        parsed = urlparse(url)
        if parsed.path == "/api/eod/AAPL.US":
            return [{"date": "2025-01-02", "close": 100.0}], {
                "http_status": 200
            }
        if parsed.path == "/api/splits/AAPL.US":
            return {"error": "forbidden"}, {"http_status": 403}
        return [{"Code": "SPY", "Type": "ETF"}], {"http_status": 200}

    result = run_minervini_eodhd_capability_v1(
        api_key="secret-value",
        http_get=getter,
    )

    assert result["status"] == "INSUFFICIENT_CAPABILITY"
    assert result["active_symbols_available"] is False
    assert result["delisted_symbols_available"] is False
    assert result["daily_adjusted_ohlcv_available"] is False
    assert result["splits_available"] is False
    assert result["provider_calls_used"] == 4


def test_cli_defaults_to_dry_run_without_provider_call(capsys):
    exit_code = main([])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "status=DRY_RUN",
        "planned_provider_calls=4",
    ]
