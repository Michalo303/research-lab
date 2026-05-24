from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any


ROBUSTNESS_COLUMNS = [
    "strategy_id",
    "family",
    "short_name",
    "tier",
    "data_source",
    "data_years",
    "positive_windows",
    "window_count",
    "walk_forward_score",
    "worst_window_cagr",
    "worst_window_drawdown",
    "unseen_cagr",
    "unseen_max_drawdown",
    "cost_survives",
    "robustness_verdict",
]

STABILITY_COLUMNS = [
    "family",
    "short_name",
    "data_source",
    "run_count",
    "positive_unseen_share",
    "cost_survival_share",
    "median_unseen_cagr",
    "worst_unseen_cagr",
    "median_unseen_drawdown",
    "tier_a_b_count",
    "rejected_count",
    "stability_verdict",
]


def load_backtest_results(root: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted((root / "backtests" / "runs").glob("*/result.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("strategy_id"):
            results.append(item)
    return results


def build_robustness_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [_robustness_row(item) for item in results]
    return sorted(rows, key=lambda row: (row["robustness_verdict"], row["walk_forward_score"], row["unseen_cagr"]), reverse=True)


def build_stability_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in results:
        key = (
            str(item.get("family", "")),
            str(item.get("short_name", "")),
            str(item.get("data_manifest", {}).get("source", "")),
        )
        groups.setdefault(key, []).append(item)

    rows = []
    for (family, short_name, data_source), items in groups.items():
        unseen_cagrs = [_metric(item, "unseen", "cagr") for item in items]
        unseen_dds = [_metric(item, "unseen", "max_drawdown") for item in items]
        positive_share = _share(value > 0 for value in unseen_cagrs)
        cost_share = _share(bool(item.get("cost_stress", {}).get("survives_double_cost")) for item in items)
        tier_a_b = sum(1 for item in items if item.get("tier") in {"A", "B"})
        rejected = sum(1 for item in items if item.get("tier") == "Rejected")
        row = {
            "family": family,
            "short_name": short_name,
            "data_source": data_source,
            "run_count": len(items),
            "positive_unseen_share": positive_share,
            "cost_survival_share": cost_share,
            "median_unseen_cagr": _median(unseen_cagrs),
            "worst_unseen_cagr": min(unseen_cagrs) if unseen_cagrs else 0.0,
            "median_unseen_drawdown": _median(unseen_dds),
            "tier_a_b_count": tier_a_b,
            "rejected_count": rejected,
            "stability_verdict": _stability_verdict(len(items), positive_share, cost_share, rejected),
        }
        rows.append(row)
    return sorted(rows, key=lambda row: (row["stability_verdict"], row["positive_unseen_share"], row["median_unseen_cagr"]), reverse=True)


def write_weekly_robustness_outputs(root: Path, report_stem: str) -> dict[str, Any]:
    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    results = load_backtest_results(root)
    robustness_rows = build_robustness_rows(results)
    stability_rows = build_stability_rows(results)
    robustness_path = report_dir / f"{report_stem}_robustness.csv"
    stability_path = report_dir / f"{report_stem}_stability.csv"
    _write_csv(robustness_path, robustness_rows, ROBUSTNESS_COLUMNS)
    _write_csv(stability_path, stability_rows, STABILITY_COLUMNS)
    return {
        "results": results,
        "robustness_rows": robustness_rows,
        "stability_rows": stability_rows,
        "robustness_path": robustness_path,
        "stability_path": stability_path,
    }


def summarize_weekly_robustness(robustness_rows: list[dict[str, Any]], stability_rows: list[dict[str, Any]]) -> list[str]:
    if not robustness_rows:
        return ["- No backtest result files found yet."]
    robust = [row for row in robustness_rows if row["robustness_verdict"] == "pass"]
    borderline = [row for row in robustness_rows if row["robustness_verdict"] == "borderline"]
    stable_groups = [row for row in stability_rows if row["stability_verdict"] == "stable"]
    best = robustness_rows[0]
    lines = [
        f"- result files reviewed: {len(robustness_rows)}",
        f"- walk-forward proxy pass: {len(robust)}",
        f"- borderline: {len(borderline)}",
        f"- stable strategy groups: {len(stable_groups)}",
        (
            "- best robustness candidate: "
            f"{best['strategy_id']} score={best['walk_forward_score']:.2f} "
            f"unseen={best['unseen_cagr']:.2%} verdict={best['robustness_verdict']}"
        ),
    ]
    if stability_rows:
        group = stability_rows[0]
        lines.append(
            "- best stability group: "
            f"{group['family']}/{group['short_name']} runs={group['run_count']} "
            f"positive_share={group['positive_unseen_share']:.0%} verdict={group['stability_verdict']}"
        )
    return lines


def _robustness_row(item: dict[str, Any]) -> dict[str, Any]:
    split_metrics = item.get("split_metrics", {})
    windows = [name for name in ("train", "validation", "unseen") if name in split_metrics]
    positive_windows = 0
    window_cagrs = []
    window_dds = []
    for name in windows:
        cagr = _metric(item, name, "cagr")
        max_dd = _metric(item, name, "max_drawdown")
        window_cagrs.append(cagr)
        window_dds.append(max_dd)
        if cagr > 0 and max_dd >= -0.15:
            positive_windows += 1
    window_count = max(len(windows), 1)
    cost_survives = bool(item.get("cost_stress", {}).get("survives_double_cost"))
    walk_forward_score = positive_windows / window_count
    unseen_cagr = _metric(item, "unseen", "cagr")
    unseen_dd = _metric(item, "unseen", "max_drawdown")
    return {
        "strategy_id": item.get("strategy_id", ""),
        "family": item.get("family", ""),
        "short_name": item.get("short_name", ""),
        "tier": item.get("tier", ""),
        "data_source": item.get("data_manifest", {}).get("source", ""),
        "data_years": item.get("data_manifest", {}).get("years", 0.0),
        "positive_windows": positive_windows,
        "window_count": window_count,
        "walk_forward_score": walk_forward_score,
        "worst_window_cagr": min(window_cagrs) if window_cagrs else 0.0,
        "worst_window_drawdown": min(window_dds) if window_dds else 0.0,
        "unseen_cagr": unseen_cagr,
        "unseen_max_drawdown": unseen_dd,
        "cost_survives": cost_survives,
        "robustness_verdict": _robustness_verdict(walk_forward_score, unseen_cagr, unseen_dd, cost_survives),
    }


def _robustness_verdict(walk_forward_score: float, unseen_cagr: float, unseen_dd: float, cost_survives: bool) -> str:
    if not cost_survives:
        return "fail"
    if walk_forward_score >= 1.0 and unseen_cagr > 0 and unseen_dd >= -0.15 and cost_survives:
        return "pass"
    if walk_forward_score >= 2 / 3 and unseen_cagr > 0 and unseen_dd >= -0.20:
        return "borderline"
    return "fail"


def _stability_verdict(run_count: int, positive_share: float, cost_share: float, rejected: int) -> str:
    if run_count >= 3 and positive_share >= 0.75 and cost_share >= 0.75 and rejected == 0:
        return "stable"
    if run_count >= 2 and positive_share >= 0.50:
        return "mixed"
    return "weak"


def _metric(item: dict[str, Any], split: str, key: str) -> float:
    value = item.get("split_metrics", {}).get(split, {}).get(key, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _share(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(1 for item in items if item) / len(items)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
