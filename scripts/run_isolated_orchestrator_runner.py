from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.execution.isolated_orchestrator_runner_v1 import (
    RUN_REPORT_VERSION,
    _protected_output_roots,
    run_isolated_orchestrator_runner,
)


EXIT_VALIDATION_FAILURE = 2
EXIT_UNSAFE_OUTPUT_DIR = 3
EXIT_NONEMPTY_OUTPUT_DIR = 4
EXIT_LOCK_CONFLICT = 5
EXIT_IO_FAILURE = 6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated review-only orchestrator into an explicit output directory.")
    parser.add_argument("--input", required=True, help="Path to the orchestrator run-bundle request JSON.")
    parser.add_argument("--output-dir", required=True, help="Explicit isolated output directory for run artifacts.")
    args = parser.parse_args(argv)

    try:
        bundle_request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _emit_failure("input_read_failed", EXIT_IO_FAILURE)

    output_dir = Path(args.output_dir).expanduser()
    if _is_unsafe_output_dir(output_dir, repo_root=Path(__file__).resolve().parents[1]):
        return _emit_failure("unsafe_output_dir", EXIT_UNSAFE_OUTPUT_DIR)

    try:
        result = run_isolated_orchestrator_runner(bundle_request, output_dir=output_dir)
    except ValueError as exc:
        message = str(exc)
        if "unsafe_output_dir" in message:
            return _emit_failure("unsafe_output_dir", EXIT_UNSAFE_OUTPUT_DIR)
        if "non-empty" in message or "completed run" in message:
            return _emit_failure("nonempty_output_dir", EXIT_NONEMPTY_OUTPUT_DIR)
        if "lock" in message:
            return _emit_failure("lock_conflict", EXIT_LOCK_CONFLICT)
        return _emit_failure(message, EXIT_VALIDATION_FAILURE)
    except OSError:
        return _emit_failure("output_write_failed", EXIT_IO_FAILURE)

    print(json.dumps(result, sort_keys=True))
    return 0


def _emit_failure(failure_reason: str, exit_code: int) -> int:
    payload = {
        "version": RUN_REPORT_VERSION,
        "execution_status": "failed",
        "failure_reason": failure_reason,
        "run_directory_complete": False,
        "execution_authority_granted": False,
        "persistence_authority_granted": False,
    }
    print(json.dumps(payload, sort_keys=True))
    return exit_code


def _is_unsafe_output_dir(output_dir: Path, *, repo_root: Path) -> bool:
    if any(part == ".." for part in output_dir.parts):
        return True
    resolved_output = _resolved_destination_path(output_dir)
    for protected_root in _protected_output_roots(repo_root):
        try:
            resolved_output.relative_to(protected_root)
            return True
        except ValueError:
            continue
    return False


def _resolved_destination_path(path: Path) -> Path:
    resolved_parent = _resolved_existing_parent(path)
    if path.exists():
        return path.resolve()
    missing_parts: list[str] = []
    current = path
    while not current.exists():
        missing_parts.append(current.name)
        current = current.parent
    return resolved_parent.joinpath(*reversed(missing_parts))


def _resolved_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        current = current.parent
    return current.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
