from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.config import REAL_EOD_DATA_SOURCES
from research_lab.edge import classify_edge
from research_lab.metrics import performance_metrics
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

PORTFOLIO_BACKTEST_COLUMNS = [
    "strategy_count",
    "start",
    "end",
    "total_weight_pct",
    "cash_weight_pct",
    "gross_exposure_pct",
    "net_exposure_pct",
    "cagr",
    "sharpe",
    "max_drawdown",
    "mar",
    "average_pairwise_correlation",
    "rebalance_count",
    "rebalance_frequency",
    "portfolio_verdict",
    "status",
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


def run_portfolio_combination_backtest(
    root: Path,
    report_stem: str,
    candidate_rows: list[dict[str, Any]] | None = None,
    rebalance_frequency: str = "ME",
) -> dict[str, Any]:
    candidate_rows = candidate_rows if candidate_rows is not None else run_portfolio_scoring(root, report_stem)["rows"]
    selected = [row for row in candidate_rows if float(row.get("suggested_weight_pct", 0.0) or 0.0) > 0]
    selected_strategy_ids = {str(row.get("strategy_id", "")) for row in selected}
    results = load_backtest_results(root, return_series_strategy_ids=selected_strategy_ids)
    returns_by_strategy = {
        item["strategy_id"]: _return_series(item)
        for item in results
        if str(item.get("strategy_id", "")) in selected_strategy_ids
    }
    selected = [row for row in selected if not returns_by_strategy.get(str(row.get("strategy_id", ""))).empty]

    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path = report_dir / f"{report_stem}_portfolio_backtest.csv"
    equity_path = report_dir / f"{report_stem}_portfolio_equity.csv"

    if not selected:
        has_results = bool(results)
        has_real_data = any(
            item.get("data_manifest", {}).get("source") in REAL_EOD_DATA_SOURCES
            or item.get("data_source") in REAL_EOD_DATA_SOURCES
            for item in results
        )
        status = "blocked_no_real_data_candidates" if has_results and not has_real_data else "no_backtestable_return_series"
        summary = _empty_portfolio_backtest_summary(status)
        _write_csv(summary_path, [summary], PORTFOLIO_BACKTEST_COLUMNS)
        _write_equity_csv(equity_path, pd.Series(dtype=float), pd.Series(dtype=float))
        return {"summary": summary, "equity": pd.Series(dtype=float), "path": summary_path, "equity_path": equity_path}

    return_frame = pd.DataFrame({row["strategy_id"]: returns_by_strategy[row["strategy_id"]] for row in selected}).sort_index().fillna(0.0)
    target_weights = pd.Series(
        {row["strategy_id"]: float(row.get("suggested_weight_pct", 0.0) or 0.0) / 100.0 for row in selected},
        dtype=float,
    )
    total_weight = float(target_weights.sum())
    if total_weight > 1.0:
        target_weights = target_weights / total_weight
        total_weight = 1.0
    weight_frame = _rebalance_weights(return_frame.index, target_weights, rebalance_frequency)
    portfolio_returns = (weight_frame.shift(1).fillna(0.0) * return_frame).sum(axis=1)
    equity = (1.0 + portfolio_returns).cumprod()
    metrics = performance_metrics(portfolio_returns, 252)
    corr = return_frame.corr()
    summary = {
        "strategy_count": len(selected),
        "start": _format_index_value(return_frame.index[0]),
        "end": _format_index_value(return_frame.index[-1]),
        "total_weight_pct": round(total_weight * 100.0, 4),
        "cash_weight_pct": round(max(0.0, 1.0 - total_weight) * 100.0, 4),
        "gross_exposure_pct": round(total_weight * 100.0, 4),
        "net_exposure_pct": round(total_weight * 100.0, 4),
        "cagr": metrics["cagr"],
        "sharpe": metrics["sharpe"],
        "max_drawdown": metrics["max_drawdown"],
        "mar": metrics["mar"],
        "average_pairwise_correlation": _average_pairwise_correlation(corr),
        "rebalance_count": _rebalance_count(weight_frame),
        "rebalance_frequency": rebalance_frequency,
        "portfolio_verdict": _portfolio_verdict(metrics["max_drawdown"], total_weight, _average_pairwise_correlation(corr)),
        "status": "ok",
    }
    _write_csv(summary_path, [summary], PORTFOLIO_BACKTEST_COLUMNS)
    _write_equity_csv(equity_path, equity, portfolio_returns)
    return {"summary": summary, "equity": equity, "path": summary_path, "equity_path": equity_path}


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


def summarize_portfolio_backtest(summary: dict[str, Any]) -> list[str]:
    if not summary or summary.get("status") != "ok":
        return [f"- portfolio combination backtest: {summary.get('status', 'not_run') if summary else 'not_run'}"]
    return [
        (
            "- portfolio combination backtest: "
            f"{summary['strategy_count']} strategies, cash={summary['cash_weight_pct']:.1f}%, "
            f"cagr={summary['cagr']:.2%}, max_dd={summary['max_drawdown']:.2%}, "
            f"avg_corr={summary['average_pairwise_correlation']:.2f}, "
            f"rebalance_count={summary.get('rebalance_count', 0)}, "
            f"verdict={summary.get('portfolio_verdict', 'not_run')}"
        )
    ]


def _candidate_results(results: list[dict[str, Any]], max_candidates: int) -> list[dict[str, Any]]:
    candidates = []
    for item in results:
        if item.get("tier") == "Rejected":
            continue
        if item.get("data_source", item.get("data_manifest", {}).get("source")) not in REAL_EOD_DATA_SOURCES:
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


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    columns = columns or PORTFOLIO_COLUMNS
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _return_series(item: dict[str, Any]) -> pd.Series:
    records = item.get("return_series") or []
    values = {}
    for record in records:
        if not isinstance(record, dict) or "date" not in record:
            continue
        try:
            values[pd.to_datetime(record["date"])] = float(record.get("value", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return pd.Series(values, dtype=float).sort_index()


def _rebalance_weights(index: pd.Index, target_weights: pd.Series, rebalance_frequency: str) -> pd.DataFrame:
    weights = pd.DataFrame(0.0, index=index, columns=target_weights.index)
    if weights.empty:
        return weights
    marker = pd.Series(range(len(index)), index=index)
    rebal_dates = set(index[int(position)] for position in marker.resample(rebalance_frequency).last().dropna().astype(int))
    current = pd.Series(0.0, index=target_weights.index, dtype=float)
    for ts in index:
        if ts == index[0] or ts in rebal_dates:
            current = target_weights.copy()
        weights.loc[ts] = current
    return weights


def _average_pairwise_correlation(corr: pd.DataFrame) -> float:
    if corr.empty or len(corr.columns) < 2:
        return 0.0
    values = []
    for i, left in enumerate(corr.columns):
        for right in corr.columns[i + 1 :]:
            value = corr.loc[left, right]
            if pd.notna(value):
                values.append(float(value))
    return float(sum(values) / len(values)) if values else 0.0


def _empty_portfolio_backtest_summary(status: str) -> dict[str, Any]:
    return {
        "strategy_count": 0,
        "start": "",
        "end": "",
        "total_weight_pct": 0.0,
        "cash_weight_pct": 100.0,
        "gross_exposure_pct": 0.0,
        "net_exposure_pct": 0.0,
        "cagr": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "mar": 0.0,
        "average_pairwise_correlation": 0.0,
        "rebalance_count": 0,
        "rebalance_frequency": "ME",
        "portfolio_verdict": "blocked" if status.startswith("blocked") else "not_run",
        "status": status,
    }


def _rebalance_count(weights: pd.DataFrame) -> int:
    if weights.empty:
        return 0
    changed = weights.diff().abs().sum(axis=1) > 1e-12
    return int(changed.sum() + 1)


def _portfolio_verdict(max_drawdown: float, total_weight: float, average_correlation: float) -> str:
    if total_weight <= 0:
        return "blocked"
    if max_drawdown < -0.15:
        return "fail"
    if average_correlation > 0.70:
        return "fail"
    return "pass"


def _write_equity_csv(path: Path, equity: pd.Series, returns: pd.Series) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "equity", "return"])
        writer.writeheader()
        for ts, value in equity.items():
            writer.writerow({"date": _format_index_value(ts), "equity": value, "return": float(returns.loc[ts])})


def _format_index_value(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
