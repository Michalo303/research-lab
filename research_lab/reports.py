from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from research_lab.config import REAL_EOD_DATA_SOURCES
from research_lab.drawdown_diagnostics import drawdown_diagnostics_for_result
from research_lab.hermes.artifacts import latest_hermes_artifact
from research_lab.research_orchestrator import build_research_guidance, summarize_recent_failures


ACCEPTED_TIERS = {"A", "B"}

GUIDANCE_CATEGORY_ORDER = [
    "data quality/fallback",
    "risk/drawdown",
    "unseen return weakness",
    "walk-forward robustness",
    "promotion gate",
]


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
    accepted = [r for r in results if r["tier"] in ACCEPTED_TIERS]
    non_accepted = [r for r in results if r["tier"] not in ACCEPTED_TIERS]
    rejected = [r for r in results if r["tier"] == "Rejected"]
    best = max(results, key=lambda r: r["split_metrics"]["unseen"]["mar"]) if results else None
    sources = sorted({r["data_manifest"]["source"] for r in results})
    data_source_summary = _data_source_summary(results)
    source_note = _source_note(results)
    next_actions = _next_actions(results)
    rejection_diagnostics = _rejection_diagnostics_rows(non_accepted)
    orchestrator_guidance = _orchestrator_guidance_lines(results)
    next_research_guidance = build_next_research_guidance(results)
    drawdown_diagnostics = _drawdown_diagnostics_rows(results)
    rejection_drawdown_attribution = _rejection_drawdown_attribution_rows(rejected)
    walk_forward_diagnostics = _bounded_walk_forward_diagnostics(results)
    compact_funnel = build_daily_experiment_funnel(results, run_metadata.get("daily_experiment_selection") if run_metadata else None)
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
        f"- data-source summary: {data_source_summary['summary_text']}",
        "",
        *_hermes_report_lines(run_metadata.get("hermes") if run_metadata else None),
        "",
        "## Compact Funnel",
        "",
        *render_daily_experiment_funnel(compact_funnel),
        "",
        "## New Strategies Tested",
        "",
        *rows,
        "",
        "## Important Findings",
        "",
        "- The deterministic runner, registry, leaderboard, and strategy-card pipeline are now operational.",
        f"- {source_note}",
        f"- {data_source_summary['summary_text']}",
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
        "## Orchestrator Guidance",
        "",
        *orchestrator_guidance,
        "",
        "## Next Research Guidance",
        "",
        *next_research_guidance,
        "",
        *_bounded_walk_forward_diagnostics_lines(walk_forward_diagnostics),
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


def build_daily_experiment_funnel(results: list[dict], selection: dict[str, Any] | None = None) -> dict[str, Any]:
    selection = selection or {}
    selection_mode = str(selection.get("selection_mode") or "unknown")
    queue_inspected = bool(selection.get("queue_inspected", False))
    queue_consumed = bool(selection.get("queue_consumed", selection.get("queue_rows_consumed", False)))
    candidate_source = str(selection.get("candidate_source") or "unspecified")
    selector_counts = {
        "proposed": _safe_int(selection.get("proposed"), 0),
        "family_filtered": _safe_int(selection.get("family_filtered"), 0),
        "source_filtered": _safe_int(selection.get("source_filtered"), 0),
        "invalid_filtered": _safe_int(selection.get("invalid_filtered"), 0),
        "recent_duplicate_skipped": _safe_int(selection.get("recent_duplicate_skipped"), 0),
        "in_batch_duplicate_skipped": _safe_int(selection.get("in_batch_duplicate_skipped"), 0),
        "budget_skipped": _safe_int(selection.get("budget_skipped"), 0),
        "selected": _safe_int(selection.get("selected", selection.get("budget_selected")), 0),
    }
    execution_counts = {
        "attempted": _safe_int(selection.get("attempted"), len(results)),
        "completed": _safe_int(selection.get("completed"), len(results)),
        "missing_data_skipped": _safe_int(selection.get("missing_data_skipped"), 0),
    }
    result_diagnostics = {
        "positive_oos": sum(1 for result in results if _safe_float(result.get("split_metrics", {}).get("unseen", {}).get("cagr"), 0.0) > 0.0),
        "tier_drawdown_pass_15pct": sum(1 for result in results if _safe_float(result.get("split_metrics", {}).get("unseen", {}).get("max_drawdown"), -1.0) >= -0.15),
        "recovery_drawdown_pass_10pct": sum(1 for result in results if _safe_float(result.get("split_metrics", {}).get("unseen", {}).get("max_drawdown"), -1.0) >= -0.10),
        "walk_forward_pass": sum(1 for result in results if isinstance(result.get("walk_forward"), dict) and _walk_forward_failure(result["walk_forward"]) is None),
        "cost_pass": sum(1 for result in results if result.get("cost_stress", {}).get("survives_double_cost") is True),
        "tier_ab": sum(1 for result in results if result.get("tier") in ACCEPTED_TIERS),
        "deployment_gate_pass": sum(1 for result in results if result.get("deployment_gate", {}).get("passed") is True),
    }
    rejection_reasons: dict[str, int] = {}
    for result in results:
        if result.get("tier") in ACCEPTED_TIERS:
            continue
        for reason in build_rejection_diagnostics(result):
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    return {
        "selector_counts": selector_counts,
        "execution_counts": execution_counts,
        "execution_failure_contract": "fail_fast_no_completed_report",
        "result_diagnostics": result_diagnostics,
        "rejection_reasons": rejection_reasons,
        "selection_mode": selection_mode,
        "queue_inspected": queue_inspected,
        "queue_consumed": queue_consumed,
        "queue_rows_consumed": queue_consumed,
        "candidate_source": candidate_source,
        "candidate_scope_note": (
            "selector counts are mutually exclusive terminal outcomes; candidates came from the internal seven-day recovery manifest"
            if selection_mode == "bounded_recovery"
            else "selector counts are mutually exclusive terminal outcomes; the normal baseline/guided/queue path inspected queue rows without consuming them"
        ),
        "result_scope_note": "execution counts reconcile selected candidates; result diagnostics are overlapping, non-exclusive counts over completed results",
    }


def render_daily_experiment_funnel(funnel: dict[str, Any]) -> list[str]:
    selector_counts = funnel.get("selector_counts", {})
    execution_counts = funnel.get("execution_counts", {})
    result_diagnostics = funnel.get("result_diagnostics", {})
    rejection_reasons = funnel.get("rejection_reasons", {})
    rows = [
        "| stage | scope | count |",
        "|---|---|---:|",
    ]
    for stage in (
        "proposed",
        "family_filtered",
        "source_filtered",
        "invalid_filtered",
        "recent_duplicate_skipped",
        "in_batch_duplicate_skipped",
        "budget_skipped",
        "selected",
    ):
        rows.append(f"| {stage} | selector outcome | {_safe_int(selector_counts.get(stage), 0)} |")
    for stage in ("attempted", "completed", "missing_data_skipped"):
        rows.append(f"| {stage} | execution | {_safe_int(execution_counts.get(stage), 0)} |")
    for stage in ("positive_oos", "tier_drawdown_pass_15pct", "recovery_drawdown_pass_10pct", "walk_forward_pass", "cost_pass", "tier_ab", "deployment_gate_pass"):
        rows.append(f"| {stage} | independent diagnostic | {_safe_int(result_diagnostics.get(stage), 0)} |")
    rejection_text = (
        "; ".join(f"{reason}={count}" for reason, count in sorted(rejection_reasons.items()))
        if rejection_reasons
        else "none"
    )
    rows.extend(
        [
            "",
            f"- candidate semantics: {funnel.get('candidate_scope_note', '')}",
            f"- selection mode: {funnel.get('selection_mode', 'unknown')}",
            f"- candidate source: {funnel.get('candidate_source', 'unspecified')}",
            f"- queue inspected: {str(bool(funnel.get('queue_inspected', False))).lower()}",
            f"- queue consumed: {str(bool(funnel.get('queue_consumed', False))).lower()}",
            f"- result semantics: {funnel.get('result_scope_note', '')}",
            "- execution failure contract: execution exceptions abort the run; no completed daily report is written",
            f"- queue rows consumed: {str(bool(funnel.get('queue_rows_consumed', False))).lower()}",
            f"- rejection_reasons: {rejection_text}",
        ]
    )
    return rows


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
    extra_metadata: dict[str, Any] | None = None,
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
            "data_source_summary": _data_source_summary(results),
            "provider_history_summary": _provider_history_summary(results),
            "walk_forward_diagnostics": _bounded_walk_forward_diagnostics(results),
            "hermes": _hermes_metadata(latest_hermes_artifact(root, before=timestamp_utc)),
            **(extra_metadata or {}),
        }
    )
    metadata["daily_experiment_funnel"] = build_daily_experiment_funnel(
        results,
        metadata.get("daily_experiment_selection"),
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


def _hermes_metadata(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not artifact:
        return None
    allowed = (
        "run_id",
        "timestamp_utc",
        "provider",
        "status",
        "generated_hypotheses_count",
        "imported_hypotheses_count",
        "rejected_hypotheses_count",
        "rejection_reasons",
        "imported_hypothesis_ids",
        "input_report_path",
        "dominant_blocker",
        "artifact_path",
    )
    return {key: artifact.get(key) for key in allowed}


def _hermes_report_lines(hermes: dict[str, Any] | None) -> list[str]:
    lines = ["## Hermes Pre-Research Stage", ""]
    if not hermes:
        return [*lines, "- Hermes ran: no", "- status: no eligible Hermes artifact found before this daily run"]
    reasons = hermes.get("rejection_reasons")
    if not isinstance(reasons, list):
        reasons = []
    imported_ids = hermes.get("imported_hypothesis_ids")
    if not isinstance(imported_ids, list):
        imported_ids = []
    return [
        *lines,
        "- Hermes ran: yes",
        f"- provider: {hermes.get('provider', '')}",
        f"- status: {hermes.get('status', '')}",
        f"- generated hypotheses: {hermes.get('generated_hypotheses_count', 0)}",
        f"- imported hypotheses: {hermes.get('imported_hypotheses_count', 0)}",
        f"- rejected hypotheses: {hermes.get('rejected_hypotheses_count', 0)}",
        f"- rejection reasons: {'; '.join(str(reason) for reason in reasons) if reasons else 'none'}",
        f"- imported hypothesis IDs: {', '.join(str(item) for item in imported_ids) if imported_ids else 'none'}",
        f"- artifact: {hermes.get('artifact_path', '')}",
    ]


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
        "| strategy_id | tier | tier_reason | rejection_reasons | failed_metric | actual_value | required_threshold |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in rejected:
        diagnostic = _rejection_diagnostic(result)
        rows.append(
            "| {strategy_id} | {tier} | {tier_reason} | {reasons} | {metric} | {actual} | {threshold} |".format(
                strategy_id=_markdown_cell(result["strategy_id"]),
                tier=_markdown_cell(result.get("tier", "")),
                tier_reason=_markdown_cell(result.get("tier_reason", "")),
                reasons=_markdown_cell(diagnostic["reasons"]),
                metric=diagnostic["metric"],
                actual=diagnostic["actual"],
                threshold=diagnostic["threshold"],
            )
        )
    return rows


def build_rejection_diagnostics(result: dict) -> list[str]:
    if result.get("tier") in ACCEPTED_TIERS:
        return []
    return [failure["reason"] for failure in _rejection_failures(result)]


def build_next_research_guidance(results: list[dict]) -> list[str]:
    signals = _next_research_guidance_signals(results)
    source_summary = _data_source_summary(results)
    if not signals:
        return [
            "- dominant blocker category: inconclusive",
            "- next research direction: guidance is limited because there are no rejected or non-accepted strategies with usable diagnostics.",
            "- blocker mix: none",
            f"- data quality: {_guidance_data_quality_note(0, source_summary)}",
            "- confidence: insufficient diagnostic signal; do not infer a research direction from this run.",
        ]

    counts: dict[str, int] = {}
    strategies_by_category: dict[str, set[str]] = {}
    for signal in signals:
        category = signal["category"]
        strategy_id = signal["strategy_id"]
        counts[category] = counts.get(category, 0) + 1
        strategies_by_category.setdefault(category, set()).add(strategy_id)

    ordered_categories = _ordered_guidance_categories(counts)
    dominant = ordered_categories[0]
    dominant_count = counts[dominant]
    dominant_strategy_count = len(strategies_by_category.get(dominant, set()))
    data_quality_strategy_count = len(strategies_by_category.get("data quality/fallback", set()))

    guidance = [
        (
            f"- dominant blocker category: {dominant} "
            f"({dominant_count} {_pluralize('signal', dominant_count)} across "
            f"{dominant_strategy_count} {_pluralize('strategy', dominant_strategy_count)})"
        ),
        f"- next research direction: {_guidance_direction(dominant)}",
        f"- blocker mix: {_guidance_blocker_mix(counts)}",
        f"- data quality: {_guidance_data_quality_note(data_quality_strategy_count, source_summary)}",
        "- confidence: enough diagnostic signals for conservative next-step guidance.",
    ]
    near_miss = _best_near_miss_mutation_target(results)
    if near_miss:
        guidance.extend(
            [
                f"- near-miss mutation target: {near_miss['strategy_id']}",
                (
                    "- conservative mutation brief: preserve trend + volatility cap structure; search for lower drawdown and higher "
                    "walk-forward pass rate without relaxing promotion gates."
                ),
            ]
        )
    return guidance


def _orchestrator_guidance_lines(results: list[dict]) -> list[str]:
    guidance = build_research_guidance(summarize_recent_failures(results))
    source_summary = _data_source_summary(results)
    lines = [
        f"- dominant blocker category: {guidance.dominant_blocker_category}",
        f"- blocker mix: {_format_orchestrator_blocker_mix(guidance.blocker_mix)}",
        f"- promotion blocked by data quality: {str(guidance.promotion_blocked).lower()}",
        f"- confidence: {guidance.confidence}",
        f"- data-source summary: {source_summary['summary_text']}",
    ]
    for direction in guidance.prioritized_next_directions:
        features = ", ".join(direction.required_features) if direction.required_features else "none"
        lines.append(f"- prioritized direction: {direction.name} - {direction.rationale}; required_features={features}")
    if guidance.deprioritized_candidate_types:
        rendered = "; ".join(
            f"{penalty.pattern_key} score={penalty.score}" for penalty in guidance.deprioritized_candidate_types[:5]
        )
        lines.append(f"- deprioritized candidate types: {rendered}")
    else:
        lines.append("- deprioritized candidate types: none")
    if guidance.data_quality_limitations:
        lines.append(f"- data quality limitations: {'; '.join(guidance.data_quality_limitations)}")
    else:
        lines.append("- data quality limitations: none")
    return lines


def _format_orchestrator_blocker_mix(blocker_mix: dict[str, int]) -> str:
    if not blocker_mix:
        return "none"
    return "; ".join(f"{category}={count}" for category, count in blocker_mix.items())


def _next_research_guidance_signals(results: list[dict]) -> list[dict[str, str]]:
    signals = []
    for result in results:
        if result.get("tier") in ACCEPTED_TIERS:
            continue
        strategy_id = str(result.get("strategy_id") or "unknown")
        for failure in _rejection_failures(result):
            category = _guidance_category_for_failure(failure)
            if category == "data quality/fallback" and _is_structural_intraday_synthetic_auxiliary_result(result):
                continue
            if category:
                signals.append({"strategy_id": strategy_id, "category": category})
    return signals


def _guidance_category_for_failure(failure: dict[str, Any]) -> str | None:
    reason = str(failure.get("reason") or "")
    if reason in {"missing required provider data", "synthetic/fallback data used", "insufficient real data history"}:
        return "data quality/fallback"
    if reason in {"max drawdown too deep", "failed cost stress"}:
        return "risk/drawdown"
    if reason in {"validation return below threshold", "unseen return below threshold"}:
        return "unseen return weakness"
    if reason in {"insufficient walk-forward robustness", "too few unseen trades"}:
        return "walk-forward robustness"
    if reason in {"failed promotion gate", "no accepted tier reached"}:
        return "promotion gate"
    return None


def _ordered_guidance_categories(counts: dict[str, int]) -> list[str]:
    priority = {category: index for index, category in enumerate(GUIDANCE_CATEGORY_ORDER)}
    return sorted(counts, key=lambda category: (-counts[category], priority.get(category, len(priority)), category))


def _guidance_direction(category: str) -> str:
    directions = {
        "data quality/fallback": "fix provider coverage, fallback usage, or real-history limits before interpreting strategy performance.",
        "risk/drawdown": "prioritize lower-drawdown and cost-robust variants before relaxing any risk or promotion gates.",
        "unseen return weakness": "prioritize ideas with positive validation and unseen CAGR before relaxing any risk or promotion gates.",
        "walk-forward robustness": "prioritize rolling out-of-sample robustness and sufficient unseen trade samples before promoting candidates.",
        "promotion gate": "inspect the concrete promotion-gate diagnostics before choosing a strategy-family direction.",
    }
    return directions.get(category, "guidance is limited because no supported blocker category dominates.")


def _guidance_blocker_mix(counts: dict[str, int]) -> str:
    ordered = _ordered_guidance_categories(counts)
    if not ordered:
        return "none"
    return "; ".join(f"{category}={counts[category]}" for category in ordered)


def _guidance_data_quality_note(strategy_count: int, source_summary: dict[str, Any]) -> str:
    if source_summary.get("classification") == "mixed_real_eod_with_synthetic_intraday_auxiliary":
        return "mixed-source run: ETF universe uses real EOD data without fallback; intraday synthetic auxiliary candidates remain promotion-blocked."
    if strategy_count:
        return (
            f"synthetic/fallback diagnostics present in {strategy_count} "
            f"{_pluralize('strategy', strategy_count)}; treat guidance as data-quality limited."
        )
    return "no synthetic/fallback data signal in rejection diagnostics."


def _best_near_miss_mutation_target(results: list[dict]) -> dict | None:
    targets = [result for result in results if _is_near_miss_trend_vol_cap_result(result)]
    if not targets:
        return None
    return sorted(
        targets,
        key=lambda result: (
            -_safe_float(result.get("walk_forward", {}).get("pass_rate"), 0.0),
            -_safe_float(result.get("split_metrics", {}).get("unseen", {}).get("cagr"), 0.0),
            -_safe_float(result.get("split_metrics", {}).get("unseen", {}).get("max_drawdown"), -1.0),
            str(result.get("strategy_id") or ""),
        ),
    )[0]


def _is_near_miss_trend_vol_cap_result(result: dict) -> bool:
    if result.get("tier") != "C":
        return False
    strategy_id = str(result.get("strategy_id") or "")
    short_name = str(result.get("short_name") or _short_name_from_strategy_id(strategy_id))
    builder = str(result.get("builder") or "")
    if result.get("family") != "LONGTERM":
        return False
    if short_name != "TREND_VOL_CAP" and builder != "long_term_vol_target_cap":
        return False
    split = result.get("split_metrics", {})
    if _safe_float(split.get("train", {}).get("cagr"), 0.0) <= 0:
        return False
    if _safe_float(split.get("validation", {}).get("cagr"), 0.0) <= 0:
        return False
    unseen = split.get("unseen", {})
    if _safe_float(unseen.get("cagr"), 0.0) <= 0:
        return False
    if _safe_float(unseen.get("max_drawdown"), -1.0) < -0.15:
        return False
    walk_forward = result.get("walk_forward", {})
    return (
        isinstance(walk_forward, dict)
        and walk_forward.get("method") == "true_rolling_oos"
        and walk_forward.get("status") == "ok"
        and 0.50 <= _safe_float(walk_forward.get("pass_rate"), 0.0) < 0.67
    )


def _bounded_walk_forward_diagnostics(results: list[dict]) -> list[dict[str, Any]]:
    diagnostics = []
    for result in results:
        diagnostic = _bounded_walk_forward_diagnostic(result)
        if diagnostic:
            diagnostics.append(diagnostic)
    return diagnostics


def _bounded_walk_forward_diagnostic(result: dict[str, Any]) -> dict[str, Any] | None:
    if str(result.get("asset_class") or "") != "ETF" or not _is_near_miss_trend_vol_cap_result(result):
        return None
    walk_forward = result.get("walk_forward")
    if not isinstance(walk_forward, dict):
        return None
    if walk_forward.get("method") != "true_rolling_oos" or walk_forward.get("status") != "ok":
        return None

    pass_rate_raw = walk_forward.get("pass_rate")
    median_test_cagr_raw = walk_forward.get("median_test_cagr")
    worst_test_drawdown_raw = walk_forward.get("worst_test_drawdown")
    if not _is_number(pass_rate_raw) or not _is_number(median_test_cagr_raw) or not _is_number(worst_test_drawdown_raw):
        return None

    windows = walk_forward.get("windows")
    window_count = _resolved_total_windows(walk_forward)
    passed_windows = _resolved_passed_windows(walk_forward, windows)
    if window_count <= 0 or passed_windows < 0:
        return None

    all_failed_windows = _failed_windows(windows if isinstance(windows, list) else [])
    failed_windows = _worst_failed_windows(all_failed_windows)

    diagnostic: dict[str, Any] = {
        "strategy_id": str(result.get("strategy_id") or ""),
        "window_count": window_count,
        "passed_windows": passed_windows,
        "total_windows": window_count,
        "pass_rate": float(pass_rate_raw),
        "required_pass_rate": 0.67,
        "median_test_cagr": float(median_test_cagr_raw),
        "worst_test_drawdown": float(worst_test_drawdown_raw),
        "failed_window_count": max(window_count - passed_windows, 0),
        "worst_failed_windows": failed_windows,
    }
    regime_summary = walk_forward.get("regime_summary")
    if isinstance(regime_summary, str) and regime_summary.strip():
        diagnostic["regime_summary"] = regime_summary.strip()
    return diagnostic


def _resolved_total_windows(walk_forward: dict[str, Any]) -> int:
    total_windows = _safe_int(walk_forward.get("total_windows"), 0)
    if total_windows > 0:
        return total_windows
    return _safe_int(walk_forward.get("window_count"), 0)


def _resolved_passed_windows(walk_forward: dict[str, Any], windows: Any) -> int:
    passed_windows = _safe_int(walk_forward.get("passed_windows"), -1)
    if passed_windows >= 0:
        return passed_windows
    if isinstance(windows, list):
        return sum(1 for window in windows if bool(window.get("passed")))
    return -1


def _failed_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for window in windows:
        if bool(window.get("passed")):
            continue
        failures.append(
            {
                "window": _safe_int(window.get("window"), 0),
                "test_start": str(window.get("test_start") or ""),
                "test_end": str(window.get("test_end") or ""),
                "regime": str(window.get("regime") or "unknown"),
                "test_cagr": _safe_float(window.get("test_cagr"), 0.0),
                "test_max_drawdown": _safe_float(window.get("test_max_drawdown"), 0.0),
            }
        )
    return failures


def _worst_failed_windows(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        failures,
        key=lambda window: (
            _safe_float(window.get("test_max_drawdown"), 0.0),
            _safe_float(window.get("test_cagr"), 0.0),
            _safe_int(window.get("window"), 0),
        ),
    )[:3]


def _short_name_from_strategy_id(strategy_id: str) -> str:
    marker = "_1D_"
    if marker not in strategy_id:
        return ""
    tail = strategy_id.split(marker, 1)[1]
    parts = tail.split("_")
    if len(parts) <= 2:
        return tail
    return "_".join(parts[:-2])


def _pluralize(word: str, count: int) -> str:
    if word == "strategy":
        return word if count == 1 else "strategies"
    return word if count == 1 else f"{word}s"


def _rejection_diagnostic(result: dict) -> dict:
    failures = _rejection_failures(result)
    primary = failures[0] if failures else _fallback_rejection_failure(result)
    return {
        "reasons": "; ".join(failure["reason"] for failure in failures) if failures else primary["reason"],
        "metric": primary["metric"],
        "actual": primary["actual"],
        "threshold": primary["threshold"],
    }


def _rejection_failures(result: dict) -> list[dict]:
    unseen = result.get("split_metrics", {}).get("unseen", {})
    validation = result.get("split_metrics", {}).get("validation", {})
    cost_stress = result.get("cost_stress", {})
    data_manifest = result.get("data_manifest", {})
    walk_forward = result.get("walk_forward")
    family = result.get("family", "")
    failures = []
    validation_cagr = _safe_float(validation.get("cagr"), 0.0)
    cagr = _safe_float(unseen.get("cagr"), 0.0)
    max_drawdown = _safe_float(unseen.get("max_drawdown"), 0.0)
    trade_count = _safe_int(unseen.get("trade_count"), 0)
    source = str(data_manifest.get("source") or result.get("data_source") or "")
    years = _safe_float(data_manifest.get("years", result.get("history_length")), 0.0)
    fallback_used = bool(data_manifest.get("fallback_used") or result.get("fallback_used"))
    fallback_reason = str(data_manifest.get("fallback_reason") or result.get("fallback_reason") or "")

    if validation_cagr <= 0:
        failures.append(_failure("validation return below threshold", "validation_cagr", _format_percent(validation_cagr), "> 0.00%"))
    if cagr <= 0:
        failures.append(_failure("unseen return below threshold", "unseen_cagr", _format_percent(cagr), "> 0.00%"))
    if max_drawdown < -0.15:
        failures.append(
            _failure(
                "max drawdown too deep",
                "unseen_max_drawdown",
                _format_percent(max_drawdown),
                ">= -15.00%",
            )
        )
    if family in {"SWING", "INTRADAY"} and trade_count < 100:
        failures.append(
            _failure(
                "too few unseen trades",
                "unseen_trades",
                str(trade_count),
                ">= 100",
            )
        )
    if not bool(cost_stress.get("survives_double_cost", True)):
        failures.append(
            _failure(
                "failed cost stress",
                "double_cost_unseen_cagr",
                _format_percent(_safe_float(cost_stress.get("double_unseen_cagr"), 0.0)),
                "> 0.00%",
            )
        )
    if isinstance(walk_forward, dict):
        walk_forward_failure = _walk_forward_failure(walk_forward)
        if walk_forward_failure:
            failures.append(walk_forward_failure)
    if fallback_reason or result.get("missing_symbols") or data_manifest.get("missing_symbols"):
        failures.append(
            _failure(
                "missing required provider data",
                "provider_data",
                fallback_reason or _format_missing_symbols(result, data_manifest),
                "all required symbols present from requested provider",
            )
        )
    if fallback_used or source not in REAL_EOD_DATA_SOURCES:
        failures.append(
            _failure(
                "synthetic/fallback data used",
                "data_source",
                f"{source or 'unknown'}; fallback_used={fallback_used}",
                "real production EOD provider without fallback",
            )
        )
    if _insufficient_history(family, years):
        failures.append(
            _failure(
                "insufficient real data history",
                "data_years",
                f"{years:.1f}",
                _history_threshold(family),
            )
        )
    if result.get("tier") not in {"Rejected", *ACCEPTED_TIERS}:
        failures.append(_failure("failed promotion gate", "tier", str(result.get("tier", "")), "A or B"))
        failures.append(_failure("no accepted tier reached", "tier", str(result.get("tier", "")), "A or B"))
    return failures


def _walk_forward_failure(walk_forward: dict) -> dict | None:
    method = walk_forward.get("method")
    status = walk_forward.get("status")
    pass_rate = _safe_float(walk_forward.get("pass_rate"), 0.0)
    window_count = _safe_int(walk_forward.get("window_count"), 0)
    median_test_cagr = _safe_float(walk_forward.get("median_test_cagr"), 0.0)
    worst_test_drawdown = _safe_float(walk_forward.get("worst_test_drawdown"), -1.0)
    if method != "true_rolling_oos":
        return _failure("insufficient walk-forward robustness", "walk_forward_method", str(method), "true_rolling_oos")
    if status != "ok":
        return _failure("insufficient walk-forward robustness", "walk_forward_status", str(status), "ok")
    if pass_rate < 0.67:
        return _failure("insufficient walk-forward robustness", "walk_forward_pass_rate", _format_percent(pass_rate), ">= 67.00%")
    if window_count < 3:
        return _failure("insufficient walk-forward robustness", "walk_forward_windows", str(window_count), ">= 3")
    if median_test_cagr <= 0:
        return _failure("insufficient walk-forward robustness", "walk_forward_median_test_cagr", _format_percent(median_test_cagr), "> 0.00%")
    if worst_test_drawdown < -0.20:
        return _failure("insufficient walk-forward robustness", "walk_forward_worst_drawdown", _format_percent(worst_test_drawdown), ">= -20.00%")
    return None


def _format_missing_symbols(result: dict, data_manifest: dict) -> str:
    missing = result.get("missing_symbols") or data_manifest.get("missing_symbols") or []
    if isinstance(missing, list):
        return ", ".join(str(symbol) for symbol in missing) or "unknown"
    return str(missing)


def _insufficient_history(family: str, years: float) -> bool:
    if family in {"LONGTERM", "ROTATION"}:
        return years < 10.0
    if family == "SWING":
        return years < 3.0
    return False


def _history_threshold(family: str) -> str:
    if family in {"LONGTERM", "ROTATION"}:
        return ">= 10.0 years"
    if family == "SWING":
        return ">= 3.0 years"
    return "sufficient history"


def _fallback_rejection_failure(result: dict) -> dict:
    return _failure(
        result.get("tier_reason", "Rejected by tiering logic."),
        "tier_reason",
        result.get("tier_reason", ""),
        "non-rejected tier",
    )


def _failure(reason: str, metric: str, actual: str, threshold: str) -> dict:
    return {"reason": reason, "metric": metric, "actual": actual, "threshold": threshold}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|")


def _format_percent(value: float) -> str:
    return f"{value:.2%}"


def _format_optional_date(value: Any) -> str:
    return str(value) if value else "none"


def _format_recovery_date(diagnostic: dict[str, Any]) -> str:
    if diagnostic.get("worst_drawdown_recovery"):
        return str(diagnostic["worst_drawdown_recovery"])
    return "unrecovered" if diagnostic.get("worst_drawdown_start") else "none"


def _source_note(results: list[dict]) -> str:
    source_summary = _data_source_summary(results)
    if source_summary["classification"] == "mixed_real_eod_with_synthetic_intraday_auxiliary":
        years = max(float(r["data_manifest"].get("years", 0.0)) for r in results if r["data_manifest"]["source"] == "eodhd")
        return (
            f"EODHD real EOD data is enabled with {years:.1f} years of available history for the ETF universe; "
            "the synthetic intraday auxiliary path remains promotion-blocked."
        )
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


def _data_source_summary(results: list[dict]) -> dict[str, Any]:
    real_eod_candidate_count = 0
    synthetic_candidate_count = 0
    synthetic_intraday_auxiliary_count = 0
    provider_fallback_candidate_count = 0
    for result in results:
        manifest = result.get("data_manifest", {})
        source = str(manifest.get("source") or result.get("data_source") or "").strip().lower()
        if source in REAL_EOD_DATA_SOURCES:
            real_eod_candidate_count += 1
        else:
            synthetic_candidate_count += 1
            if _is_structural_intraday_synthetic_auxiliary_result(result):
                synthetic_intraday_auxiliary_count += 1
        if bool(manifest.get("fallback_used") or result.get("fallback_used")) or manifest.get("fallback_reason") or result.get("fallback_reason"):
            provider_fallback_candidate_count += 1

    data_quality_promotion_block_count = synthetic_candidate_count + provider_fallback_candidate_count
    classification = "all_synthetic"
    summary_text = "Synthetic data cannot validate capital allocation; real-provider evidence is still required."
    if real_eod_candidate_count and synthetic_intraday_auxiliary_count and synthetic_candidate_count == synthetic_intraday_auxiliary_count and provider_fallback_candidate_count == 0:
        classification = "mixed_real_eod_with_synthetic_intraday_auxiliary"
        summary_text = (
            "ETF universe: eodhd, no fallback; "
            "Intraday BTCUSDT: synthetic auxiliary path; "
            "Synthetic candidates are not promotion-eligible."
        )
    elif provider_fallback_candidate_count > 0:
        classification = "provider_fallback_present"
        summary_text = "Provider fallback detected; affected candidates are not promotion-eligible until real-provider coverage is restored."
    elif real_eod_candidate_count and synthetic_candidate_count == 0:
        classification = "all_real_eod"
        summary_text = "ETF universe: eodhd, no fallback."

    return {
        "classification": classification,
        "summary_text": summary_text,
        "real_eod_candidate_count": real_eod_candidate_count,
        "synthetic_candidate_count": synthetic_candidate_count,
        "synthetic_intraday_auxiliary_count": synthetic_intraday_auxiliary_count,
        "provider_fallback_candidate_count": provider_fallback_candidate_count,
        "data_quality_promotion_block_count": data_quality_promotion_block_count,
    }


def _is_structural_intraday_synthetic_auxiliary_result(result: dict[str, Any]) -> bool:
    if str(result.get("family") or "") != "INTRADAY":
        return False
    data_manifest = result.get("data_manifest", {})
    source = str(data_manifest.get("source") or result.get("data_source") or "").strip().lower()
    if source in REAL_EOD_DATA_SOURCES:
        return False
    if bool(data_manifest.get("fallback_used") or result.get("fallback_used")):
        return False
    if data_manifest.get("fallback_reason") or result.get("fallback_reason"):
        return False
    if data_manifest.get("missing_symbols") or result.get("missing_symbols"):
        return False
    return True


def _run_metadata_lines(metadata: dict[str, Any]) -> list[str]:
    git = metadata.get("git", {})
    lines = [
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
    dedupe = metadata.get("queued_candidate_dedupe")
    if isinstance(dedupe, dict):
        lines.extend(
            [
                f"- queued_candidate_dedupe_input_count: {dedupe.get('input_count', 0)}",
                f"- queued_candidate_dedupe_retained_count: {dedupe.get('retained_count', 0)}",
                f"- queued_candidate_dedupe_selected_count: {dedupe.get('selected_count', dedupe.get('retained_count', 0))}",
                f"- queued_candidate_dedupe_skipped_count: {dedupe.get('skipped_count', 0)}",
                f"- queued_candidate_dedupe_non_executable_count: {dedupe.get('non_executable_count', 0)}",
                f"- queued_candidate_dedupe_risk_filtered_count: {dedupe.get('risk_filtered_count', 0)}",
                f"- queued_candidate_dedupe_reasons: {dedupe.get('reasons', {})}",
            ]
        )
    selection = metadata.get("daily_experiment_selection")
    if isinstance(selection, dict):
        lines.extend(
            [
                f"- daily_experiment_budget: {selection.get('budget', 0)}",
                f"- daily_experiment_recent_window: {selection.get('recent_window', 0)}",
                f"- daily_experiment_proposed: {selection.get('proposed', 0)}",
                f"- daily_experiment_family_filtered: {selection.get('family_filtered', 0)}",
                f"- daily_experiment_source_filtered: {selection.get('source_filtered', 0)}",
                f"- daily_experiment_invalid_filtered: {selection.get('invalid_filtered', 0)}",
                f"- daily_experiment_recent_duplicate_skipped: {selection.get('recent_duplicate_skipped', 0)}",
                f"- daily_experiment_in_batch_duplicate_skipped: {selection.get('in_batch_duplicate_skipped', 0)}",
                f"- daily_experiment_budget_skipped: {selection.get('budget_skipped', 0)}",
                f"- daily_experiment_selected: {selection.get('selected', selection.get('budget_selected', 0))}",
                f"- daily_experiment_selection_mode: {selection.get('selection_mode', 'unknown')}",
                f"- daily_experiment_candidate_source: {selection.get('candidate_source', 'unspecified')}",
                f"- daily_experiment_queue_inspected: {selection.get('queue_inspected', False)}",
                f"- daily_experiment_queue_consumed: {selection.get('queue_consumed', selection.get('queue_rows_consumed', False))}",
                f"- daily_experiment_queue_rows_consumed: {selection.get('queue_rows_consumed', False)}",
            ]
        )
    funnel = metadata.get("daily_experiment_funnel")
    if isinstance(funnel, dict):
        selector_counts = funnel.get("selector_counts", {})
        execution_counts = funnel.get("execution_counts", {})
        result_diagnostics = funnel.get("result_diagnostics", {})
        lines.extend(
            [
                f"- compact_funnel_selector_counts: {selector_counts}",
                f"- compact_funnel_execution_counts: {execution_counts}",
                f"- compact_funnel_result_diagnostics: {result_diagnostics}",
                f"- compact_funnel_rejection_reasons: {funnel.get('rejection_reasons', {})}",
            ]
        )
    walk_forward_diagnostics = metadata.get("walk_forward_diagnostics")
    if isinstance(walk_forward_diagnostics, list) and walk_forward_diagnostics:
        lines.extend(["- walk_forward_diagnostics:"])
        for diagnostic in walk_forward_diagnostics:
            lines.extend(_walk_forward_diagnostic_run_metadata_lines(diagnostic))
    return lines


def _bounded_walk_forward_diagnostics_lines(diagnostics: list[dict[str, Any]]) -> list[str]:
    if not diagnostics:
        return []
    lines = ["## Bounded Walk-Forward Diagnostics", ""]
    for diagnostic in diagnostics:
        lines.extend(_walk_forward_diagnostic_report_lines(diagnostic))
    return lines


def _walk_forward_diagnostic_report_lines(diagnostic: dict[str, Any]) -> list[str]:
    lines = [
        f"- {diagnostic.get('strategy_id', '')}",
        f"  - windows: {diagnostic.get('passed_windows', 0)}/{diagnostic.get('total_windows', 0)} passed",
        f"  - pass_rate: {_format_percent(_safe_float(diagnostic.get('pass_rate'), 0.0))} (required: {_format_percent(_safe_float(diagnostic.get('required_pass_rate'), 0.0))})",
        f"  - median_test_cagr: {_format_percent(_safe_float(diagnostic.get('median_test_cagr'), 0.0))}",
        f"  - worst_test_drawdown: {_format_percent(_safe_float(diagnostic.get('worst_test_drawdown'), 0.0))}",
    ]
    regime_summary = diagnostic.get("regime_summary")
    if regime_summary:
        lines.append(f"  - regime_summary: {regime_summary}")
    lines.append(f"  - failed_windows: {diagnostic.get('failed_window_count', 0)}")
    for window in diagnostic.get("worst_failed_windows", []):
        lines.append(
            "  - window {window} {start}..{end} {regime} cagr={cagr} max_dd={max_dd}".format(
                window=_safe_int(window.get("window"), 0),
                start=window.get("test_start", ""),
                end=window.get("test_end", ""),
                regime=window.get("regime", "unknown"),
                cagr=_format_percent(_safe_float(window.get("test_cagr"), 0.0)),
                max_dd=_format_percent(_safe_float(window.get("test_max_drawdown"), 0.0)),
            )
        )
    lines.append("")
    return lines


def _walk_forward_diagnostic_run_metadata_lines(diagnostic: dict[str, Any]) -> list[str]:
    lines = [
        f"  - strategy_id: {diagnostic.get('strategy_id', '')}",
        f"    passed_windows: {diagnostic.get('passed_windows', 0)}/{diagnostic.get('total_windows', 0)}",
        f"    pass_rate: {_format_percent(_safe_float(diagnostic.get('pass_rate'), 0.0))}",
        f"    required_pass_rate: {_format_percent(_safe_float(diagnostic.get('required_pass_rate'), 0.0))}",
        f"    median_test_cagr: {_format_percent(_safe_float(diagnostic.get('median_test_cagr'), 0.0))}",
        f"    worst_test_drawdown: {_format_percent(_safe_float(diagnostic.get('worst_test_drawdown'), 0.0))}",
        f"    failed_window_count: {diagnostic.get('failed_window_count', 0)}",
    ]
    regime_summary = diagnostic.get("regime_summary")
    if regime_summary:
        lines.append(f"    regime_summary: {regime_summary}")
    for window in diagnostic.get("worst_failed_windows", []):
        lines.append(
            "    failed_window: window {window} {start}..{end} {regime} cagr={cagr} max_dd={max_dd}".format(
                window=_safe_int(window.get("window"), 0),
                start=window.get("test_start", ""),
                end=window.get("test_end", ""),
                regime=window.get("regime", "unknown"),
                cagr=_format_percent(_safe_float(window.get("test_cagr"), 0.0)),
                max_dd=_format_percent(_safe_float(window.get("test_max_drawdown"), 0.0)),
            )
        )
    return lines


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
