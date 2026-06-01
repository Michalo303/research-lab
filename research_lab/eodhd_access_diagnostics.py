from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from research_lab.fundamentals_eodhd import classify_eodhd_payload


HttpGet = Callable[[str], tuple[Any, dict[str, Any]]]


def run_eodhd_access_diagnostics(
    *,
    api_key: str | None = None,
    env: dict[str, str] | None = None,
    symbol: str = "AAPL.US",
    daily_start: str = "2026-05-01",
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    key = (api_key if api_key is not None else env.get("EODHD_API_KEY", "")).strip()
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "eodhd_access_diagnostics_only",
        "symbol": symbol,
        "api_key": {"name": "EODHD_API_KEY", "present": bool(key)},
    }
    if not key:
        result["daily_ohlcv"] = _missing_key_diagnostic("daily_ohlcv")
        result["fundamentals"] = _missing_key_diagnostic("fundamentals") | {"fatal": False}
        return result

    getter = http_get or _download_json
    daily_url = _daily_url(symbol, key, daily_start)
    fundamentals_url = _fundamentals_url(symbol, key)
    result["daily_ohlcv"] = _daily_diagnostic(daily_url, getter, key)
    result["fundamentals"] = _fundamentals_diagnostic(fundamentals_url, getter, key)
    return _sanitize_value(result, key)


def _missing_key_diagnostic(endpoint: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "authorized": False,
        "status": "missing_api_key",
        "http_status": None,
        "request_url": "",
    }


def _daily_diagnostic(url: str, http_get: HttpGet, api_key: str) -> dict[str, Any]:
    payload, meta = _safe_get(url, http_get, api_key)
    status = _status_from_http(meta.get("http_status"), payload)
    authorized = status == "ok" and isinstance(payload, list)
    return _base_endpoint_diagnostic(
        endpoint="daily_ohlcv",
        url=url,
        status=status,
        authorized=authorized,
        meta=meta,
        api_key=api_key,
    ) | {"parsed_row_count": len(payload) if isinstance(payload, list) else 0}


def _fundamentals_diagnostic(url: str, http_get: HttpGet, api_key: str) -> dict[str, Any]:
    payload, meta = _safe_get(url, http_get, api_key)
    status = _status_from_http(meta.get("http_status"), payload)
    authorized = status == "ok" and isinstance(payload, dict) and not payload.get("error")
    diagnostic = _base_endpoint_diagnostic(
        endpoint="fundamentals",
        url=url,
        status=status,
        authorized=authorized,
        meta=meta,
        api_key=api_key,
    ) | {"fatal": False}
    if authorized:
        classified = classify_eodhd_payload(payload, request_url=url)
        diagnostic.update(
            {
                "timestamp_safety": classified.get("timestamp_safety", "unknown"),
                "row_count": classified.get("row_count", 0),
                "timestamp_safe_rows": classified.get("timestamp_safe_rows", 0),
                "uncertain_rows": classified.get("uncertain_rows", 0),
            }
        )
    else:
        diagnostic["timestamp_safety"] = "unknown"
    return diagnostic


def _base_endpoint_diagnostic(
    *,
    endpoint: str,
    url: str,
    status: str,
    authorized: bool,
    meta: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "authorized": authorized,
        "status": status,
        "http_status": meta.get("http_status"),
        "content_type": _sanitize_text(str(meta.get("content_type", "")), api_key),
        "body_length": meta.get("body_length", 0),
        "body_preview": _sanitize_text(str(meta.get("body_preview", ""))[:300], api_key),
        "request_url": _sanitize_url(url),
    }


def _safe_get(url: str, http_get: HttpGet, api_key: str) -> tuple[Any, dict[str, Any]]:
    try:
        payload, meta = http_get(url)
    except Exception as exc:
        payload = {"error": True, "message": str(exc)[:300]}
        meta = {"http_status": 0, "content_type": "", "body_length": 0, "body_preview": str(exc)[:300]}
    return _sanitize_value(payload, api_key), _sanitize_value(meta, api_key)


def _status_from_http(http_status: Any, payload: Any) -> str:
    if http_status == 401:
        return "unauthorized"
    if http_status == 403:
        return "forbidden"
    if http_status == 200 and not (isinstance(payload, dict) and payload.get("error")):
        return "ok"
    if isinstance(payload, dict) and payload.get("error"):
        return "provider_error"
    if http_status == 0:
        return "request_error"
    return "unexpected_status"


def _daily_url(symbol: str, api_key: str, start: str) -> str:
    query = urllib.parse.urlencode({"api_token": api_key, "fmt": "json", "from": start, "period": "d"})
    return f"https://eodhd.com/api/eod/{urllib.parse.quote(symbol)}?{query}"


def _fundamentals_url(symbol: str, api_key: str) -> str:
    query = urllib.parse.urlencode({"api_token": api_key, "fmt": "json"})
    return f"https://eodhd.com/api/fundamentals/{urllib.parse.quote(symbol)}?{query}"


def _download_json(url: str) -> tuple[Any, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw), {
                "http_status": int(getattr(response, "status", 200)),
                "content_type": str(response.headers.get("Content-Type", "")),
                "body_length": len(raw),
                "body_preview": raw,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": True, "message": raw[:300]}
        return payload, {
            "http_status": int(exc.code),
            "content_type": str(exc.headers.get("Content-Type", "")),
            "body_length": len(raw),
            "body_preview": raw,
        }


def _sanitize_url(url: str) -> str:
    return re.sub(r"(?i)(api_token=)[^&]+", r"\1***", url)


def _sanitize_text(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "***")
    return re.sub(r"(?i)((?:api[_ -]?)?(?:token|key)\s*[=:]?\s*)\S+", r"\1***", text)


def _sanitize_value(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value, api_key)
    if isinstance(value, list):
        return [_sanitize_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item, api_key) for key, item in value.items()}
    return value
