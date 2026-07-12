from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REQUEST_VERSION = "bounded_eodhd_ohlcv_snapshot_request_v1"
RESULT_VERSION = "bounded_eodhd_ohlcv_snapshot_result_v1"
SNAPSHOT_VERSION = "bounded_eodhd_ohlcv_snapshot_v1"
DATASET_ID = "eodhd-spy-us-daily-2015-2026-v1"
OUTPUT_FILES = (
    "acquisition_request.json",
    "raw_response.json",
    "normalized_ohlcv.json",
    "metadata.json",
    "checksums.json",
)
EXIT_VALIDATION_FAILURE = 2
EXIT_IO_FAILURE = 3
EXIT_OUTPUT_EXISTS = 4

HttpGet = Callable[[str], tuple[Any, dict[str, Any]]]


def acquire_bounded_eodhd_ohlcv_snapshot(
    request: dict[str, object],
    *,
    api_key: str | None = None,
    http_get: Callable[..., tuple[Any, dict[str, Any]]] | None = None,
    retrieval_utc: datetime | None = None,
) -> dict[str, Any]:
    validated = _validate_request(request)
    output_dir = validated["output_dir"]
    _validate_output_dir(output_dir)
    key = (api_key or _load_eodhd_api_key()).strip()
    if not key:
        raise ValueError("EODHD_API_KEY is required.")

    request_url = _request_url(validated, api_key=key)
    getter = http_get or _http_get_json
    payload, meta = getter(
        request_url,
        timeout_seconds=validated["timeout_seconds"],
        max_response_bytes=validated["max_response_bytes"],
        headers={"User-Agent": "research-lab/0.1 research-only"},
    )
    normalized_payload, metadata = _validated_provider_payload(
        payload,
        meta=meta,
        validated=validated,
        request_url=request_url,
        retrieval_utc=retrieval_utc or datetime.now(timezone.utc),
    )
    artifacts = _build_artifacts(validated=validated, payload=normalized_payload, metadata=metadata)
    checksums = _write_snapshot_artifacts(output_dir=output_dir, artifacts=artifacts)

    hashable_validated = {**validated, "output_dir": str(validated["output_dir"])}
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "status": "SUCCESS",
        "provider": validated["provider"],
        "symbol": validated["symbol"],
        "interval": validated["interval"],
        "start_date": validated["start_date"],
        "end_date": validated["end_date"],
        "output_dir": str(output_dir),
        "written_files": list(OUTPUT_FILES) + ["COMPLETE"],
        "checksums": checksums["files"],
        "provider_calls_used": 1,
        "network_used": True,
        "registry_write_performed": False,
        "hermes_state_touched": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    result["input_sha256"] = _canonical_sha256(hashable_validated)
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire one bounded immutable EODHD OHLCV snapshot into an explicit isolated directory.")
    parser.add_argument("--request", required=True, help="Absolute path to the bounded EODHD snapshot request JSON.")
    args = parser.parse_args(argv)

    try:
        request = _load_json_object(_resolved_input_file(args.request))
        result = acquire_bounded_eodhd_ohlcv_snapshot(request)
    except FileExistsError:
        return _emit_failure("output_already_exists", EXIT_OUTPUT_EXISTS)
    except (OSError, json.JSONDecodeError):
        return _emit_failure("io_failure", EXIT_IO_FAILURE)
    except ValueError as exc:
        return _emit_failure(str(exc), EXIT_VALIDATION_FAILURE)

    print(json.dumps(result, sort_keys=True))
    return 0


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    allowed = {
        "version",
        "provider",
        "symbol",
        "interval",
        "start_date",
        "end_date",
        "output_dir",
        "approved_host",
        "timeout_seconds",
        "max_response_bytes",
        "live_access",
        "provenance",
    }
    _reject_unknown_fields(payload, allowed=allowed, name="request")
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if _required_text(payload, "provider") != "EODHD":
        raise ValueError("provider must be EODHD.")
    if _required_text(payload, "symbol") != "SPY.US":
        raise ValueError("symbol must be SPY.US.")
    if _required_text(payload, "interval") != "daily":
        raise ValueError("interval must be daily.")
    start_date = _required_text(payload, "start_date")
    end_date = _required_text(payload, "end_date")
    if start_date != "2015-01-01" or end_date != "2026-06-30":
        raise ValueError("approved range is fixed to 2015-01-01 through 2026-06-30.")
    if payload.get("live_access") is not True:
        raise ValueError("live_access must be true for an explicit bounded acquisition.")
    approved_host = _required_text(payload, "approved_host")
    if approved_host != "eodhd.com":
        raise ValueError("approved_host must be eodhd.com.")
    return {
        "version": payload["version"],
        "provider": payload["provider"],
        "symbol": payload["symbol"],
        "interval": payload["interval"],
        "start_date": start_date,
        "end_date": end_date,
        "output_dir": Path(_required_text(payload, "output_dir")).expanduser(),
        "approved_host": approved_host,
        "timeout_seconds": _required_positive_int(payload, "timeout_seconds"),
        "max_response_bytes": _required_positive_int(payload, "max_response_bytes"),
        "live_access": True,
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_output_dir(output_dir: Path) -> None:
    if any(part == ".." for part in output_dir.parts):
        raise ValueError("unsafe_output_dir: parent-directory traversal is forbidden.")
    if not output_dir.is_absolute():
        raise ValueError("output_dir must be an absolute path.")
    resolved = output_dir.resolve(strict=False)
    protected_roots = [
        Path(__file__).resolve().parents[2],
        Path("/opt/trading/research-lab"),
        Path("/opt/trading/private/hermes_books"),
        Path.home() / "AppData" / "Local" / "hermes",
    ]
    for root in protected_roots:
        normalized_root = root.resolve(strict=False)
        try:
            resolved.relative_to(normalized_root)
        except ValueError:
            continue
        raise ValueError("unsafe_output_dir: output directory is inside a protected path.")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError("output_dir must be a directory path.")
        if any(output_dir.iterdir()):
            raise ValueError("output_dir must be empty or absent.")


def _request_url(validated: dict[str, Any], *, api_key: str) -> str:
    query = urllib.parse.urlencode(
        {
            "api_token": api_key,
            "fmt": "json",
            "from": validated["start_date"],
            "to": validated["end_date"],
            "period": "d",
        }
    )
    return f"https://{validated['approved_host']}/api/eod/{urllib.parse.quote(validated['symbol'])}?{query}"


def _validated_provider_payload(
    payload: Any,
    *,
    meta: dict[str, Any],
    validated: dict[str, Any],
    request_url: str,
    retrieval_utc: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final_url = str(meta.get("final_url") or request_url)
    parsed_final_url = urllib.parse.urlparse(final_url)
    if parsed_final_url.scheme != "https":
        raise ValueError("provider response must use HTTPS.")
    if parsed_final_url.hostname != validated["approved_host"]:
        raise ValueError("provider response must remain on the approved host.")
    if parsed_final_url.path != f"/api/eod/{validated['symbol']}":
        raise ValueError("provider response symbol does not match the approved symbol.")
    body_text = str(meta.get("body_text") or json.dumps(payload, sort_keys=True, separators=(",", ":")))
    body_length = int(meta.get("body_length") or len(body_text))
    if body_length > validated["max_response_bytes"]:
        raise ValueError("response exceeds max_response_bytes.")
    http_status = int(meta.get("http_status") or 0)
    if http_status != 200:
        raise ValueError(f"unexpected HTTP status: {http_status}")
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError("API error response is not accepted.")
    if not isinstance(payload, list) or not payload:
        raise ValueError("provider response must be a non-empty JSON list.")
    normalized_rows = _validate_rows(payload, start_date=validated["start_date"], end_date=validated["end_date"])
    metadata = {
        "provider": validated["provider"],
        "symbol": validated["symbol"],
        "interval": validated["interval"],
        "requested_start_date": validated["start_date"],
        "requested_end_date": validated["end_date"],
        "retrieval_timestamp": retrieval_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sanitized_endpoint_identity": f"https://{validated['approved_host']}/api/eod/{validated['symbol']}",
        "http_status": http_status,
        "response_byte_size": body_length,
        "raw_response_sha256": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
    }
    return normalized_rows, metadata


def _validate_rows(rows: list[Any], *, start_date: str, end_date: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    previous_date: str | None = None
    seen_dates: set[str] = set()
    for item in rows:
        row = _required_mapping(item, name="provider row")
        allowed = {"date", "open", "high", "low", "close", "volume", "adjusted_close"}
        _reject_unknown_fields(row, allowed=allowed, name="provider row")
        trade_date = _required_text(row, "date")
        if trade_date < start_date or trade_date > end_date:
            raise ValueError("provider row date exceeds the approved range.")
        if trade_date in seen_dates:
            raise ValueError("provider rows contain duplicate timestamps.")
        if previous_date is not None and trade_date <= previous_date:
            raise ValueError("provider rows must remain strictly ordered.")
        open_price = _required_finite_number(row, "open")
        high_price = _required_finite_number(row, "high")
        low_price = _required_finite_number(row, "low")
        close_price = _required_finite_number(row, "close")
        volume = _required_finite_number(row, "volume")
        if volume <= 0:
            raise ValueError("volume must be positive.")
        if high_price < max(open_price, low_price, close_price):
            raise ValueError("high must be greater than or equal to open, low, and close.")
        if low_price > min(open_price, high_price, close_price):
            raise ValueError("low must be less than or equal to open, high, and close.")
        normalized.append(
            {
                "timestamp": trade_date,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
        previous_date = trade_date
        seen_dates.add(trade_date)
    return normalized


def _build_artifacts(*, validated: dict[str, Any], payload: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    acquisition_request = {
        "version": REQUEST_VERSION,
        "provider": validated["provider"],
        "symbol": validated["symbol"],
        "interval": validated["interval"],
        "start_date": validated["start_date"],
        "end_date": validated["end_date"],
        "approved_host": validated["approved_host"],
        "timeout_seconds": validated["timeout_seconds"],
        "max_response_bytes": validated["max_response_bytes"],
        "live_access": True,
        "provenance": validated["provenance"],
    }
    normalized_ohlcv = {
        "dataset_id": DATASET_ID,
        "symbol": validated["symbol"],
        "rows": payload,
    }
    raw_response = payload
    return {
        "acquisition_request.json": acquisition_request,
        "raw_response.json": raw_response,
        "normalized_ohlcv.json": normalized_ohlcv,
        "metadata.json": metadata,
    }


def _write_snapshot_artifacts(*, output_dir: Path, artifacts: dict[str, dict[str, Any] | list[dict[str, Any]]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    staged_hashes: dict[str, str] = {}
    for file_name in ("acquisition_request.json", "raw_response.json", "normalized_ohlcv.json", "metadata.json"):
        staged_hashes[file_name] = _write_verified_json(output_dir / file_name, artifacts[file_name])
    checksums = {
        "version": "bounded_eodhd_ohlcv_snapshot_checksums_v1",
        "files": dict(sorted(staged_hashes.items())),
    }
    staged_hashes["checksums.json"] = _write_verified_json(output_dir / "checksums.json", checksums)
    complete_path = output_dir / "COMPLETE"
    complete_path.write_text(json.dumps({"status": "COMPLETE"}, sort_keys=True) + "\n", encoding="utf-8")
    if json.loads(complete_path.read_text(encoding="utf-8"))["status"] != "COMPLETE":
        raise OSError("COMPLETE verification failed.")
    return {
        "version": checksums["version"],
        "files": {**checksums["files"], "checksums.json": staged_hashes["checksums.json"]},
    }


def _write_verified_json(path: Path, payload: Any) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    expected_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(encoded, encoding="utf-8")
    os.replace(temp_path, path)
    observed = json.loads(path.read_text(encoding="utf-8"))
    observed_sha256 = hashlib.sha256(
        json.dumps(observed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    if observed_sha256 != expected_sha256:
        raise OSError(f"post-write verification failed for {path.name}")
    return observed_sha256


def _http_get_json(url: str, *, timeout_seconds: int, max_response_bytes: int, headers: dict[str, str]) -> tuple[Any, dict[str, Any]]:
    request = urllib.request.Request(url, headers=headers)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("redirects are forbidden for bounded EODHD snapshot acquisition.")

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            raw_bytes = response.read(max_response_bytes + 1)
            if len(raw_bytes) > max_response_bytes:
                raise ValueError("response exceeds max_response_bytes.")
            body_text = raw_bytes.decode("utf-8", errors="replace")
            return json.loads(body_text), {
                "http_status": int(getattr(response, "status", 200)),
                "content_type": str(response.headers.get("Content-Type", "")),
                "body_length": len(raw_bytes),
                "body_text": body_text,
                "final_url": final_url,
            }
    except urllib.error.HTTPError as exc:
        raw_bytes = exc.read(max_response_bytes + 1)
        if len(raw_bytes) > max_response_bytes:
            raise ValueError("response exceeds max_response_bytes.") from exc
        body_text = raw_bytes.decode("utf-8", errors="replace")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            payload = {"error": True, "message": body_text[:300]}
        return payload, {
            "http_status": int(exc.code),
            "content_type": str(exc.headers.get("Content-Type", "")),
            "body_length": len(raw_bytes),
            "body_text": body_text,
            "final_url": url,
        }


def _load_eodhd_api_key() -> str:
    return os.getenv("EODHD_API_KEY", "").strip()


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _resolved_input_file(path_like: str | Path) -> Path:
    path = Path(path_like).expanduser()
    if not path.is_absolute():
        raise ValueError("request path must be absolute.")
    if not path.exists() or not path.is_file():
        raise ValueError("request path must point to an existing JSON file.")
    return path.resolve()


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


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
    return number


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, item in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        if item is None or isinstance(item, (str, int, float, bool)):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"provenance.{key_name} must be finite.")
            normalized[key_name] = item
            continue
        raise ValueError(f"provenance.{key_name} must be a JSON scalar.")
    return normalized


def _emit_failure(failure_reason: str, exit_code: int) -> int:
    payload = {
        "version": RESULT_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "status": "FAILED",
        "failure_reason": failure_reason,
        "production_runtime_supported": False,
    }
    print(json.dumps(payload, sort_keys=True))
    return exit_code
