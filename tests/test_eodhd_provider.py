from __future__ import annotations

import json
from datetime import date

from research_lab.data.eodhd import get_eod_history


class FakeResponse:
    def __init__(self, payload, status: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_eodhd_missing_api_key_is_controlled(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)

    result = get_eod_history("SPY.US")

    assert result["coverage_status"] == "missing"
    assert result["rows"] == []
    assert "API_KEY" in result["reason"]
    assert "api_token" not in result["reason"]


def test_eodhd_malformed_response_is_safe_to_fail():
    def fetcher(request, timeout=30):
        return FakeResponse({"unexpected": "shape"})

    result = get_eod_history("SPY.US", api_key="secret-token", fetcher=fetcher, sleep=lambda _: None)

    assert result["coverage_status"] == "error"
    assert result["rows"] == []
    assert "malformed" in result["reason"]
    assert "secret-token" not in result["reason"]


def test_eodhd_adjusted_flag_uses_adjusted_close_when_available():
    payload = [
        {
            "date": "2020-01-02",
            "open": 100,
            "high": 105,
            "low": 99,
            "close": 104,
            "adjusted_close": 52,
            "volume": 1000,
        }
    ]

    def fetcher(request, timeout=30):
        url = request.full_url
        assert "api_token=secret-token" in url
        assert "from=2020-01-01" in url
        assert "to=2020-01-31" in url
        return FakeResponse(payload)

    adjusted = get_eod_history(
        "SPY.US",
        start_date=date(2020, 1, 1),
        end_date="2020-01-31",
        adjusted=True,
        api_key="secret-token",
        fetcher=fetcher,
        sleep=lambda _: None,
    )
    raw = get_eod_history(
        "SPY.US",
        start_date="2020-01-01",
        end_date="2020-01-31",
        adjusted=False,
        api_key="secret-token",
        fetcher=fetcher,
        sleep=lambda _: None,
    )

    assert adjusted["coverage_status"] == "available"
    assert adjusted["rows"][0]["close"] == 52.0
    assert adjusted["rows"][0]["raw_close"] == 104.0
    assert adjusted["adjusted"] is True
    assert raw["rows"][0]["close"] == 104.0
    assert raw["rows"][0]["adjusted_close"] == 52.0


def test_eodhd_rate_limit_status_after_retries():
    calls = {"count": 0}

    def fetcher(request, timeout=30):
        calls["count"] += 1
        return FakeResponse({"message": "limit"}, status=429, headers={"Retry-After": "0"})

    result = get_eod_history(
        "SPY.US",
        api_key="secret-token",
        fetcher=fetcher,
        sleep=lambda _: None,
        max_retries=2,
    )

    assert calls["count"] == 2
    assert result["coverage_status"] == "error"
    assert result["rate_limited"] is True
    assert "rate limit" in result["reason"]
