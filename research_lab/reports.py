from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from research_lab.drawdown_diagnostics import drawdown_diagnostics_for_result


def write_strategy_card(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    drawdown = drawdown_diagnostics_for_result(result)
    recovery_label = _format_recovery_date(drawdown)
    lines = [
        f"# Strategy Card: {result['strategy_id']}",
        "",
        "## Hypothesis",
        result["hypothesis"],
        "",
        "## Rules",
        result["rules"],
        "",
        "## Asset Universe",
        result["asset_class"],
        "",
        "## Data",
        f"Source: {result['data_manifest']['source']}; range: {result['data_manifest']['start']} to {result['data_manifest']['end']}; rows: {result['data_manifest']['rows']}.",
        "",
        "## Costs",
        f"Normal cost: {result['cost_stress']['normal_cost_bps']} bps; stress cost: {result['cost_stress']['double_cost_bps']} bps.",
        "",
        "## Results",
        "```json",
        json.dumps(result["split_metrics"], indent=2),
        "```",
        "",
        "## Drawdown",
        f"Unseen max drawdown: {result['split_metrics']['unseen']['max_drawdown']:.2%}.",
        f"Worst drawdown start: {_format_optional_date(drawdown['worst_drawdown_start'])}.",
        f"Worst drawdown trough: {_format_optional_date(drawdown['worst_drawdown_trough'])}.",
        f"Worst drawdown recovery: {recovery_label}.",
        f"Drawdown duration days: {drawdown['drawdown_duration_days']}.",
        f"Worst calendar-year return: {_format_percent(drawdown['worst_year_return'])}.",
        f"Best calendar-year return: {_format_percent(drawdown['best_year_return'])}.",
        f"CAGR / abs(max drawdown): {drawdown['cagr_to_drawdown_ratio']:.2f}.",
        "",
        "## Robustness",
        f"Double-cost stress survives: {result['cost_stress']['survives_double_cost']}. Parameter stability is marked as TODO for deeper weekly runs.",
        "",
        "## Failure Modes",
        "Synthetic data, low trade count, unstable neighboring parameters, and cost sensitivity invalidate promotion.",
        "",
        "## Tier Decision",
        f"Tier: {result['tier']}. Reason: {result['tier_reason']}",
        "",
        "## Deployment Readiness",
        "DEPLOYMENT_CANDIDATE: NO",
        "REASON: Research-only lab output. Live deployment is prohibited without explicit approval and paper validation.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_daily_report(path: Path, results: list[dict], report_date: date | None = None, run_metadata: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = (report_date or date.today()).isoformat()
    accepted = [r for r in results if r["tier"] in {"A", "B"}]
    rejected = [r for r in results if r["tier"] == "Rejected"]
    best = max(results, key=lambda r: r["split_metrics"]["unseen"]["mar"]) if results else None
    sources = sorted({r["data_manifest"]["source"] for r in results})
    source_note = _source_note(results)
    next_actions = _next_actions(results)
    rejection_diagnostics = _rejection_diagnostics_rows(rejected)
    drawdown_diagnostics = _drawdown_diagnostics_rows(results)
    rejection_drawdown_attribution = _rejection_drawdown_attribution_rows(rejected)
    rows = [
        "| strategy_id | family | asset | timeframe | data_source | train | validation | unseen | max_dd | tier |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        rows.append(
            "| {strategy_id} | {family} | {asset_class} | {timeframe} | {data_source} | {train:.2%} | {validation:.2%} | {unseen:.2%} | {max_dd:.2%} | {tier} |".format(
                strategy_id=r["strategy_id"],
                family=r["family"],
                asset_class=r["asset_class"],
                timeframe=r["timeframe"],
                data_source=r["data_manifest"]["source"],
                train=r["split_metrics"]["train"]["cagr"],
                validation=r["split_metrics"]["validation"]["cagr"],
                unseen=r["split_metrics"]["unseen"]["cagr"],
                max_dd=r["split_metrics"]["unseen"]["max_drawdown"],
                tier=r["tier"],
            )
        )
    lines = [
        f"# Daily Research Report - {today}",
        "",
        "## Summary",
        "",
        f"- experiments run: {len(results)}",
        f"- accepted: {len(accepted)}",
        f"- rejected: {len(rejected)}",
        f"- best research result: {best['strategy_id'] if best else 'none'}",
        f"- data sources: {', '.join(sources) if sources else 'none'}",
        f"- biggest risk discovered: {source_note}",
        "",
        "## New Strategies Tested",
        "",
        *rows,
        "",
        "## Important Findings",
        "",
        "- The deterministic runner, registry, leaderboard, and strategy-card pipeline are now operational.",
        f"- {source_note}",
        "- Negative unseen results, excessive drawdown, failed cost stress, or too few trades are rejected even during smoke tests.",
        "",
        "## Rejections",
        "",
        *(f"- {r['strategy_id']}: {r['tier_reason']}" for r in rejected),
        "",
        "## Rejection Diagnostics",
        "",
        *rejection_diagnostics,
        "",
        *rejection_drawdown_attribution,
        "",
        "## Drawdown Diagnostics",
        "",
        *drawdown_diagnostics,
        "",
        "## Leaderboard Changes",
        "",
        "- Leaderboard and allocation model were regenerated from the current run.",
        "",
        "## Next Actions",
        "",
        *next_actions,
    ]
    if run_metadata:
        lines.extend(_run_metadata_lines(run_metadata))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_daily_report_artifacts(
    root: Path,
    results: list[dict],
    *,
    timestamp: datetime | None = None,
    git_info: dict[str, Any] | None = None,
    command: list[str] | str | None = None,
    runner_name: str = "run_daily_research",
    run_id: str | None = None,
    allow_existing_run_id: bool = False,
) -> dict[str, Any]:
    timestamp_utc = _utc_timestamp(timestamp)
    report_day = timestamp_utc.date()
    git = git_info if git_info is not None else collect_git_info(root)
    actual_run_id = run_id or generate_run_id(timestamp_utc, git.get("commit"))
    latest_report_path = root / "reports" / "daily" / f"{report_day.isoformat()}.md"
    run_dir = root / "reports" / "runs" / report_day.isoformat() / actual_run_id
    run_report_path = run_dir / "daily_report.md"
    metadata_path = run_dir / "run_metadata.json"
    if run_dir.exists() and not allow_existing_run_id:
        raise FileExistsError(f"run artifact already exists for run_id={actual_run_id}: {run_dir}")

    metadata = _sanitize_metadata(
        {
            "run_id": actual_run_id,
            "timestamp_utc": timestamp_utc.isoformat(),
            "git": _git_metadata(git),
            "runner": runner_name,
            "command": _sanitize_command(command if command is not None else sys.argv),
            "latest_report_path": _relative_posix(root, latest_report_path),
            "run_report_path": _relative_posix(root, run_report_path),
            "data_sources": _data_sources(results),
            "provider_history_summary": _provider_history_summary(results),
        }
    )
    write_daily_report(latest_report_path, results, report_date=report_day, run_metadata=metadata)
    write_daily_report(run_report_path, results, report_date=report_day, run_metadata=metadata)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "run_id": actual_run_id,
        "latest_report_path": latest_report_path,
        "run_report_path": run_report_path,
        "metadata_path": metadata_path,
        "metadata": metadata,
    }


def generate_run_id(timestamp: datetime | None = None, commit: str | None = None) -> str:
    timestamp_utc = _utc_timestamp(timestamp)
    commit_part = _short_commit(commit)
    return f"{timestamp_utc:%Y%m%dT%H%M%S%fZ}-{commit_part}"


def collect_git_info(root: Path) -> dict[str, Any]:
    commit = _git_output(root, "rev-parse", "HEAD")
    branch = _git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git_output(root, "status", "--short")
    dirty_paths = _dirty_paths_from_git_status(status or "")
    return {
        "commit": commit,
        "branch": branch if branch != "HEAD" else None,
        **classify_git_dirty_paths(dirty_paths),
    }


def classify_git_dirty_paths(dirty_paths: list[str]) -> dict[str, Any]:
    normalized_paths = _unique_dirty_paths(_normalize_dirty_path(path) for path in dirty_paths)
    runtime_paths = [path for path in normalized_paths if _is_runtime_artifact_path(path)]
    code_paths = [path for path in normalized_paths if path not in runtime_paths]
    code_dirty = bool(code_paths)
    runtime_artifacts_dirty = bool(runtime_paths)

    if not normalized_paths:
        dirty_classification = "clean"
    elif code_dirty and runtime_artifacts_dirty:
        dirty_classification = "mixed_code_and_runtime_dirty"
    elif code_dirty:
        dirty_classification = "code_or_config_dirty"
    else:
        dirty_classification = "runtime_artifacts_only"

    return {
        "dirty": bool(normalized_paths),
        "code_dirty": code_dirty,
        "runtime_artifacts_dirty": runtime_artifacts_dirty,
        "dirty_files": normalized_paths,
        "dirty_classification": dirty_classification,
    }


def _git_metadata(git: dict[str, Any]) -> dict[str, Any]:
    fallback = classify_git_dirty_paths(git.get("dirty_files", []))
    if git.get("dirty") is True and not fallback["dirty"]:
        fallback = {
            **fallback,
            "dirty": True,
            "code_dirty": True,
            "dirty_classification": "code_or_config_dirty",
        }
    return {
        "commit": git.get("commit"),
        "branch": git.get("branch"),
        "dirty": git.get("dirty", fallback["dirty"]),
        "code_dirty": git.get("code_dirty", fallback["code_dirty"]),
        "runtime_artifacts_dirty": git.get("runtime_artifacts_dirty", fallback["runtime_artifacts_dirty"]),
        "dirty_files": git.get("dirty_files", fallback["dirty_files"]),
        "dirty_classification": git.get("dirty_classification", fallback["dirty_classification"]),
    }


def _dirty_paths_from_git_status(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        status_columns = line[:2]
        path_text = line[3:] if len(line) > 3 and line[2] == " " else line[2:].lstrip()
        if not status_columns.strip() or not path_text:
            continue
        if " -> " in path_text:
            paths.append(_clean_status_path(path_text.split(" -> ", 1)[1]))
        else:
            paths.append(_clean_status_path(path_text))
    return paths


def _clean_status_path(path: str) -> str:
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        return path[1:-1]
    return path


def _normalize_dirty_path(path: str) -> str:
    return str(path).strip().replace("\\", "/").lstrip("./")


def _unique_dirty_paths(paths: Any) -> list[str]:
    unique = []
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _is_runtime_artifact_path(path: str) -> bool:
    if path.startswith("reports/runs/"):
        return True
    runtime_paths = {
        "registry/allocation_model.csv",
        "registry/leaderboard.csv",
        "registry/experiments.jsonl",
        "registry/strategy_registry.jsonl",
    }
    if path in runtime_paths:
        return True
    if path.startswith("data/manifests/") and path.endswith(".json") and "/" not in path.removeprefix("data/manifests/"):
        return True
    if path.startswith("reports/daily/") and path.endswith(".md") and "/" not in path.removeprefix("reports/daily/"):
        return True
    return False


def _drawdown_diagnostics_rows(results: list[dict]) -> list[str]:
    if not results:
        return ["- none"]
    rows = [
        "| strategy_id | start | trough | recovery | duration_days | max_dd | worst_year | best_year | cagr_to_dd |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        diagnostic = drawdown_diagnostics_for_result(result)
        rows.append(
            "| {strategy_id} | {start} | {trough} | {recovery} | {duration} | {max_dd} | {worst_year} | {best_year} | {ratio:.2f} |".format(
                strategy_id=result["strategy_id"],
                start=_format_optional_date(diagnostic["worst_drawdown_start"]),
                trough=_format_optional_date(diagnostic["worst_drawdown_trough"]),
                recovery=_format_recovery_date(diagnostic),
                duration=diagnostic["drawdown_duration_days"],
                max_dd=_format_percent(diagnostic["max_drawdown"]),
                worst_year=_format_percent(diagnostic["worst_year_return"]),
                best_year=_format_percent(diagnostic["best_year_return"]),
                ratio=diagnostic["cagr_to_drawdown_ratio"],
            )
        )
    return rows


def _rejection_drawdown_attribution_rows(rejected: list[dict]) -> list[str]:
    if not rejected:
        return ["### Rejection Drawdown Attribution", "", "- none"]
    rows = ["### Rejection Drawdown Attribution", ""]
    for result in rejected:
        diagnostic = drawdown_diagnostics_for_result(result)
        rows.append(
            "- {strategy_id}: worst_drawdown_start={start}; worst_drawdown_trough={trough}; "
            "worst_drawdown_recovery={recovery}; drawdown_duration_days={duration}; "
            "max_drawdown={max_dd}; worst_year_return={worst_year}; best_year_return={best_year}; "
            "cagr_to_drawdown_ratio={ratio:.2f}".format(
                strategy_id=result["strategy_id"],
                start=diagnostic["worst_drawdown_start"],
                trough=diagnostic["worst_drawdown_trough"],
                recovery=diagnostic["worst_drawdown_recovery"] or "unrecovered",
                duration=diagnostic["drawdown_duration_days"],
                max_dd=_format_percent(diagnostic["max_drawdown"]),
                worst_year=_format_percent(diagnostic["worst_year_return"]),
                best_year=_format_percent(diagnostic["best_year_return"]),
                ratio=diagnostic["cagr_to_drawdown_ratio"],
            )
        )
    return rows


def _rejection_diagnostics_rows(rejected: list[dict]) -> list[str]:
    if not rejected:
        return ["- none"]
    rows = [
        "| strategy_id | primary rejection reason | secondary rejection reasons | failed metric | actual value | required threshold |",
        "|---|---|---|---|---:|---:|",
    ]
    for result in rejected:
        diagnostic = _rejection_diagnostic(result)
        rows.append(
            "| {strategy_id} | {primary_reason} | {secondary_reasons} | {metric} | {actual} | {threshold} |".format(
                strategy_id=result["strategy_id"],
                primary_reason=diagnostic["primary_reason"],
                secondary_reasons=diagnostic["secondary_reasons"],
                metric=diagnostic["metric"],
                actual=diagnostic["actual"],
                threshold=diagnostic["threshold"],
            )
        )
    return rows


def _rejection_diagnostic(result: dict) -> dict:
    failures = _hard_rejection_failures(result)
    primary_reason = result.get("tier_reason", "")
    primary = next((failure for failure in failures if failure["reason"] == primary_reason), None)
    if primary is None:
        primary = failures[0] if failures else _fallback_rejection_failure(result)
    secondary = [failure["reason"] for failure in failures if failure["reason"] != primary["reason"]]
    return {
        "primary_reason": primary_reason or primary["reason"],
        "secondary_reasons": "; ".join(secondary) if secondary else "none",
        "metric": primary["metric"],
        "actual": primary["actual"],
        "threshold": primary["threshold"],
    }


def _hard_rejection_failures(result: dict) -> list[dict]:
    unseen = result.get("split_metrics", {}).get("unseen", {})
    cost_stress = result.get("cost_stress", {})
    family = result.get("family", "")
    failures = []
    cagr = float(unseen.get("cagr", 0.0))
    max_drawdown = float(unseen.get("max_drawdown", 0.0))
    trade_count = int(unseen.get("trade_count", 0))
    if cagr <= 0:
        failures.append(_failure("Negative unseen result.", "unseen_cagr", _format_percent(cagr), "> 0.00%"))
    if max_drawdown < -0.15:
        failures.append(
            _failure(
                "Unseen max drawdown exceeds 15%.",
                "unseen_max_drawdown",
                _format_percent(max_drawdown),
                ">= -15.00%",
            )
        )
    if family in {"SWING", "INTRADAY"} and trade_count < 100:
        failures.append(
            _failure(
                "Too few unseen trades for a trade-based strategy.",
                "unseen_trades",
                str(trade_count),
                ">= 100",
            )
        )
    if not bool(cost_stress.get("survives_double_cost", True)):
        failures.append(
            _failure(
                "Double transaction-cost stress destroys unseen profitability.",
                "double_cost_unseen_cagr",
                _format_percent(float(cost_stress.get("double_unseen_cagr", 0.0))),
                "> 0.00%",
            )
        )
    return failures


def _fallback_rejection_failure(result: dict) -> dict:
    return _failure(
        result.get("tier_reason", "Rejected by tiering logic."),
        "tier_reason",
        result.get("tier_reason", ""),
        "non-rejected tier",
    )


def _failure(reason: str, metric: str, actual: str, threshold: str) -> dict:
    return {"reason": reason, "metric": metric, "actual": actual, "threshold": threshold}


def _format_percent(value: float) -> str:
    return f"{value:.2%}"


def _format_optional_date(value: Any) -> str:
    return str(value) if value else "none"


def _format_recovery_date(diagnostic: dict[str, Any]) -> str:
    if diagnostic.get("worst_drawdown_recovery"):
        return str(diagnostic["worst_drawdown_recovery"])
    return "unrecovered" if diagnostic.get("worst_drawdown_start") else "none"


def _source_note(results: list[dict]) -> str:
    sources = {r["data_manifest"]["source"] for r in results}
    if "massive" in sources:
        years = max(float(r["data_manifest"].get("years", 0.0)) for r in results if r["data_manifest"]["source"] == "massive")
        return f"Massive real EOD data is enabled, but available history is only {years:.1f} years; long-term promotion still needs 10+ years plus walk-forward validation."
    if "eodhd" in sources:
        years = max(float(r["data_manifest"].get("years", 0.0)) for r in results if r["data_manifest"]["source"] == "eodhd")
        return f"EODHD real EOD data is enabled with {years:.1f} years of available history; strategy promotion still depends on existing validation gates."
    if "yfinance" in sources:
        return "Free EOD data is enabled; data integrity, adjusted prices, and survivorship assumptions still need validation."
    return "Synthetic data cannot validate capital allocation; real data ingestion and walk-forward stability remain required."


def _next_actions(results: list[dict]) -> list[str]:
    sources = {r["data_manifest"]["source"] for r in results}
    if "massive" in sources:
        return [
            "- Run daily Massive-backed research for 7-14 days before judging the subscription.",
            "- Add walk-forward and parameter-neighborhood stability for the weekly deep run.",
            "- Add a longer-history EOD source before promoting long-term or rotation systems above Tier C.",
        ]
    if "eodhd" in sources:
        return [
            "- Monitor EODHD-backed daily research for provider stability and symbol coverage gaps.",
            "- Add walk-forward and parameter-neighborhood stability for the weekly deep run.",
            "- Keep deployment blocked until paper validation and existing gates pass.",
        ]
    return [
        "- Enable real EOD data ingestion on Hetzner if network/dependencies allow it.",
        "- Add walk-forward and parameter-neighborhood stability for the weekly deep run.",
        "- Add data integrity checks before any strategy can rise above paper-only research.",
    ]


def _run_metadata_lines(metadata: dict[str, Any]) -> list[str]:
    git = metadata.get("git", {})
    return [
        "",
        "## Run Metadata",
        "",
        f"- run_id: {metadata.get('run_id', '')}",
        f"- timestamp_utc: {metadata.get('timestamp_utc', '')}",
        f"- git_commit: {git.get('commit') or 'unknown'}",
        f"- git_branch: {git.get('branch') or 'unknown'}",
        f"- git_dirty: {git.get('dirty')}",
        f"- code_dirty: {git.get('code_dirty')}",
        f"- runtime_artifacts_dirty: {git.get('runtime_artifacts_dirty')}",
        f"- dirty_classification: {git.get('dirty_classification')}",
        f"- dirty_files: {_format_dirty_files(git.get('dirty_files'))}",
        f"- immutable_report_path: {metadata.get('run_report_path', '')}",
    ]


def _format_dirty_files(dirty_files: Any) -> str:
    if not dirty_files:
        return "none"
    if isinstance(dirty_files, list):
        return ", ".join(str(path) for path in dirty_files) or "none"
    return str(dirty_files)


def _utc_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _short_commit(commit: str | None) -> str:
    if not commit:
        return "nogit"
    return re.sub(r"[^0-9A-Za-z]", "", commit)[:7] or "nogit"


def _git_output(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _relative_posix(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _data_sources(results: list[dict]) -> list[str]:
    sources = {
        str(result.get("data_manifest", {}).get("source") or result.get("data_source", ""))
        for result in results
        if result.get("data_manifest", {}).get("source") or result.get("data_source")
    }
    return sorted(sources)


def _provider_history_summary(results: list[dict]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for result in results:
        manifest = result.get("data_manifest", {})
        source = str(manifest.get("source") or result.get("data_source", "unknown"))
        summary = by_source.setdefault(
            source,
            {
                "source": source,
                "start": manifest.get("start"),
                "end": manifest.get("end"),
                "rows": manifest.get("rows"),
                "years": manifest.get("years"),
                "symbols": [],
                "symbol_history": [],
            },
        )
        summary["start"] = _min_non_empty(summary.get("start"), manifest.get("start"))
        summary["end"] = _max_non_empty(summary.get("end"), manifest.get("end"))
        summary["rows"] = _max_number(summary.get("rows"), manifest.get("rows"))
        summary["years"] = _max_number(summary.get("years"), manifest.get("years"))
        summary["symbols"] = sorted(set(summary["symbols"]) | {str(symbol) for symbol in manifest.get("symbols", [])})
        summary["symbol_history"].extend(_symbol_history(manifest))
    return [by_source[source] for source in sorted(by_source)]


def _symbol_history(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = ("requested_symbol", "selected_provider", "first_date", "last_date", "daily_bars", "history_years")
    rows = []
    for row in manifest.get("symbol_diagnostics", []):
        rows.append({key: row.get(key) for key in allowed if key in row})
    return rows


def _min_non_empty(left: Any, right: Any) -> Any:
    if left in {None, ""}:
        return right
    if right in {None, ""}:
        return left
    return min(str(left), str(right))


def _max_non_empty(left: Any, right: Any) -> Any:
    if left in {None, ""}:
        return right
    if right in {None, ""}:
        return left
    return max(str(left), str(right))


def _max_number(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    try:
        return max(float(left), float(right))
    except (TypeError, ValueError):
        return left


def _sanitize_metadata(value: Any, key: str = "") -> Any:
    if _is_secret_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {item_key: _sanitize_metadata(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_assignments(value)
    return value


def _sanitize_command(command: list[str] | str) -> list[str] | str:
    if isinstance(command, str):
        return _redact_secret_assignments(command)
    sanitized = []
    redact_next = False
    for token in command:
        text = str(token)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if "=" in text:
            name, value = text.split("=", 1)
            if _is_secret_key(name):
                sanitized.append(f"{name}=<redacted>")
            else:
                sanitized.append(_redact_secret_assignments(text))
            continue
        sanitized.append(_redact_secret_assignments(text))
        if text.startswith("-") and _is_secret_key(text):
            redact_next = True
    return sanitized


def _is_secret_key(key: str) -> bool:
    return bool(re.search(r"(api[-_]?key|token|secret|password|credential|auth)", key, flags=re.IGNORECASE))


def _redact_secret_assignments(value: str) -> str:
    return re.sub(
        r"(?i)\b(api[-_]?key|token|secret|password|credential|auth)(=)[^\s&]+",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        value,
    )
