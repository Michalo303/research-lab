from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import (
    build_eodhd_us_equity_acquisition_plan_v1,
    run_eodhd_us_equity_universe_acquisition_v1,
)
from research_lab.research.eodhd_us_equity_universe_selection_v1 import (
    build_point_in_time_qlib_manifest_v1,
)


CLI_RESULT_VERSION = "eodhd_us_equity_universe_acquisition_cli_result_v1"
EXIT_SUCCESS = 0
EXIT_MISSING_KEY = 3
EXIT_VALIDATION = 4
EXIT_BUDGET = 5
EXIT_PROVIDER = 6
EXIT_IO = 7
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Acquire one immutable point-in-time EODHD US equity dataset."
    )
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        request_path = Path(args.request)
        if not request_path.is_absolute() or request_path.is_symlink() or not request_path.is_file():
            raise ValueError("request path is invalid")
        expected_sha256 = str(args.expected_request_sha256)
        if not _SHA_RE.fullmatch(expected_sha256):
            raise ValueError("request hash is invalid")
        request_bytes = request_path.read_bytes()
        if hashlib.sha256(request_bytes).hexdigest() != expected_sha256:
            raise ValueError("request hash mismatch")
        request = json.loads(request_bytes.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request is invalid")
        plan = build_eodhd_us_equity_acquisition_plan_v1(request)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _emit({"version": CLI_RESULT_VERSION, "status": "REQUEST_VALIDATION_FAILED"}, EXIT_VALIDATION)

    if not args.execute:
        return _emit(
            {
                "version": CLI_RESULT_VERSION,
                "status": "DRY_RUN",
                "acquisition_id": plan["acquisition_id"],
                "request_sha256": expected_sha256,
                "maximum_call_units": plan["maximum_call_units"],
                "planned_phases": [
                    "ACTIVE_COMMON_STOCKS",
                    "DELISTED_COMMON_STOCKS",
                    "SPY_SESSION_PROXY",
                    "MONTH_END_BULK",
                    "SYMBOL_HISTORY",
                    "POINT_IN_TIME_SELECTION",
                ],
                "planned_sealed_oos_rows": 0,
                "provider_call_units_used": 0,
            },
            EXIT_SUCCESS,
        )

    acquisition = run_eodhd_us_equity_universe_acquisition_v1(request)
    status = str(acquisition.get("status", "ACQUISITION_VALIDATION_FAILED"))
    if status != "DOWNLOAD_COMPLETE_PENDING_SELECTION":
        return _emit(
            {
                "version": CLI_RESULT_VERSION,
                "status": status,
                "provider_call_units_used": int(acquisition.get("provider_call_units_used", 0)),
                "provider_http_requests_used": int(acquisition.get("provider_http_requests_used", 0)),
            },
            _exit_for_status(status),
        )
    try:
        staging = Path(str(acquisition["staging_dir"])).resolve()
        state_path = staging / "state.sqlite"
        connection = sqlite3.connect(state_path)
        try:
            try:
                selection = build_point_in_time_qlib_manifest_v1(
                    staging_root=staging,
                    state_connection=connection,
                )
            except ValueError:
                return _emit(
                    {
                        "version": CLI_RESULT_VERSION,
                        "status": "POINT_IN_TIME_SELECTION_FAILED",
                        "provider_call_units_used": int(
                            acquisition.get("provider_call_units_used", 0)
                        ),
                        "provider_http_requests_used": int(
                            acquisition.get("provider_http_requests_used", 0)
                        ),
                    },
                    EXIT_VALIDATION,
                )
            raw_manifest = _build_raw_manifest(connection)
        finally:
            connection.close()
        result = _publish_complete_bundle(
            request=request,
            request_bytes=request_bytes,
            request_sha256=expected_sha256,
            plan=plan,
            acquisition=acquisition,
            selection=selection,
            raw_manifest=raw_manifest,
            staging=staging,
        )
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return _emit(
            {
                "version": CLI_RESULT_VERSION,
                "status": "FINALIZATION_FAILED",
                "provider_call_units_used": int(acquisition.get("provider_call_units_used", 0)),
                "provider_http_requests_used": int(acquisition.get("provider_http_requests_used", 0)),
            },
            EXIT_IO,
        )
    return _emit(result, EXIT_SUCCESS)


def _build_raw_manifest(connection: sqlite3.Connection) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT endpoint_identity, ordinal, kind, call_units, attempts, status, subject,
               raw_path, raw_sha256, normalized_path, normalized_sha256,
               response_bytes, row_count, rejected_row_count
        FROM requests ORDER BY ordinal
        """
    ).fetchall()
    records = []
    for row in rows:
        endpoint_identity = _validate_public_endpoint_identity(row[0])
        records.append(
            {
                "endpoint_identity": endpoint_identity,
                "ordinal": row[1],
                "kind": row[2],
                "call_units": row[3],
                "attempts": row[4],
                "status": row[5],
                "subject": row[6],
                "raw_path": row[7],
                "raw_sha256": row[8],
                "normalized_path": row[9],
                "normalized_sha256": row[10],
                "response_bytes": row[11],
                "row_count": row[12],
                "rejected_row_count": row[13],
            }
        )
    result: dict[str, object] = {
        "version": "eodhd_us_equity_raw_manifest_v1",
        "request_count": len(records),
        "records": records,
    }
    result["canonical_manifest_sha256"] = _canonical_sha256(result)
    return result


def _validate_public_endpoint_identity(raw: object) -> str:
    if not isinstance(raw, str):
        raise ValueError("endpoint identity is invalid")
    parsed = urllib.parse.urlparse(raw)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    credential_names = {"api_token", "api_key", "apikey", "authorization", "token"}
    if (
        parsed.scheme != "https"
        or parsed.hostname != "eodhd.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(key.casefold() in credential_names for key, _ in query)
    ):
        raise ValueError("endpoint identity contains credential or authority drift")
    return raw


def _publish_complete_bundle(
    *,
    request: dict[str, object],
    request_bytes: bytes,
    request_sha256: str,
    plan: dict[str, object],
    acquisition: dict[str, object],
    selection: dict[str, object],
    raw_manifest: dict[str, object],
    staging: Path,
) -> dict[str, object]:
    final = Path(str(request["output_dir"])).resolve()
    if final.exists() or final.is_symlink():
        raise ValueError("final output already exists")
    if not staging.is_dir() or staging.parent != final.parent:
        raise ValueError("staging identity is invalid")
    _write_verified(staging / "request.json", request_bytes)
    _write_json(staging / "acquisition_plan.json", plan)
    _write_json(staging / "raw_manifest.json", raw_manifest)
    final_result: dict[str, object] = {
        "version": CLI_RESULT_VERSION,
        "status": "COMPLETE",
        "acquisition_id": plan["acquisition_id"],
        "request_sha256": request_sha256,
        "dataset_manifest_sha256": selection["manifest_sha256"],
        "selected_instrument_count": selection["selected_instrument_count"],
        "minimum_development_cross_section": selection["minimum_development_cross_section"],
        "median_development_cross_section": selection["median_development_cross_section"],
        "provider_call_units_used": acquisition["provider_call_units_used"],
        "provider_http_requests_used": acquisition["provider_http_requests_used"],
        "sealed_oos_rows": selection["sealed_oos_rows"],
        "sealed_oos_opened": False,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
        "output_dir": str(final),
        "manifest_path": str(final / "dataset_manifest.json"),
    }
    final_result["canonical_result_sha256"] = _canonical_sha256(final_result)
    _write_json(staging / "result.json", final_result)
    checksums: dict[str, str] = {}
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.name in {"COMPLETE", "checksums.json"} or path.name.endswith(".tmp"):
            continue
        checksums[path.relative_to(staging).as_posix()] = _file_sha256(path)
    checksum_payload = {
        "version": "eodhd_us_equity_acquisition_checksums_v1",
        "files": checksums,
    }
    _write_json(staging / "checksums.json", checksum_payload)
    for relative, expected in checksums.items():
        if _file_sha256(staging / relative) != expected:
            raise OSError("checksum verification failed")
    _write_verified(
        staging / "COMPLETE",
        (json.dumps({"status": "COMPLETE"}, sort_keys=True) + "\n").encode("utf-8"),
    )
    os.replace(staging, final)
    return final_result


def _exit_for_status(status: str) -> int:
    if status == "EODHD_API_KEY_UNAVAILABLE":
        return EXIT_MISSING_KEY
    if status.startswith("CALL_BUDGET"):
        return EXIT_BUDGET
    if status in {"STAGING_IO_FAILED", "OUTPUT_ALREADY_EXISTS", "INSUFFICIENT_DISK_SPACE"}:
        return EXIT_IO
    if status in {
        "PROVIDER_RESPONSE_INVALID",
        "PROVIDER_RESPONSE_CONTAINED_SECRET",
        "RETRYABLE_PROVIDER_FAILURE_EXHAUSTED",
        "ACQUISITION_VALIDATION_FAILED",
        "UNRESOLVED_IDENTITY_FAILURE_LIMIT_EXCEEDED",
    }:
        return EXIT_PROVIDER
    return EXIT_VALIDATION


def _write_verified(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    if temporary.read_bytes() != body:
        raise OSError("artifact verification failed")
    os.replace(temporary, path)
    return hashlib.sha256(body).hexdigest()


def _write_json(path: Path, payload: object) -> str:
    body = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return _write_verified(path, body)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _emit(payload: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
