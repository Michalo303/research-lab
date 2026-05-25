from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, Iterable


EODHD_BASE_URL = "https://eodhd.com/api/eod"
USER_AGENT = "research-lab/0.1 research-only"


def get_eod_history(
    symbol: str,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    adjusted: bool = True,
    api_key: str | None = None,
    base_url: str = EODHD_BASE_URL,
    max_retries: int = 3,
    timeout: int = 30,
    fetcher: Callable | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict:
    """Fetch daily EODHD history with controlled failure states.

    Returns a provider-neutral dict instead of raising for expected provider
    failures, so historical validation can fail safely inside weekly research.
    """

    key = api_key if api_key is not None else os.getenv("EODHD_API_KEY", "")
    if not str(key).strip():
        return _status(symbol, adjusted, "missing", "EODHD_API_KEY missing", [])

    retries = max(1, int(max_retries))
    fetch = fetcher or urllib.request.urlopen
    pause = sleep or time.sleep
    request = _build_request(symbol, str(key), start_date, end_date, base_url)
    last_reason = "request failed"
    rate_limited = False

    for attempt in range(retries):
        try:
            with fetch(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                if status == 429:
                    rate_limited = True
                    last_reason = "EODHD rate limit response"
                    _sleep_before_retry(response, pause, attempt)
                    continue
                if status >= 500:
                    last_reason = f"EODHD transient HTTP {status}"
                    _sleep_before_retry(response, pause, attempt)
                    continue
                if status >= 400:
                    return _status(symbol, adjusted, "error", f"EODHD HTTP {status}", [], rate_limited=False)
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0))
            if status == 429:
                rate_limited = True
                last_reason = "EODHD rate limit response"
                _sleep_before_retry(exc, pause, attempt)
                continue
            if status >= 500:
                last_reason = f"EODHD transient HTTP {status}"
                _sleep_before_retry(exc, pause, attempt)
                continue
            return _status(symbol, adjusted, "error", f"EODHD HTTP {status}", [], rate_limited=False)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            last_reason = _safe_reason(exc)
            _sleep_before_retry(None, pause, attempt)
            continue

        if not isinstance(payload, list):
            return _status(symbol, adjusted, "error", "malformed EODHD response: expected list", [])
        rows = _normalize_rows(payload, adjusted)
        if not rows:
            return _status(symbol, adjusted, "missing", "EODHD returned no rows", [])
        return _status(symbol, adjusted, "available", "", rows)

    return _status(symbol, adjusted, "error", last_reason, [], rate_limited=rate_limited)


def _build_request(
    symbol: str,
    api_key: str,
    start_date: str | date | None,
    end_date: str | date | None,
    base_url: str,
) -> urllib.request.Request:
    params = {
        "api_token": api_key,
        "fmt": "json",
        "period": "d",
        "order": "a",
    }
    if start_date:
        params["from"] = _date_text(start_date)
    if end_date:
        params["to"] = _date_text(end_date)
    url = f"{base_url.rstrip('/')}/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}"
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _normalize_rows(payload: Iterable[dict], adjusted: bool) -> list[dict]:
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        date_text = item.get("date")
        raw_close = _float_or_none(item.get("close"))
        adjusted_close = _float_or_none(item.get("adjusted_close"))
        close = adjusted_close if adjusted and adjusted_close is not None else raw_close
        rows.append(
            {
                "date": date_text,
                "open": _float_or_none(item.get("open")),
                "high": _float_or_none(item.get("high")),
                "low": _float_or_none(item.get("low")),
                "close": close,
                "raw_close": raw_close,
                "adjusted_close": adjusted_close,
                "volume": _float_or_none(item.get("volume")),
            }
        )
    return [row for row in rows if row.get("date")]


def _float_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _sleep_before_retry(response, sleep: Callable[[float], None], attempt: int) -> None:
    retry_after = None
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = float(headers.get("Retry-After")) if headers.get("Retry-After") is not None else None
    except (TypeError, ValueError):
        retry_after = None
    delay = retry_after if retry_after is not None else min(2.0**attempt, 8.0)
    sleep(max(delay, 0.0))


def _status(symbol: str, adjusted: bool, status: str, reason: str, rows: list[dict], rate_limited: bool = False) -> dict:
    return {
        "provider": "eodhd",
        "symbol": symbol,
        "adjusted": bool(adjusted),
        "coverage_status": status,
        "reason": reason,
        "rate_limited": rate_limited,
        "rows": rows,
        "row_count": len(rows),
        "research_only": True,
        "not_trading_signal": True,
    }


def _safe_reason(exc: BaseException) -> str:
    text = str(exc)
    if "api_token" in text.lower():
        return exc.__class__.__name__
    return text[:240] or exc.__class__.__name__
