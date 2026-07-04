from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one deterministic daily research cycle."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Research-lab root to run against. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Print basic readiness details and exit without running daily research.",
    )
    parser.add_argument(
        "--recovery-mode",
        action="store_true",
        help="Run one explicit bounded recovery-manifest day instead of normal daily selection.",
    )
    parser.add_argument(
        "--recovery-day",
        type=_positive_integer,
        help="Positive bounded recovery day. Days above 7 resume normal daily selection.",
    )
    return parser


def _print_preflight(root: Path) -> None:
    from research_lab.config import LabConfig

    resolved_root = root.resolve()
    config = LabConfig.from_env(resolved_root)
    print(f"preflight_only=true root={resolved_root}")
    print("entrypoint=research_lab.runner.run_daily_research")
    print(f"root_exists={resolved_root.exists()}")
    print(f"data_provider={config.data_provider}")
    print(f"eodhd_credentials_present={str(bool(config.eodhd_api_key)).lower()}")
    print("manual_cli_loads_dotenv=false")
    print("systemd_service_loads_environmentfile=if_configured")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.recovery_mode and args.recovery_day is None:
        parser.error("--recovery-mode requires --recovery-day")
    if args.recovery_day is not None and not args.recovery_mode:
        parser.error("--recovery-day requires --recovery-mode")
    if args.preflight_only:
        _print_preflight(args.root)
        return 0

    from research_lab.runner import run_daily_research

    results = run_daily_research(
        args.root,
        recovery_mode=args.recovery_mode,
        recovery_day=args.recovery_day,
    )
    print(f"daily research completed: {len(results)} experiments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
