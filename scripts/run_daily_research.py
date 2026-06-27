from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
    args = build_parser().parse_args(argv)
    if args.preflight_only:
        _print_preflight(args.root)
        return 0

    from research_lab.runner import run_daily_research

    results = run_daily_research(args.root)
    print(f"daily research completed: {len(results)} experiments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
