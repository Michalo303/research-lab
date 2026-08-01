from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from research_lab.research.massive_fundamental_edge_discovery_v1 import (
    build_massive_fundamental_edge_discovery_plan_v1,
    run_massive_fundamental_edge_discovery_v1,
)


CLI_VERSION = "massive_fundamental_edge_discovery_cli_result_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded offline fundamental edge program.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        request_path = Path(args.request)
        expected = _sha(args.expected_request_sha256)
        if not request_path.is_absolute() or request_path.is_symlink() or not request_path.is_file():
            raise ValueError("request path invalid")
        request_bytes = request_path.read_bytes()
        if hashlib.sha256(request_bytes).hexdigest() != expected:
            raise ValueError("request hash mismatch")
        request = json.loads(request_bytes.decode("utf-8"))
        plan = build_massive_fundamental_edge_discovery_plan_v1(request)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _emit({"version": CLI_VERSION, "status": "REQUEST_VALIDATION_FAILED"}, 4)
    if not args.execute:
        return _emit({"version": CLI_VERSION, "status": "DRY_RUN", **plan}, 0)
    try:
        result = run_massive_fundamental_edge_discovery_v1(request)
        public_result = _publish_result_bundle(request, request_bytes, result)
    except (OSError, ValueError, TypeError, KeyError):
        return _emit({"version": CLI_VERSION, "status": "EXECUTION_OR_PUBLICATION_FAILED"}, 7)
    status = str(public_result["status"])
    exit_code = 0 if status in {"FUNDAMENTAL_EDGE_CANDIDATE_FOUND", "NO_FUNDAMENTAL_EDGE"} else 8 if status == "FUNDAMENTAL_COVERAGE_INSUFFICIENT" else 5
    return _emit(public_result, exit_code)


def _publish_result_bundle(
    request: dict[str, object],
    request_bytes: bytes,
    result: dict[str, object],
) -> dict[str, object]:
    output = Path(str(request["output_dir"]))
    if output.exists() or output.is_symlink():
        raise ValueError("output exists")
    staging = output.with_name(f".{output.name}-{str(result['canonical_result_sha256'])[:12]}.partial")
    if staging.exists() or staging.is_symlink():
        raise ValueError("staging exists")
    staging.mkdir(parents=True)
    _write(staging / "request.json", request_bytes)
    _write_json(staging / "result.json", result)
    _write_json(staging / "updated_ledger.json", result["updated_ledger"])
    if "economic_scorecard" in result:
        _write_json(staging / "economic_scorecard.json", result["economic_scorecard"])
    if "fundamental_screen" in result:
        _write_json(staging / "fundamental_screen.json", result["fundamental_screen"])
    records = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and path.name not in {"checksums.json", "COMPLETE"}:
            records.append({"path": path.relative_to(staging).as_posix(), "sha256": _file_sha(path)})
    checksums: dict[str, object] = {
        "version": "massive_fundamental_edge_discovery_checksums_v1",
        "record_count": len(records),
        "records": records,
    }
    checksums["canonical_checksums_sha256"] = _canonical_sha(checksums)
    _write_json(staging / "checksums.json", checksums)
    _write(staging / "COMPLETE", (str(result["canonical_result_sha256"]) + "\n").encode("ascii"))
    staging.replace(output)
    return {
        "version": CLI_VERSION,
        "status": result["status"],
        "pilot_id": result["pilot_id"],
        "canonical_result_sha256": result["canonical_result_sha256"],
        "new_trial_count": result["new_trial_count"],
        "new_hypothesis_count": result["new_hypothesis_count"],
        "accounting_complete": result["accounting_complete"],
        "provider_calls_used": result["provider_calls_used"],
        "sealed_oos_opened": result["sealed_oos_opened"],
        "output_dir": str(output),
    }


def _write_json(path: Path, value: object) -> str:
    return _write(path, (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"))


def _write(path: Path, body: bytes) -> str:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    if temporary.read_bytes() != body:
        raise OSError("artifact verification failed")
    temporary.replace(path)
    return hashlib.sha256(body).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _sha(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError("SHA-256 invalid")
    return raw


def _emit(payload: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
