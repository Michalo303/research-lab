from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.execution.risk_overlay_isolated_executor_v1 import (
    RESULT_VERSION,
    run_isolated_risk_overlay_execution,
)


EXIT_VALIDATION_FAILURE = 2
EXIT_UNSAFE_OUTPUT_PATH = 3
EXIT_OVERWRITE_FORBIDDEN = 4
EXIT_IO_FAILURE = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated synthetic risk overlay executor.")
    parser.add_argument("--input", required=True, help="Path to the isolated executor request JSON.")
    parser.add_argument("--output", required=True, help="Path to the output result JSON artifact.")
    args = parser.parse_args(argv)

    try:
        request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _emit_failure("input_read_failed", EXIT_IO_FAILURE)

    output_path = Path(args.output).expanduser()
    if output_path.exists():
        return _emit_failure("overwrite_forbidden", EXIT_OVERWRITE_FORBIDDEN)
    if _is_unsafe_output_path(output_path, repo_root=Path(__file__).resolve().parents[1]):
        return _emit_failure("unsafe_output_path", EXIT_UNSAFE_OUTPUT_PATH)

    try:
        result = run_isolated_risk_overlay_execution(request)
    except ValueError as exc:
        return _emit_failure(str(exc), EXIT_VALIDATION_FAILURE)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return _emit_failure("output_write_failed", EXIT_IO_FAILURE)

    print(json.dumps(result, sort_keys=True))
    return 0


def _emit_failure(failure_reason: str, exit_code: int) -> int:
    payload = {
        "version": RESULT_VERSION,
        "execution_status": "failed",
        "failure_reason": failure_reason,
        "execution_performed": False,
        "synthetic_data_used": True,
    }
    print(json.dumps(payload, sort_keys=True))
    return exit_code


def _is_unsafe_output_path(output_path: Path, *, repo_root: Path) -> bool:
    resolved_output = _resolved_destination_path(output_path)
    for protected_root in _protected_output_roots(repo_root):
        try:
            resolved_output.relative_to(protected_root)
            return True
        except ValueError:
            continue
    return False


def _protected_output_roots(repo_root: Path) -> list[Path]:
    resolved_repo_root = repo_root.resolve()
    roots = [
        resolved_repo_root,
        Path("/opt/trading/private"),
        Path("/opt/trading/private/hermes_books"),
        Path("/opt/trading/research-lab/reports"),
        Path("/opt/trading/research-lab/backtests_runs"),
        Path("/opt/trading/research-lab/leaderboard"),
        Path("/opt/trading/research-lab/cache"),
        Path("/opt/trading/research-lab/deployment"),
        Path("/opt/trading/research-lab/data"),
        Path("/opt/trading/research-lab/tests/fixtures"),
        resolved_repo_root / "reports",
        resolved_repo_root / "backtests_runs",
        resolved_repo_root / "leaderboard",
        resolved_repo_root / "cache",
        resolved_repo_root / "deployment",
        resolved_repo_root / "data",
        resolved_repo_root / "tests" / "fixtures",
        resolved_repo_root / "research_lab",
        resolved_repo_root / "scripts",
        resolved_repo_root / "tests",
    ]
    return [_normalize_path(root) for root in roots]


def _resolved_destination_path(path: Path) -> Path:
    output_path = path.expanduser()
    resolved_parent = _resolved_existing_parent(output_path)
    if output_path.exists():
        return output_path.resolve()
    missing_parts = []
    current = output_path
    while not current.exists():
        missing_parts.append(current.name)
        current = current.parent
    return resolved_parent.joinpath(*reversed(missing_parts))


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _resolved_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        current = current.parent
    return current.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
