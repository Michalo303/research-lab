from __future__ import annotations

import hashlib
import json
import math
from typing import Any


REQUEST_VERSION = "isolated_real_data_adapter_contract_request_v1"
RESULT_VERSION = "isolated_real_data_adapter_contract_result_v1"
ADAPTER_VERSION = "isolated_real_data_adapter_contract_v1"


def build_isolated_real_data_adapter_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    source_symbol = validated["source_symbol"]
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "symbol": f"SYNTH_{source_symbol}",
        "source_symbol": source_symbol,
        "synthetic_bars": validated["synthetic_bars"],
        "synthetic_data_used": False,
        "real_data_used": True,
        "production_runtime_supported": False,
        "supported_for_risk_overlay_execution": False,
        "safe_flags": {
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_gate_run": False,
            "hermes_state_touched": False,
            "hetzner_state_touched": False,
            "promotion_performed": False,
            "backtest_run": False,
        },
        "input_sha256": input_sha256,
        "provenance": {
            **validated["provenance"],
            "adapter_input_mode": "local_pre_supplied_bars",
            "provider_fetch_performed": False,
            "source_symbol": source_symbol,
        },
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "symbol", "input_bars", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    source_symbol = _required_text(payload, "symbol").upper()
    if source_symbol.startswith("SYNTH_"):
        source_symbol = source_symbol.removeprefix("SYNTH_")
    if not source_symbol:
        raise ValueError("symbol must not be empty.")
    return {
        "version": version,
        "source_symbol": source_symbol,
        "synthetic_bars": _validate_input_bars(payload.get("input_bars")),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_input_bars(value: Any) -> list[dict[str, float | str]]:
    bars = _required_list(value, name="input_bars")
    normalized: list[dict[str, float | str]] = []
    previous_timestamp: str | None = None
    for item in bars:
        payload = _required_mapping(item, name="input bar")
        _reject_unknown_fields(
            payload,
            allowed={"timestamp", "open", "high", "low", "close", "volume"},
            name="input bar",
        )
        timestamp = _required_text(payload, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("input_bars timestamps must be strictly increasing.")
        open_price = _required_finite_number(payload, "open")
        high_price = _required_finite_number(payload, "high")
        low_price = _required_finite_number(payload, "low")
        close_price = _required_finite_number(payload, "close")
        if high_price < max(open_price, low_price, close_price):
            raise ValueError("high must be greater than or equal to open, low, and close.")
        if low_price > min(open_price, high_price, close_price):
            raise ValueError("low must be less than or equal to open, high, and close.")
        normalized.append(
            {
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
        previous_timestamp = timestamp
    return normalized


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    if number <= 0:
        raise ValueError(f"{field} must be positive.")
    return number


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
