from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from research_lab.research.massive_fundamental_acquisition_v1 import (
    build_massive_fundamental_acquisition_plan_v1,
    run_massive_fundamental_acquisition_v1,
    verify_massive_fundamental_bundle_v1,
)


CLI_VERSION = "massive_fundamental_acquisition_cli_result_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire the bounded Massive fundamental snapshot.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and args.verify:
        return _emit({"version": CLI_VERSION, "status": "REQUEST_VALIDATION_FAILED"}, 4)
    try:
        path = Path(args.request)
        expected = _sha(args.expected_request_sha256)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("request path invalid")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            raise ValueError("request hash mismatch")
        request = json.loads(body.decode("utf-8"))
        plan = build_massive_fundamental_acquisition_plan_v1(request)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _emit({"version": CLI_VERSION, "status": "REQUEST_VALIDATION_FAILED"}, 4)
    if args.verify:
        verification = verify_massive_fundamental_bundle_v1(str(request["output_dir"]))
        return _emit(verification, 0 if verification["status"] == "PASS" else 7)
    if not args.execute:
        return _emit(
            {
                "version": CLI_VERSION,
                "status": "DRY_RUN",
                "acquisition_id": plan["acquisition_id"],
                "request_sha256": expected,
                "subject_count": plan["subject_count"],
                "maximum_call_units": plan["maximum_call_units"],
                "minimum_request_interval_seconds": plan["minimum_request_interval_seconds"],
                "provider_calls_used": 0,
                "sealed_oos_opened": False,
            },
            0,
        )
    result = run_massive_fundamental_acquisition_v1(request)
    status = str(result.get("status", "ACQUISITION_RUNTIME_FAILED"))
    exit_code = 0 if status == "COMPLETE" else 3 if status == "MASSIVE_API_KEY_UNAVAILABLE" else 6 if status == "PROVIDER_ACQUISITION_INCOMPLETE" else 7
    return _emit(result, exit_code)


def _sha(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError("SHA-256 invalid")
    return raw


def _emit(payload: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
