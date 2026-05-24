from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from research_lab.edge import classify_edge
from research_lab.robustness import load_backtest_results


PORTFOLIO_COLUMNS = [
    "strategy_id",
    "family",
    "short_name",
    "tier",
    "data_source",
    "unseen_cagr",
    "unseen_max_drawdown",
    "cost_survives",
    "edge_bucket",
    "edge_strength",
    "correlation_penalty",
    "portfolio_score",
    "suggested_weight_pct",
    "portfolio_role",
    "reason",
]

EDGE_SCORE = {
    "plausible": 1.0,
    "plausible_filter": 0.8,
    "risk_control": 0.6,
    "weak_until_tested": 0.35,
    "data_limited": 0.2,
    "missing": 0.0,
}


def run_portfolio_scoring(root: Path, report_stem: str, max_candidates: int = 12) -> dict[str, Any]:
    results = _candidate_results(load_backtest_results(root), max_candidates=max_candidates)
    rows = _portfolio_rows(results)
    _assign_weights(rows)
    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report_stem}_portfolio_candidates.csv"
    _write_csv(path, rows)
    return {"rows": rows, "path": path}


def summarize_portfolio_scoring(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- portfolio scoring: no eligible candidates"]
    nonzero = [row for row in rows if row["suggested_weight_pct"] > 0]
    top = rows[0]
    total_weight = sum(row["suggested_weight_pct"] for row in rows)
    family_weights: dict[str, float] = {}
    for row in rows:
        family_weights[row["family"]] = family_weights.get(row["family"], 0.0) + row["suggested_weight_pct"]
    family_text = ", ".join(f"{family}={weight:.1f}%" for family, weight in sorted(family_weights.items()))
    return [
        f"- portfolio candidates scored: {len(rows)}",
        f"- candidates with nonzero model weight: {len(nonzero)}",
        f"- model weight total: {total_weight:.1f}%",
        f"- family weights: {family_text}",
        (
            "- top portfolio candidate: "
            f"{top['strategy_id']} score={top['portfolio_score']:.2f} "
            f"weight={top['suggested_weight_pct']:.1f}%"
        ),
    ]


def _candidate_results(results: list[dict[str, Any]], max_candidates: int) -> list[dict[str, Any]]:
    candidates = []
    for item in results:
        if item.get("tier") == "Rejected":
            continue
        if item.get("data_manifest", {}).get("source") not in {"massive", "yfinance"}:
            continue
        if item.get("family") == "INTRADAY":
            continue
        unseen = item.get("split_metrics", {}).get("unseen", {})
        if float(unseen.get("cagr", 0.0) or 0.0) <= 0:
            continue
        candidates.append(item)
    return sorted(candidates, key=_raw_candidate_sort_key, reverse=True)[:max_candidates]


def _portfolio_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    family_counts: dict[str, int] = {}
    short_counts: dict[tuple[str, str], int] = {}
    for item in results:
        family = str(item.get("family", ""))
        short_name = str(item.get("short_name", ""))
        family_counts[family] = family_counts.get(family, 0) + 1
        short_counts[(family, short_name)] = short_counts.get((family, short_name), 0) + 1

    for item in results:
        edge = classify_edge(item)
        unseen = item.get("split_metrics", {}).get("unseen", {})
        cagr = float(unseen.get("cagr", 0.0) or 0.0)
        max_dd = float(unseen.get("max_drawdown", 0.0) or 0.0)
        cost_survives = bool(item.get("cost_stress", {}).get("survives_double_cost"))
        family = str(item.get("family", ""))
        short_name = str(item.get("short_name", ""))
        correlation_penalty = _correlation_penalty(family_counts.get(family, 1), short_counts.get((family, short_name), 1))
        score = _portfolio_score(cagr, max_dd, cost_survives, edge["edge_strength"], correlation_penalty)
        rows.append(
            {
                "strategy_id": item.get("strategy_id", ""),
                "family": family,
                "short_name": short_name,
                "tier": item.get("tier", ""),
                "data_source": item.get("data_manifest", {}).get("source", ""),
                "unseen_cagr": cagr,
                "unseen_max_drawdown": max_dd,
                "cost_survives": cost_survives,
                "edge_bucket": edge["edge_bucket"],
                "edge_strength": edge["edge_strength"],
                "correlation_penalty": correlation_penalty,
                "portfolio_score": score,
                "suggested_weight_pct": 0.0,
                "portfolio_role": _portfolio_role(family, edge["edge_bucket"]),
                "reason": "Model-only paper research allocation; not an execution permission.",
            }
        )
    return sorted(rows, key=lambda row: row["portfolio_score"], reverse=True)


def _assign_weights(rows: list[dict[str, Any]]) -> None:
    positive = [row for row in rows if row["portfolio_score"] > 0]
    if not positive:
        return
    total = sum(row["portfolio_score"] for row in positive)
    family_caps = {"LONGTERM": 35.0, "ROTATION": 45.0, "SWING": 20.0}
    family_used: dict[str, float] = {}
    for row in positive:
        raw_weight = 100.0 * row["portfolio_score"] / total
        cap = family_caps.get(row["family"], 10.0)
        used = family_used.get(row["family"], 0.0)
        allowed = max(cap - used, 0.0)
        weight = min(raw_weight, allowed, 25.0)
        row["suggested_weight_pct"] = round(weight, 2)
        family_used[row["family"]] = used + weight


def _raw_candidate_sort_key(item: dict[str, Any]) -> tuple[float, float]:
    unseen = item.get("split_metrics", {}).get("unseen", {})
    cagr = float(unseen.get("cagr", 0.0) or 0.0)
    dd = abs(float(unseen.get("max_drawdown", 0.0) or 0.0))
    mar = cagr / dd if dd > 0 else 0.0
    return mar, cagr


def _correlation_penalty(family_count: int, short_count: int) -> float:
    penalty = 0.0
    if family_count > 1:
        penalty += min(0.30, 0.08 * (family_count - 1))
    if short_count > 1:
        penalty += min(0.35, 0.10 * (short_count - 1))
    return min(penalty, 0.60)


def _portfolio_score(cagr: float, max_dd: float, cost_survives: bool, edge_strength: str, correlation_penalty: float) -> float:
    if not cost_survives or cagr <= 0:
        return 0.0
    dd = max(abs(max_dd), 0.01)
    mar_component = min(cagr / dd, 3.0)
    drawdown_component = max(0.0, 1.0 - dd / 0.20)
    edge_component = EDGE_SCORE.get(edge_strength, 0.0)
    score = (0.55 * mar_component) + (0.25 * drawdown_component) + (0.20 * edge_component)
    score *= max(0.0, 1.0 - correlation_penalty)
    return round(score, 4) if math.isfinite(score) else 0.0


def _portfolio_role(family: str, edge_bucket: str) -> str:
    if family == "LONGTERM":
        return "stability sleeve"
    if family == "ROTATION":
        return "risk-premia rotation sleeve"
    if family == "SWING" and edge_bucket == "smart_money_flow":
        return "smart-money filtered swing sleeve"
    if family == "SWING":
        return "swing alpha sleeve"
    return "research sleeve"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PORTFOLIO_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in PORTFOLIO_COLUMNS})
