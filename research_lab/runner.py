from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import re

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.config import LabConfig, ensure_project_structure
from research_lab.data import load_daily_universe, load_intraday_symbol, load_massive_daily_universe
from research_lab.registry import append_jsonl, write_allocation_model, write_leaderboard
from research_lab.reports import write_daily_report, write_strategy_card
from research_lab.strategies.baselines import build_weights, baseline_strategies
from research_lab.tiering import classify_strategy


def run_daily_research(root: Path | None = None) -> list[dict]:
    config = LabConfig.from_env(root)
    if config.mode != "research_only":
        raise RuntimeError("Refusing to run unless RESEARCH_LAB_MODE=research_only.")
    ensure_project_structure(config.root)

    daily_symbols = ["SPY", "QQQ", "TLT", "GLD", "BTC-USD"]
    if config.data_provider == "massive":
        daily_bundle = load_massive_daily_universe(
            config.root,
            daily_symbols,
            config.massive_api_key,
            config.massive_base_url,
            config.massive_start_date,
            config.massive_adjusted,
        )
    else:
        daily_bundle = load_daily_universe(config.root, daily_symbols, config.use_yfinance)
    intraday_bundle = load_intraday_symbol(config.root, "BTCUSDT")

    results = []
    start_sequence = _next_sequence(config.root)
    for offset, spec in enumerate(baseline_strategies()):
        sequence = start_sequence + offset
        strategy_id = spec.strategy_id(sequence)
        data_bundle = intraday_bundle if spec.family == "INTRADAY" else daily_bundle
        panel = data_bundle.data
        weights = build_weights(spec, daily_bundle.data, intraday_bundle.data)
        close = close_frame(panel)
        if spec.family == "INTRADAY":
            periods_per_year = 252 * 26
            cost_bps = config.intraday_cost_bps
        else:
            periods_per_year = 252
            cost_bps = config.eod_cost_bps
        backtest = weighted_backtest(close, weights, cost_bps, periods_per_year)
        stress = cost_stress(close, weights, cost_bps, periods_per_year)
        tier, tier_reason = classify_strategy(
            spec.family,
            backtest["split_metrics"],
            stress,
            data_bundle.manifest["source"],
            float(data_bundle.manifest.get("years", 0.0)),
        )
        result = {
            "strategy_id": strategy_id,
            "family": spec.family,
            "asset_class": spec.asset_class,
            "timeframe": spec.timeframe,
            "short_name": spec.short_name,
            "hypothesis": spec.hypothesis,
            "rules": spec.rules,
            "parameters": spec.parameters,
            "parameter_count": len(spec.parameters),
            "variants_tried": 1,
            "data_manifest": data_bundle.manifest,
            "cost_stress": stress,
            "metrics": backtest["metrics"],
            "split_metrics": backtest["split_metrics"],
            "average_turnover": backtest["average_turnover"],
            "average_exposure": backtest["average_exposure"],
            "tier": tier,
            "tier_reason": tier_reason,
            "research_only": True,
        }
        _persist_result(config.root, result)
        results.append(result)

    leaderboard_rows = [_leaderboard_row(r) for r in results]
    write_leaderboard(config.root / "registry" / "leaderboard.csv", leaderboard_rows)
    write_allocation_model(config.root / "registry" / "allocation_model.csv", leaderboard_rows)
    write_daily_report(config.root / "reports" / "daily" / f"{date.today().isoformat()}.md", results)
    return results


def _persist_result(root: Path, result: dict) -> None:
    run_dir = root / "backtests" / "runs" / result["strategy_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(json.dumps(_json_safe(result), indent=2, default=str), encoding="utf-8")
    append_jsonl(root / "registry" / "experiments.jsonl", result)
    append_jsonl(
        root / "registry" / "strategy_registry.jsonl",
        {
            "strategy_id": result["strategy_id"],
            "family": result["family"],
            "asset_class": result["asset_class"],
            "timeframe": result["timeframe"],
            "tier": result["tier"],
            "hypothesis": result["hypothesis"],
            "tier_reason": result["tier_reason"],
        },
    )
    write_strategy_card(root / "reports" / "strategy_cards" / f"{result['strategy_id']}.md", result)


def _next_sequence(root: Path) -> int:
    registry = root / "registry" / "strategy_registry.jsonl"
    today = date.today().strftime("%Y%m%d")
    if not registry.exists():
        return 1
    max_sequence = 0
    pattern = re.compile(rf"_{today}_(\d{{3}})")
    for line in registry.read_text(encoding="utf-8").splitlines():
        match = pattern.search(line)
        if match:
            max_sequence = max(max_sequence, int(match.group(1)))
    return max_sequence + 1


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        return None
    return value


def _leaderboard_row(result: dict) -> dict:
    unseen = result["split_metrics"]["unseen"]
    return {
        "strategy_id": result["strategy_id"],
        "family": result["family"],
        "asset_class": result["asset_class"],
        "timeframe": result["timeframe"],
        "tier": result["tier"],
        "data_source": result["data_manifest"]["source"],
        "unseen_cagr": unseen["cagr"],
        "unseen_sharpe": unseen["sharpe"],
        "unseen_mar": unseen["mar"],
        "unseen_max_drawdown": unseen["max_drawdown"],
        "unseen_profit_factor": unseen["profit_factor"],
        "unseen_trades": unseen["trade_count"],
        "cost_bps": result["cost_stress"]["normal_cost_bps"],
        "double_cost_unseen_cagr": result["cost_stress"]["double_unseen_cagr"],
        "average_exposure": result["average_exposure"],
        "average_turnover": result["average_turnover"],
    }
