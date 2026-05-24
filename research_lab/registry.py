from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


LEADERBOARD_FIELDS = [
    "strategy_id",
    "family",
    "asset_class",
    "timeframe",
    "tier",
    "data_source",
    "unseen_cagr",
    "unseen_sharpe",
    "unseen_mar",
    "unseen_max_drawdown",
    "unseen_profit_factor",
    "unseen_trades",
    "cost_bps",
    "double_cost_unseen_cagr",
    "average_exposure",
    "average_turnover",
]


ALLOCATION_FIELDS = [
    "strategy_id",
    "family",
    "asset_class",
    "tier",
    "suggested_weight_pct",
    "max_strategy_dd",
    "portfolio_role",
    "reason",
]


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "logged_at": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(payload), sort_keys=True) + "\n")


def write_leaderboard(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(rows, key=lambda row: (tier_rank(row["tier"]), row["unseen_mar"], row["unseen_cagr"]), reverse=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEADERBOARD_FIELDS)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in LEADERBOARD_FIELDS} for row in ranked])


def write_allocation_model(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    eligible = [row for row in rows if row["tier"] in {"A", "B", "C"}]
    weights = _suggest_weights(eligible)
    output = []
    for row in eligible:
        output.append(
            {
                "strategy_id": row["strategy_id"],
                "family": row["family"],
                "asset_class": row["asset_class"],
                "tier": row["tier"],
                "suggested_weight_pct": round(weights.get(row["strategy_id"], 0.0), 2),
                "max_strategy_dd": row["unseen_max_drawdown"],
                "portfolio_role": _role(row["family"], row["tier"]),
                "reason": "Model allocation only; no real capital authorization.",
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALLOCATION_FIELDS)
        writer.writeheader()
        writer.writerows(output)


def tier_rank(tier: str) -> int:
    return {"A": 4, "B": 3, "C": 2, "Rejected": 1}.get(tier, 0)


def _suggest_weights(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    tier_budget = {"A": 60.0, "B": 30.0, "C": 10.0}
    result: dict[str, float] = {}
    for tier, budget in tier_budget.items():
        tier_rows = [row for row in rows if row["tier"] == tier]
        if not tier_rows:
            continue
        scores = {}
        for row in tier_rows:
            dd_penalty = max(abs(float(row["unseen_max_drawdown"])), 0.01)
            scores[row["strategy_id"]] = max(float(row["unseen_mar"]), 0.01) / dd_penalty
        total = sum(scores.values())
        for strategy_id, score in scores.items():
            result[strategy_id] = budget * score / total
    return result


def _role(family: str, tier: str) -> str:
    if tier == "C":
        return "paper-only experiment"
    return {
        "LONGTERM": "core stability sleeve",
        "ROTATION": "diversifying rotation sleeve",
        "SWING": "medium-frequency alpha sleeve",
        "INTRADAY": "high-friction research sleeve",
    }.get(family, "research sleeve")


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
