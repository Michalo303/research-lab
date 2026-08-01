from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from research_lab.research.massive_sec_filing_audit_v1 import (
    build_massive_sec_filing_audit_plan_v1,
    run_massive_sec_filing_audit_v1,
)


CLI_VERSION = "massive_sec_filing_audit_cli_result_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded independent SEC filing audit.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        request_path = Path(args.request)
        expected = _sha(args.expected_request_sha256)
        if not request_path.is_absolute() or request_path.is_symlink() or not request_path.is_file():
            raise ValueError("request path invalid")
        body = request_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            raise ValueError("request hash mismatch")
        request = json.loads(body.decode("utf-8"))
        plan = build_massive_sec_filing_audit_plan_v1(request)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _emit({"version": CLI_VERSION, "status": "REQUEST_VALIDATION_FAILED"}, 4)
    if not args.execute:
        return _emit({"version": CLI_VERSION, "status": "DRY_RUN", **plan}, 0)
    result = run_massive_sec_filing_audit_v1(request)
    return _emit(result, 0 if result.get("status") == "PASS" else 7)


def _sha(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError("SHA-256 invalid")
    return raw


def _emit(payload: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
