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


def _bool(value: object) -> str:
    return str(bool(value)).lower()


def _emit_recovery_preflight_status(
    *,
    data_provider: str,
    ready: bool,
    blocker_reason: str,
    extra_lines: list[str] | None = None,
) -> int:
    print("recovery_preflight=true")
    print(f"data_provider={data_provider}")
    print("provider_calls=0")
    print(f"blocker_reason={blocker_reason}")
    print(f"ready={_bool(ready)}")
    for line in extra_lines or []:
        print(line)
    return 0 if ready else 1


def _print_preflight(
    root: Path,
    *,
    recovery_mode: bool = False,
    recovery_day: int | None = None,
) -> int:
    from research_lab.config import LabConfig

    resolved_root = root.resolve()
    effective_provider = LabConfig.data_provider_from_env()
    bounded_recovery = (
        recovery_mode
        and recovery_day is not None
        and recovery_day <= 7
    )
    print(f"preflight_only=true root={resolved_root}")
    print("entrypoint=research_lab.runner.run_daily_research")
    print(f"root_exists={resolved_root.exists()}")
    print(f"data_provider={effective_provider}")
    print("manual_cli_loads_dotenv=false")
    print("systemd_service_loads_environmentfile=if_configured")
    if bounded_recovery:
        provider_valid = effective_provider == "eodhd_cache"
        if not provider_valid:
            print("eodhd_credentials_present=false")
            return _emit_recovery_preflight_status(
                data_provider=effective_provider,
                ready=False,
                blocker_reason="invalid_data_provider",
                extra_lines=[
                    f"effective_data_provider={effective_provider}",
                    f"recovery_provider_valid={_bool(provider_valid)}",
                    "recovery_ready=false",
                ],
            )

    config = LabConfig.from_env(resolved_root)
    print(f"eodhd_credentials_present={_bool(config.eodhd_api_key)}")
    if config.data_provider != "eodhd_cache":
        return 0

    csv_path = resolved_root / "data" / "processed" / "eodhd_daily_universe.csv"
    manifest_path = resolved_root / "data" / "manifests" / "daily_universe.json"
    print("data_access_mode=offline_cache")
    print(f"cached_eodhd_csv_present={_bool(csv_path.is_file())}")
    print(f"cached_eodhd_manifest_present={_bool(manifest_path.is_file())}")
    print("provider_request_allowed=false")

    if not bounded_recovery:
        print("cache_requested_symbols_present=false")
        print("recovery_ready=false")
        return 1

    from research_lab.data import validate_cached_eodhd_daily_universe_metadata
    from research_lab.runner import _spec_symbols, select_daily_candidates
    from research_lab.strategies.baselines import recovery_manifest_specs

    required_symbols = _spec_symbols(recovery_manifest_specs(recovery_day))
    diagnostics: dict = {}
    resolution_error = False
    try:
        diagnostics = select_daily_candidates(
            resolved_root,
            recovery_mode=True,
            recovery_day=recovery_day,
        )["diagnostics"]
    except (OSError, TypeError, ValueError) as exc:
        resolution_error = True
        print(f"recovery_validation_error={type(exc).__name__}")
    target = int(diagnostics.get("recovery_target", 0))
    selected_new = int(diagnostics.get("selected_new", 0))
    covered = int(diagnostics.get("covered_by_recent_real", 0))
    nonqualifying = int(diagnostics.get("nonqualifying_recent_matches", 0))
    resolved = int(diagnostics.get("recovery_resolved", 0))
    shortfall = int(diagnostics.get("recovery_shortfall", max(target - resolved, 0)))
    resolution_complete = not resolution_error and target > 0 and resolved == target and shortfall == 0
    manifest: dict = {}
    cache_valid = False
    cache_error = False
    if resolution_complete:
        try:
            manifest = validate_cached_eodhd_daily_universe_metadata(resolved_root, required_symbols)
            cache_valid = True
        except (OSError, ValueError) as exc:
            cache_error = True
            print(f"cache_validation_error={type(exc).__name__}")
    print(f"cached_manifest_source={str(manifest.get('source') or '')}")
    print(f"cached_manifest_fallback_used={_bool(manifest.get('fallback_used', False))}")
    print(f"cache_requested_symbols_present={_bool(cache_valid)}")
    ready = (
        config.mode == "research_only"
        and cache_valid
        and resolution_complete
    )
    print(f"recovery_day={recovery_day}")
    print(f"recovery_target={target}")
    print(f"recovery_selected_new={selected_new}")
    print(f"recovery_selected_for_new_execution={selected_new}")
    print(f"recovery_covered_by_recent_real={covered}")
    print(f"recovery_historically_covered={covered}")
    print(f"recovery_nonqualifying_recent_matches={nonqualifying}")
    print(f"recovery_resolved={resolved}")
    print(f"recovery_shortfall={shortfall}")
    print(f"recovery_unresolved={shortfall}")
    print(f"recovery_ready={_bool(ready)}")
    if ready:
        return _emit_recovery_preflight_status(
            data_provider=effective_provider,
            ready=True,
            blocker_reason="none",
        )
    blocker_reason = "unresolved_recovery"
    if cache_error or not cache_valid:
        blocker_reason = "cache_metadata_invalid" if resolution_complete else "unresolved_recovery"
    return _emit_recovery_preflight_status(
        data_provider=effective_provider,
        ready=False,
        blocker_reason=blocker_reason,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.recovery_mode and args.recovery_day is None:
        parser.error("--recovery-mode requires --recovery-day")
    if args.recovery_day is not None and not args.recovery_mode:
        parser.error("--recovery-day requires --recovery-mode")
    if args.preflight_only:
        return _print_preflight(
            args.root,
            recovery_mode=args.recovery_mode,
            recovery_day=args.recovery_day,
        )

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
