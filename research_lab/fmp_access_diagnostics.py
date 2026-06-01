from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


HttpGet = Callable[[str], tuple[Any, dict[str, Any]]]

DATE_FIELD_ORDER = (
    "acceptedDate",
    "filingDate",
    "fillingDate",
    "reportedDate",
    "date",
    "calendarYear",
    "period",
    "fiscalYear",
    "finalLink",
    "link",
    "cik",
)


@dataclass(frozen=True)
class FmpEndpoint:
    name: str
    path: str
    period: str | None = None


FMP_ENDPOINTS = (
    FmpEndpoint("income statement annual", "/stable/income-statement", "annual"),
    FmpEndpoint("income statement quarterly", "/stable/income-statement", "quarter"),
    FmpEndpoint("balance sheet annual", "/stable/balance-sheet-statement", "annual"),
    FmpEndpoint("balance sheet quarterly", "/stable/balance-sheet-statement", "quarter"),
    FmpEndpoint("cash flow annual", "/stable/cash-flow-statement", "annual"),
    FmpEndpoint("cash flow quarterly", "/stable/cash-flow-statement", "quarter"),
    FmpEndpoint("key metrics annual", "/stable/key-metrics", "annual"),
    FmpEndpoint("key metrics quarterly", "/stable/key-metrics", "quarter"),
    FmpEndpoint("ratios annual", "/stable/ratios", "annual"),
    FmpEndpoint("ratios quarterly", "/stable/ratios", "quarter"),
    FmpEndpoint("enterprise values", "/stable/enterprise-values"),
    FmpEndpoint("shares float", "/stable/shares-float"),
    FmpEndpoint("as-reported income annual", "/stable/income-statement-as-reported", "annual"),
    FmpEndpoint("as-reported income quarterly", "/stable/income-statement-as-reported", "quarter"),
    FmpEndpoint("as-reported balance annual", "/stable/balance-sheet-statement-as-reported", "annual"),
    FmpEndpoint("as-reported balance quarterly", "/stable/balance-sheet-statement-as-reported", "quarter"),
    FmpEndpoint("as-reported cash flow annual", "/stable/cash-flow-statement-as-reported", "annual"),
    FmpEndpoint("as-reported cash flow quarterly", "/stable/cash-flow-statement-as-reported", "quarter"),
)


def run_fmp_access_diagnostics(
    *,
    api_key: str | None = None,
    env: dict[str, str] | None = None,
    symbols: list[str] | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    key = (api_key if api_key is not None else env.get("FMP_API_KEY", "")).strip()
    symbol = _first_symbol(symbols)
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "fmp_access_diagnostics_only",
        "api_key": {"name": "FMP_API_KEY", "present": bool(key)},
        "endpoints": [],
    }

    if not key:
        result["endpoints"] = [
            _missing_key_diagnostic(endpoint, symbol)
            for endpoint in FMP_ENDPOINTS
        ]
        return result

    getter = http_get or _download_json
    for endpoint in FMP_ENDPOINTS:
        url = _endpoint_url(endpoint, symbol, key)
        sanitized_url = _sanitize_url(url, key)
        try:
            payload, meta = getter(url)
            diagnostic = classify_fmp_endpoint_payload(
                endpoint_name=endpoint.name,
                endpoint_url_sanitized=sanitized_url,
                symbol=symbol,
                period=endpoint.period,
                credential_present=True,
                payload=payload,
                meta=meta,
                api_key=key,
            )
        except Exception as exc:
            diagnostic = _base_diagnostic(
                endpoint_name=endpoint.name,
                endpoint_url_sanitized=sanitized_url,
                symbol=symbol,
                period=endpoint.period,
                credential_present=True,
            ) | {
                "http_status": 0,
                "authorized": False,
                "reachable": False,
                "status": "request_error",
                "message": _sanitize_text(str(exc), key),
                "warnings": [_sanitize_text("request failed before payload classification", key)],
            }
        result["endpoints"].append(_sanitize_value(diagnostic, key))
    return _sanitize_value(result, key)


def classify_fmp_endpoint_payload(
    *,
    endpoint_name: str,
    endpoint_url_sanitized: str,
    symbol: str,
    period: str | None,
    credential_present: bool,
    payload: Any,
    meta: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    record_keys = _record_keys(payload)
    date_fields = [field for field in DATE_FIELD_ORDER if field in record_keys]
    has_accepted_date = _has_any_key(record_keys, ("acceptedDate", "accepted_date", "acceptedDatetime", "accepted_datetime"))
    has_filing_date = _has_any_key(record_keys, ("filingDate", "fillingDate", "filing_date", "filling_date"))
    timestamp_safe_candidate = has_accepted_date or has_filing_date
    http_status = _http_status(meta)
    status = _status_from_http(http_status, payload)
    warnings = _warnings(
        status=status,
        record_keys=record_keys,
        timestamp_safe_candidate=timestamp_safe_candidate,
    )
    message = _message(payload, meta, api_key)

    return _base_diagnostic(
        endpoint_name=endpoint_name,
        endpoint_url_sanitized=_sanitize_url(endpoint_url_sanitized, api_key),
        symbol=symbol,
        period=period,
        credential_present=credential_present,
    ) | {
        "http_status": http_status,
        "authorized": status not in {"missing_api_key", "unauthorized", "request_error"},
        "reachable": http_status != 0,
        "status": status,
        "message": message,
        "payload_type": _payload_type(payload),
        "record_keys": record_keys,
        "date_fields_present": date_fields,
        "has_accepted_date": has_accepted_date,
        "has_filing_date": has_filing_date,
        "timestamp_safe_candidate": bool(timestamp_safe_candidate),
        "plan_limited": status == "plan_limited",
        "warnings": warnings,
    }


def _missing_key_diagnostic(endpoint: FmpEndpoint, symbol: str) -> dict[str, Any]:
    return _base_diagnostic(
        endpoint_name=endpoint.name,
        endpoint_url_sanitized=_sanitize_url(_endpoint_url(endpoint, symbol, "REDACTED"), ""),
        symbol=symbol,
        period=endpoint.period,
        credential_present=False,
    ) | {
        "http_status": None,
        "authorized": False,
        "reachable": False,
        "status": "missing_api_key",
        "message": "FMP_API_KEY is not present; network probe skipped",
        "payload_type": "none",
        "record_keys": [],
        "date_fields_present": [],
        "has_accepted_date": False,
        "has_filing_date": False,
        "timestamp_safe_candidate": False,
        "plan_limited": False,
        "warnings": ["missing FMP_API_KEY is non-fatal"],
    }


def _base_diagnostic(
    *,
    endpoint_name: str,
    endpoint_url_sanitized: str,
    symbol: str,
    period: str | None,
    credential_present: bool,
) -> dict[str, Any]:
    return {
        "provider": "FMP",
        "endpoint_name": endpoint_name,
        "endpoint_url_sanitized": endpoint_url_sanitized,
        "symbol": symbol,
        "period": period,
        "credential_present": credential_present,
    }


def _endpoint_url(endpoint: FmpEndpoint, symbol: str, api_key: str) -> str:
    query: dict[str, str | int] = {"symbol": symbol}
    if endpoint.period:
        query["period"] = endpoint.period
        query["limit"] = 1
    query["apikey"] = api_key
    return f"https://financialmodelingprep.com{endpoint.path}?{urllib.parse.urlencode(query)}"


def _first_symbol(symbols: list[str] | None) -> str:
    if not symbols:
        return "AAPL"
    for symbol in symbols:
        normalized = str(symbol).strip().upper()
        if normalized:
            return normalized
    return "AAPL"


def _download_json(url: str) -> tuple[Any, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 fmp-diagnostics", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return _loads_json(raw), {
                "http_status": int(getattr(response, "status", 200)),
                "content_type": str(response.headers.get("Content-Type", "")),
                "body_length": len(raw),
                "body_preview": raw[:300],
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return _loads_json(raw), {
            "http_status": int(exc.code),
            "content_type": str(exc.headers.get("Content-Type", "")),
            "body_length": len(raw),
            "body_preview": raw[:300],
        }


def _loads_json(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": True, "message": raw[:300]}


def _http_status(meta: dict[str, Any]) -> int:
    try:
        return int(meta.get("http_status") or 0)
    except (TypeError, ValueError):
        return 0


def _status_from_http(http_status: int, payload: Any) -> str:
    if http_status in {401, 403}:
        return "unauthorized"
    if http_status == 402:
        return "plan_limited"
    if http_status == 0:
        return "request_error"
    if 200 <= http_status < 300:
        return "provider_error" if _payload_has_error(payload) else "ok"
    return "unexpected_status"


def _payload_has_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return True
    status = str(payload.get("status") or "").lower()
    return status in {"error", "unauthorized", "forbidden"}


def _payload_type(payload: Any) -> str:
    if isinstance(payload, list):
        return "array"
    if isinstance(payload, dict):
        return "object"
    if payload is None:
        return "none"
    return type(payload).__name__


def _record_keys(payload: Any) -> list[str]:
    if isinstance(payload, list):
        if not payload or not isinstance(payload[0], dict):
            return []
        return [str(key) for key in payload[0].keys()]
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys()]
    return []


def _has_any_key(keys: list[str], candidates: tuple[str, ...]) -> bool:
    lowered = {key.lower() for key in keys}
    return any(candidate.lower() in lowered for candidate in candidates)


def _warnings(*, status: str, record_keys: list[str], timestamp_safe_candidate: bool) -> list[str]:
    warnings: list[str] = []
    if status == "plan_limited":
        warnings.append("endpoint is plan-limited for this credential; non-fatal")
    if status == "unauthorized":
        warnings.append("endpoint returned 401/403 authorization failure; non-fatal")
    if not timestamp_safe_candidate:
        warnings.append("no acceptedDate/filingDate/fillingDate availability field present")
        if "date" in record_keys:
            warnings.append("date alone is not an availability date")
        if any(field in record_keys for field in ("period", "fiscalYear", "calendarYear")):
            warnings.append("period/fiscalYear/calendarYear alone are not availability dates")
    return warnings


def _message(payload: Any, meta: dict[str, Any], api_key: str) -> str:
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error") or payload.get("status") or ""
        if message:
            return _sanitize_text(str(message), api_key)
    preview = str(meta.get("body_preview") or "")
    return _sanitize_text(preview[:300], api_key)


def _sanitize_url(url: str, api_key: str) -> str:
    if not url:
        return ""
    sanitized = re.sub(r"(?i)(apikey=)[^&]+", r"\1REDACTED", url)
    if api_key:
        sanitized = sanitized.replace(api_key, "REDACTED")
    return sanitized


def _sanitize_text(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "REDACTED")
    return re.sub(r"(?i)((?:api[_ -]?)?(?:token|key)\s*[=:]\s*)\S+", r"\1REDACTED", text)


def _sanitize_value(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value, api_key)
    if isinstance(value, list):
        return [_sanitize_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item, api_key) for key, item in value.items()}
    return value
