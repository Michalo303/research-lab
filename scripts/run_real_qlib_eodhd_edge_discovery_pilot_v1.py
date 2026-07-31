from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from research_lab.research.real_qlib_eodhd_edge_discovery_pilot_v1 import (
    run_real_qlib_eodhd_edge_discovery_pilot_v1,
)


EXIT_EDGE_FOUND = 0
EXIT_NO_EDGE = 2
EXIT_QLIB_UNAVAILABLE = 3
EXIT_VALIDATION_OR_LEDGER_FAILURE = 4
EXIT_IO_FAILURE = 5

_JSON_ARTIFACTS = {
    "request.json": "request",
    "dataset_metadata.json": "dataset_metadata",
    "qlib_runtime.json": "qlib_runtime",
    "factor_screen.json": "factor_screen",
    "reference_parity.json": "reference_parity",
    "economic_scorecard.json": "economic_scorecard",
    "updated_ledger.json": "updated_ledger",
    "result.json": "result",
}


def main(
    argv: Sequence[str] | None = None,
    *,
    pilot_runner: Callable[[dict[str, object]], dict[str, object]] = run_real_qlib_eodhd_edge_discovery_pilot_v1,
) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _read_request(args.request, args.expected_request_sha256)
        pilot_id = request.get("pilot_id")
        if not isinstance(pilot_id, str) or not pilot_id:
            raise ValueError("request pilot_id is invalid.")
        if not args.execute:
            print("status=DRY_RUN")
            print(f"pilot_id={pilot_id}")
            print("planned_factor_count=8")
            print("planned_provider_calls=0")
            print("planned_sealed_oos_reads=0")
            return EXIT_EDGE_FOUND
        output_dir = _validate_output_path(request.get("output_dir"))
        result = pilot_runner(request)
        if not isinstance(result, dict):
            raise ValueError("pilot result is invalid.")
        status = result.get("status")
        if status == "QLIB_RUNTIME_UNAVAILABLE":
            print("reason=QLIB_RUNTIME_UNAVAILABLE")
            return EXIT_QLIB_UNAVAILABLE
        if status not in {"EDGE_CANDIDATE_FOUND", "NO_PRICE_VOLUME_EDGE"}:
            print("reason=VALIDATION_OR_LEDGER_FAILURE")
            return EXIT_VALIDATION_OR_LEDGER_FAILURE
        _write_bundle_atomic(request=request, result=result, output_dir=output_dir)
        print(f"status={status}")
        return EXIT_EDGE_FOUND if status == "EDGE_CANDIDATE_FOUND" else EXIT_NO_EDGE
    except ValueError:
        print("reason=VALIDATION_OR_LEDGER_FAILURE")
        return EXIT_VALIDATION_OR_LEDGER_FAILURE
    except OSError:
        print("reason=IO_FAILURE")
        return EXIT_IO_FAILURE


def _read_request(path_text: str, expected_sha256: str) -> dict[str, object]:
    request_path = Path(path_text)
    if not request_path.is_absolute() or request_path.is_symlink() or not request_path.is_file():
        raise ValueError("request path is invalid.")
    _required_sha256(expected_sha256)
    body = request_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError("request hash mismatch.")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("request must be a mapping.")
    return value


def _validate_output_path(raw: object) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("output_dir is invalid.")
    output = Path(raw)
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("output_dir must be absolute, new, and non-symlinked.")
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("output_dir parent is invalid.")
    repository_root = Path(__file__).resolve().parents[1]
    try:
        output.resolve().relative_to(repository_root)
    except ValueError:
        return output
    raise ValueError("output_dir must be outside the repository.")


def _write_bundle_atomic(
    *,
    request: dict[str, object],
    result: dict[str, object],
    output_dir: Path,
) -> None:
    required_result_fields = {
        "dataset_metadata",
        "qlib_runtime",
        "factor_screen",
        "reference_parity",
        "economic_scorecard",
        "updated_ledger",
    }
    if not required_result_fields.issubset(result):
        raise ValueError("pilot result artifact fields are missing.")
    payloads: dict[str, object] = {
        "request.json": request,
        "dataset_metadata.json": result["dataset_metadata"],
        "qlib_runtime.json": result["qlib_runtime"],
        "factor_screen.json": result["factor_screen"],
        "reference_parity.json": result["reference_parity"],
        "economic_scorecard.json": result["economic_scorecard"],
        "updated_ledger.json": result["updated_ledger"],
        "result.json": result,
    }
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=str(output_dir.parent)))
    try:
        checksums: dict[str, str] = {}
        for name in _JSON_ARTIFACTS:
            body = _json_bytes(payloads[name])
            path = temporary / name
            _write_bytes(path, body)
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = hashlib.sha256(body).hexdigest()
            if observed != expected:
                raise OSError("artifact verification failed.")
            checksums[name] = observed
        checksum_payload = {
            "version": "real_qlib_edge_discovery_checksums_v1",
            "sha256": dict(sorted(checksums.items())),
        }
        checksum_body = _json_bytes(checksum_payload)
        checksum_path = temporary / "checksums.json"
        _write_bytes(checksum_path, checksum_body)
        if json.loads(checksum_path.read_text(encoding="utf-8")) != checksum_payload:
            raise OSError("checksum artifact verification failed.")
        _write_bytes(temporary / "COMPLETE", b"status=COMPLETE\n")
        temporary.replace(output_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _write_bytes(path: Path, body: bytes) -> None:
    path.write_bytes(body)


def _required_sha256(raw: object) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError("expected request hash must be lowercase SHA-256.")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
