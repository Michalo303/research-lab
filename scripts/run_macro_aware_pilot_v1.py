from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.execution.macro_aware_pilot_runner_v1 import run_macro_aware_pilot
from research_lab.execution.macro_aware_pilot_request_builder_v1 import (
    prepare_controlled_synthetic_macro_pilot_request,
)
from research_lab.execution.macro_aware_pilot_verifier_replay_v1 import (
    replay_macro_aware_pilot,
    verify_macro_aware_pilot_run,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and verify the controlled synthetic macro pilot.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--market-snapshot", required=True)
    prepare_parser.add_argument("--request-output", required=True)
    prepare_parser.add_argument("--run-id", required=True)
    prepare_parser.add_argument("--created-at", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", required=True)
    run_parser.add_argument("--output-dir", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--run-dir", required=True)
    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--source-run-dir", required=True)
    replay_parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "prepare":
            result = prepare_controlled_synthetic_macro_pilot_request(
                market_snapshot_path=args.market_snapshot,
                request_output_path=args.request_output,
                run_id=args.run_id,
                created_at=args.created_at,
            )
            success = result["request_status"] == "PREPARED"
        elif args.command == "run":
            request = _load_json(Path(args.input))
            result = run_macro_aware_pilot(request, output_dir=args.output_dir)
            success = result["execution_status"] == "COMPLETED"
        elif args.command == "verify":
            result = verify_macro_aware_pilot_run(args.run_dir)
            success = result["verification_status"] == "VERIFIED"
        else:
            result = replay_macro_aware_pilot(
                args.source_run_dir,
                replay_output_dir=args.output_dir,
            )
            success = result["replay_status"] == "REPLAY_MATCH"
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        result = _failure_result(str(exc))
        success = False
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if success else 2


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input must contain a JSON object.")
    return payload


def _failure_result(failure_reason: str) -> dict[str, Any]:
    return {
        "version": "macro_aware_pilot_cli_result_v1",
        "execution_status": "FAILED_VALIDATION",
        "failure_reason": failure_reason,
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "paper_trading_performed": False,
        "deployment_performed": False,
        "promotion_performed": False,
        "generated_code_executed": False,
        "automatic_strategy_application_performed": False,
        "production_runtime_supported": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
