from __future__ import annotations

import hashlib
import json
import math
import copy
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)


REQUEST_VERSION = "local_ohlcv_file_input_adapter_request_v1"
RESULT_VERSION = "local_ohlcv_file_input_adapter_result_v1"
ADAPTER_VERSION = "local_ohlcv_file_input_adapter_v1"
SUPPORTED_FORMATS = {"json", "jsonl", "csv"}
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_ROW_FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}


def build_local_ohlcv_file_input_adapter(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    source_path = validated["source_path"]
    source_sha256 = _file_sha256(source_path)
    if validated["expected_sha256"] is not None and validated["expected_sha256"] != source_sha256:
        raise ValueError("expected_sha256 does not match the source file.")

    parsed = _load_rows_from_file(
        source_path,
        format_name=validated["format"],
        dataset_id=validated["dataset_id"],
        symbol=validated["symbol"],
        exchange=validated["exchange"],
        timezone_name=validated["timezone"],
    )
    if validated["format"] == "csv":
        return {
            "version": RESULT_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "status": "UNSUPPORTED_FORMAT",
            "dataset_id": validated["dataset_id"],
            "symbol": validated["symbol"],
            "source_file_identity": _source_file_identity(source_path),
            "source_sha256": source_sha256,
            "format": validated["format"],
            "row_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "normalized_rows_hash": None,
            "normalized_bars": None,
            "downstream_adapter_result": None,
            "provenance": validated["provenance"],
            "source_modified": False,
            "network_used": False,
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "production_runtime_supported": False,
        }

    normalized_rows = _normalize_rows(
        parsed["rows"],
        timezone_name=parsed["timezone"] or validated["timezone"],
        max_rows=validated["max_rows"],
    )
    normalized_rows_hash = _canonical_sha256(normalized_rows)
    downstream_adapter_result = build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": validated["symbol"],
            "input_bars": normalized_rows,
            "provenance": {
                **validated["provenance"],
                "dataset_id": validated["dataset_id"],
                "source_file_sha256": source_sha256,
                "input_format": validated["format"],
            },
        }
    )
    final_source_sha256 = _file_sha256(source_path)
    if final_source_sha256 != source_sha256:
        raise ValueError("source file changed during processing.")

    return {
        "version": RESULT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "status": "SUCCESS",
        "dataset_id": validated["dataset_id"],
        "symbol": validated["symbol"],
        "source_file_identity": _source_file_identity(source_path),
        "source_sha256": source_sha256,
        "format": validated["format"],
        "row_count": len(normalized_rows),
        "first_timestamp": normalized_rows[0]["timestamp"],
        "last_timestamp": normalized_rows[-1]["timestamp"],
        "normalized_rows_hash": normalized_rows_hash,
        "normalized_bars": copy.deepcopy(normalized_rows),
        "downstream_adapter_result": downstream_adapter_result,
        "provenance": validated["provenance"],
        "source_modified": False,
        "network_used": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
    }


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "file_path",
            "format",
            "dataset_id",
            "symbol",
            "exchange",
            "timezone",
            "expected_sha256",
            "max_bytes",
            "max_rows",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    format_name = _required_text(payload, "format").lower()
    if format_name not in SUPPORTED_FORMATS:
        raise ValueError("format must be one of json, jsonl, csv.")
    source_path = _validated_source_path(_required_text(payload, "file_path"))
    if source_path.stat().st_size > _required_positive_int(payload, "max_bytes"):
        raise ValueError("source file exceeds max_bytes.")
    expected_sha256 = payload.get("expected_sha256")
    if expected_sha256 is not None:
        expected_sha256 = _required_sha256(expected_sha256, name="expected_sha256")
    timezone_name = payload.get("timezone")
    if timezone_name is not None:
        timezone_name = _validated_timezone(timezone_name, name="timezone")
    exchange = payload.get("exchange")
    if exchange is not None:
        exchange = _required_text(payload, "exchange")
    return {
        "version": version,
        "source_path": source_path,
        "format": format_name,
        "dataset_id": _required_text(payload, "dataset_id"),
        "symbol": _required_text(payload, "symbol").upper(),
        "exchange": exchange,
        "timezone": timezone_name,
        "expected_sha256": expected_sha256,
        "max_bytes": _required_positive_int(payload, "max_bytes"),
        "max_rows": _required_positive_int(payload, "max_rows"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _load_rows_from_file(
    source_path: Path,
    *,
    format_name: str,
    dataset_id: str,
    symbol: str,
    exchange: str | None,
    timezone_name: str | None,
) -> dict[str, Any]:
    if format_name == "json":
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {
                "rows": [_validate_row_object(item) for item in payload],
                "timezone": timezone_name,
            }
        envelope = _required_mapping(payload, name="json_payload")
        _reject_unknown_fields(
            envelope,
            allowed={"dataset_id", "symbol", "exchange", "timezone", "rows"},
            name="json_payload",
        )
        if _required_text(envelope, "dataset_id") != dataset_id:
            raise ValueError("dataset identity mismatch between request and source file.")
        if _required_text(envelope, "symbol").upper() != symbol:
            raise ValueError("symbol identity mismatch between request and source file.")
        if "exchange" in envelope and exchange is not None and _required_text(envelope, "exchange") != exchange:
            raise ValueError("exchange identity mismatch between request and source file.")
        file_timezone = timezone_name
        if "timezone" in envelope:
            file_timezone = _validated_timezone(envelope["timezone"], name="json_payload.timezone")
            if timezone_name is not None and file_timezone != timezone_name:
                raise ValueError("timezone identity mismatch between request and source file.")
        return {
            "rows": [_validate_row_object(item) for item in _required_list(envelope.get("rows"), name="json_payload.rows")],
            "timezone": file_timezone,
        }
    if format_name == "jsonl":
        rows: list[dict[str, Any]] = []
        for line in source_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(_validate_row_object(json.loads(line)))
        if not rows:
            raise ValueError("jsonl source must contain at least one row.")
        return {
            "rows": rows,
            "timezone": timezone_name,
        }
    return {
        "rows": [],
        "timezone": timezone_name,
    }


def _normalize_rows(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str | None,
    max_rows: int,
) -> list[dict[str, Any]]:
    if len(rows) > max_rows:
        raise ValueError("source rows exceed max_rows.")
    normalized: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    seen_timestamps: set[str] = set()
    for row in rows:
        timestamp = _normalize_timestamp(_required_text(row, "timestamp"), timezone_name=timezone_name)
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("timestamps must be strictly ordered in the source file.")
        if timestamp in seen_timestamps:
            raise ValueError("duplicate timestamp is not allowed.")
        open_price = _required_finite_number(row, "open")
        high_price = _required_finite_number(row, "high")
        low_price = _required_finite_number(row, "low")
        close_price = _required_finite_number(row, "close")
        volume = _required_finite_number(row, "volume")
        if high_price < low_price:
            raise ValueError("high must be greater than or equal to low.")
        if high_price < open_price:
            raise ValueError("high must be greater than or equal to open.")
        if high_price < close_price:
            raise ValueError("high must be greater than or equal to close.")
        if low_price > open_price:
            raise ValueError("low must be less than or equal to open.")
        if low_price > close_price:
            raise ValueError("low must be less than or equal to close.")
        normalized.append(
            {
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
        seen_timestamps.add(timestamp)
        previous_timestamp = timestamp
    return normalized


def _normalize_timestamp(raw: str, *, timezone_name: str | None) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("timestamp must be non-empty text.")
    if "T" not in text and " " not in text:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("invalid timestamp.") from exc
        base = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
        if timezone_name is None:
            aware = base.replace(tzinfo=timezone.utc)
        else:
            aware = base.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
        return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise ValueError("invalid timestamp.") from exc
    if parsed.tzinfo is None:
        if timezone_name is None:
            raise ValueError("timezone-naive timestamps require an explicit timezone.")
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validated_source_path(raw_path: str) -> Path:
    stripped = raw_path.strip()
    if stripped.startswith("\\\\") or stripped.startswith("//"):
        raise ValueError("network paths are not allowed.")
    if "://" in stripped or _has_uri_scheme(stripped):
        raise ValueError("URI or URL file paths are not allowed.")
    path = Path(stripped).expanduser()
    if not path.is_absolute():
        raise ValueError("file_path must be an absolute local path.")
    if path.is_symlink():
        raise ValueError("symlink sources are not allowed.")
    if not path.exists():
        raise ValueError("source file does not exist.")
    if not path.is_file():
        raise ValueError("source path must be a regular file.")
    return path.resolve()


def _has_uri_scheme(path_text: str) -> bool:
    match = _SCHEME_RE.match(path_text)
    if match is None:
        return False
    scheme = match.group(0)
    if len(scheme) == 2 and scheme[0].isalpha() and scheme[1] == ":":
        remainder = path_text[2:]
        return not remainder or remainder[0] not in ("\\", "/")
    return True


def _validate_row_object(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="row")
    _reject_unknown_fields(payload, allowed=_ROW_FIELDS, name="row")
    for field in _ROW_FIELDS:
        if field not in payload:
            raise ValueError(f"missing required field: {field}")
    return payload


def _source_file_identity(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path),
        "file_name": path.name,
        "size_bytes": stat.st_size,
        "is_symlink": False,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


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


def _required_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value.strip()):
        raise ValueError(f"{name} must be a lowercase sha256 hex digest.")
    return value.strip()


def _validated_timezone(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"{name} must be a valid IANA timezone.") from exc
    return timezone_name


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
