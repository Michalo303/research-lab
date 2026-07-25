from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


RESULT_VERSION = "minervini_eodhd_capability_result_v1"
PROVIDER_CALL_LIMIT = 4
HttpGet = Callable[[str], tuple[object, dict[str, object]]]


def run_minervini_eodhd_capability_v1(
    *,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, object]:
    """Perform exactly four bounded, read-only EODHD capability probes."""
    environment = os.environ if env is None else env
    key = (
        api_key if api_key is not None else environment.get("EODHD_API_KEY", "")
    ).strip()
    if not key:
        return _finalize(
            {
                "version": RESULT_VERSION,
                "status": "MISSING_API_KEY",
                "active_symbols_available": False,
                "delisted_symbols_available": False,
                "daily_adjusted_ohlcv_available": False,
                "splits_available": False,
                "probes": [],
                "provider_calls_used": 0,
            }
        )

    urls = _probe_urls(key)
    getter = http_get or _download_json
    probe_results: list[dict[str, object]] = []
    payloads: list[object] = []
    for name, url in urls:
        try:
            payload, metadata = getter(url)
            http_status = metadata.get("http_status")
            ok = http_status == 200
        except Exception:
            payload = None
            http_status = 0
            ok = False
        payloads.append(payload)
        probe_results.append(
            {
                "name": name,
                "endpoint_identity": _sanitized_endpoint_identity(url),
                "http_status": http_status,
                "authorized": ok,
                "row_count": len(payload) if isinstance(payload, list) else 0,
            }
        )

    active = _valid_common_stock_list(payloads[0], probe_results[0])
    delisted = _valid_common_stock_list(payloads[1], probe_results[1])
    daily = _valid_daily_payload(payloads[2], probe_results[2])
    splits = _valid_splits_payload(payloads[3], probe_results[3])
    capable = active and delisted and daily and splits
    return _finalize(
        {
            "version": RESULT_VERSION,
            "status": "CAPABLE" if capable else "INSUFFICIENT_CAPABILITY",
            "active_symbols_available": active,
            "delisted_symbols_available": delisted,
            "daily_adjusted_ohlcv_available": daily,
            "splits_available": splits,
            "probes": probe_results,
            "provider_calls_used": len(urls),
        }
    )


def _probe_urls(api_key: str) -> list[tuple[str, str]]:
    base = "https://eodhd.com/api"
    queries = [
        (
            "active_common_stocks",
            "/exchange-symbol-list/US",
            {"type": "common_stock", "fmt": "json", "api_token": api_key},
        ),
        (
            "delisted_common_stocks",
            "/exchange-symbol-list/US",
            {
                "delisted": "1",
                "type": "common_stock",
                "fmt": "json",
                "api_token": api_key,
            },
        ),
        (
            "adjusted_daily_ohlcv",
            "/eod/AAPL.US",
            {
                "from": "2025-01-02",
                "to": "2025-01-10",
                "period": "d",
                "fmt": "json",
                "api_token": api_key,
            },
        ),
        (
            "splits",
            "/splits/AAPL.US",
            {"fmt": "json", "api_token": api_key},
        ),
    ]
    result = [
        (name, f"{base}{path}?{urllib.parse.urlencode(query)}")
        for name, path, query in queries
    ]
    if len(result) != PROVIDER_CALL_LIMIT:
        raise RuntimeError("capability probe count changed unexpectedly.")
    return result


def _valid_common_stock_list(
    payload: object, diagnostic: dict[str, object]
) -> bool:
    return bool(
        diagnostic["authorized"]
        and isinstance(payload, list)
        and payload
        and any(
            isinstance(row, dict)
            and str(row.get("Code", "")).strip()
            and str(row.get("Type", "")).casefold().replace("_", " ")
            == "common stock"
            for row in payload
        )
    )


def _valid_daily_payload(
    payload: object, diagnostic: dict[str, object]
) -> bool:
    required = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    }
    return bool(
        diagnostic["authorized"]
        and isinstance(payload, list)
        and payload
        and all(isinstance(row, dict) and required.issubset(row) for row in payload)
    )


def _valid_splits_payload(
    payload: object, diagnostic: dict[str, object]
) -> bool:
    return bool(
        diagnostic["authorized"]
        and isinstance(payload, list)
        and payload
        and all(
            isinstance(row, dict)
            and isinstance(row.get("date"), str)
            and ("split" in row or "split_factor" in row)
            for row in payload
        )
    )


def _sanitized_endpoint_identity(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = [(key, value) for key, value in query if key != "api_token"]
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            urllib.parse.urlencode(safe_query),
            "",
        )
    )


def _download_json(url: str) -> tuple[object, dict[str, object]]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "research-lab/0.1 research-only"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw), {
                "http_status": int(getattr(response, "status", 200)),
                "content_type": str(response.headers.get("Content-Type", "")),
            }
    except urllib.error.HTTPError as exc:
        return None, {"http_status": int(exc.code)}


def _finalize(result: dict[str, object]) -> dict[str, object]:
    result.update(
        {
            "network_used": bool(result["provider_calls_used"]),
            "broker_actions_used": 0,
            "registry_write_performed": False,
            "promotion_performed": False,
            "deployment_performed": False,
            "production_runtime_supported": False,
        }
    )
    result["output_payload_sha256"] = hashlib.sha256(
        json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return result
