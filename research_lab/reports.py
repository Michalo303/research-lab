from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def write_strategy_card(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_daily_report(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    accepted = [r for r in results if r["tier"] in {"A", "B"}]
    rejected = [r for r in results if r["tier"] == "Rejected"]
    best = max(results, key=lambda r: r["split_metrics"]["unseen"]["mar"]) if results else None
    rows = [
        "| strategy_id | family | asset | timeframe | train | validation | unseen | max_dd | tier |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        rows.append(
            "| {strategy_id} | {family} | {asset_class} | {timeframe} | {train:.2%} | {validation:.2%} | {unseen:.2%} | {max_dd:.2%} | {tier} |".format(
                strategy_id=r["strategy_id"],
                family=r["family"],
                asset_class=r["asset_class"],
                timeframe=r["timeframe"],
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
        "- biggest risk discovered: synthetic data cannot validate capital allocation; real data ingestion and walk-forward stability remain required.",
        "",
        "## New Strategies Tested",
        "",
        *rows,
        "",
        "## Important Findings",
        "",
        "- The deterministic runner, registry, leaderboard, and strategy-card pipeline are now operational.",
        "- Synthetic data can validate the runner, but it cannot validate capital allocation.",
        "- Negative unseen results, excessive drawdown, failed cost stress, or too few trades are rejected even during smoke tests.",
        "",
        "## Rejections",
        "",
        *(f"- {r['strategy_id']}: {r['tier_reason']}" for r in rejected),
        "",
        "## Leaderboard Changes",
        "",
        "- Leaderboard and allocation model were regenerated from the current run.",
        "",
        "## Next Actions",
        "",
        "- Enable real EOD data ingestion on Hetzner if network/dependencies allow it.",
        "- Add walk-forward and parameter-neighborhood stability for the weekly deep run.",
        "- Add data integrity checks before any strategy can rise above paper-only research.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
