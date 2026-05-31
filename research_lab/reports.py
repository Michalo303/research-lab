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
    sources = sorted({r["data_manifest"]["source"] for r in results})
    source_note = _source_note(results)
    next_actions = _next_actions(results)
    rejection_diagnostics = _rejection_diagnostics_rows(rejected)
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
        "## Leaderboard Changes",
        "",
        "- Leaderboard and allocation model were regenerated from the current run.",
        "",
        "## Next Actions",
        "",
        *next_actions,
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
