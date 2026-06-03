from __future__ import annotations

import csv
import itertools
import json
import statistics
from pathlib import Path
from typing import Any

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.config import LabConfig, REAL_EOD_DATA_SOURCES
from research_lab.data import load_daily_universe, load_eodhd_daily_universe, load_massive_daily_universe
from research_lab.robustness import build_stability_rows, load_backtest_results
from research_lab.strategies.baselines import StrategySpec, build_weights, queued_daily_symbols
from research_lab.tiering import classify_strategy


PARAMETER_SWEEP_COLUMNS = [
    "family",
    "short_name",
    "variant",
    "parameters_json",
    "tier",
    "train_cagr",
    "validation_cagr",
    "unseen_cagr",
    "unseen_max_drawdown",
    "wf_window_count",
    "wf_pass_rate",
    "wf_median_test_cagr",
    "wf_worst_test_drawdown",
    "wf_status",
    "cost_survives",
    "verdict",
    "final_verdict",
    "tier_reason",
]

BUILDER_BY_SHORT_NAME = {
    "TREND_FILTER": "long_term_trend_filter",
    "QUEUE_VOL_TARGET": "long_term_vol_target",
    "DUAL_MOMENTUM": "active_momentum_rotation",
    "QUEUE_MOM_DD": "rotation_momentum_drawdown_filter",
    "RSI_PULLBACK": "swing_rsi_pullback",
    "QUEUE_PULLBACK": "swing_trend_filtered_pullback",
}


def run_parameter_sweep(root: Path, report_stem: str, max_groups: int = 4, max_variants_per_group: int = 9) -> dict[str, Any]:
    config = LabConfig.from_env(root)
    results = load_backtest_results(root)
    representatives = _select_representatives(results, max_groups=max_groups)
    symbols = _daily_symbols(root, representatives)
    daily_bundle = _load_daily_bundle(config, symbols)

    rows = []
    for item in representatives:
        for variant_number, params in enumerate(_parameter_variants(item.get("short_name", ""), item.get("parameters", {}), max_variants_per_group), start=1):
            spec = _spec_from_result(item, params)
            if spec is None or _missing_symbols(spec, daily_bundle.data):
                continue
            close = close_frame(daily_bundle.data)
            weights = build_weights(spec, daily_bundle.data, None)
            backtest = weighted_backtest(close, weights, config.eod_cost_bps, 252)
            stress = cost_stress(close, weights, config.eod_cost_bps, 252)
            tier, tier_reason = classify_strategy(
                spec.family,
                backtest["split_metrics"],
                stress,
                daily_bundle.manifest["source"],
                float(daily_bundle.manifest.get("years", 0.0)),
                backtest["walk_forward"],
            )
            rows.append(_row(spec, variant_number, params, backtest["split_metrics"], stress, backtest["walk_forward"], tier, tier_reason))

    report_dir = root / "reports" / "weekly"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report_stem}_parameter_sweep.csv"
    _write_csv(path, rows)
    return {"rows": rows, "path": path}


def summarize_parameter_sweep(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- parameter sweep: no eligible real-data EOD groups found"]
    pass_rows = [row for row in rows if row["verdict"] == "pass"]
    borderline_rows = [row for row in rows if row["verdict"] == "borderline"]
    groups = _group_rows(rows)
    best_group = max(groups, key=lambda key: (_group_pass_rate(groups[key]), _median([row["unseen_cagr"] for row in groups[key]])))
    best_rows = groups[best_group]
    lines = [
        f"- parameter variants tested: {len(rows)}",
        f"- parameter pass: {len(pass_rows)}",
        f"- parameter borderline: {len(borderline_rows)}",
        (
            "- best parameter group: "
            f"{best_group[0]}/{best_group[1]} pass_rate={_group_pass_rate(best_rows):.0%} "
            f"median_unseen={_median([row['unseen_cagr'] for row in best_rows]):.2%} "
            f"median_wf_pass={_median([row.get('wf_pass_rate', 0.0) for row in best_rows]):.0%}"
        ),
    ]
    return lines


def _select_representatives(results: list[dict[str, Any]], max_groups: int) -> list[dict[str, Any]]:
    stability_rows = build_stability_rows(results)
    selected = []
    seen = set()
    for group in stability_rows:
        key = (group["family"], group["short_name"], group["data_source"])
        if group["data_source"] not in REAL_EOD_DATA_SOURCES:
            continue
        if group["family"] == "INTRADAY":
            continue
        candidates = [
            item
            for item in results
            if item.get("family") == group["family"]
            and item.get("short_name") == group["short_name"]
            and item.get("data_manifest", {}).get("source") == group["data_source"]
        ]
        if not candidates or key in seen:
            continue
        selected.append(sorted(candidates, key=lambda item: str(item.get("strategy_id", "")), reverse=True)[0])
        seen.add(key)
        if len(selected) >= max_groups:
            break
    return selected


def _daily_symbols(root: Path, representatives: list[dict[str, Any]]) -> list[str]:
    symbols = ["SPY", "QQQ", "TLT", "GLD"] + queued_daily_symbols(root, limit=8)
    for item in representatives:
        params = item.get("parameters", {})
        if "symbol" in params:
            symbols.append(str(params["symbol"]).upper())
        if "symbols" in params:
            symbols.extend(str(symbol).upper() for symbol in params["symbols"])
    result = []
    for symbol in symbols:
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _load_daily_bundle(config: LabConfig, symbols: list[str]):
    if config.eodhd_api_key or config.data_provider == "eodhd":
        return load_eodhd_daily_universe(config.root, symbols, config.eodhd_api_key, config.eodhd_start_date)
    if config.data_provider == "massive":
        return load_massive_daily_universe(
            config.root,
            symbols,
            config.massive_api_key,
            config.massive_base_url,
            config.massive_start_date,
            config.massive_adjusted,
        )
    return load_daily_universe(config.root, symbols, config.use_yfinance)


def _spec_from_result(item: dict[str, Any], params: dict[str, Any]) -> StrategySpec | None:
    short_name = str(item.get("short_name", ""))
    builder = BUILDER_BY_SHORT_NAME.get(short_name)
    if builder is None:
        return None
    return StrategySpec(
        family=str(item.get("family", "")),
        asset_class=str(item.get("asset_class", "ETF")),
        timeframe=str(item.get("timeframe", "1D")),
        short_name=short_name,
        hypothesis=str(item.get("hypothesis", "")),
        parameters=params,
        rules=str(item.get("rules", "")),
        builder=builder,
    )


def _parameter_variants(short_name: str, params: dict[str, Any], max_variants: int) -> list[dict[str, Any]]:
    if short_name == "TREND_FILTER":
        return _grid(params, {"sma": _around(params.get("sma", 200), 50, low=50)}, max_variants)
    if short_name == "QUEUE_VOL_TARGET":
        return _grid(
            params,
            {
                "sma": _around(params.get("sma", 150), 50, low=50),
                "vol_window": _around(params.get("vol_window", 63), 21, low=21),
                "target_vol": _around_float(params.get("target_vol", 0.12), 0.02, low=0.04),
            },
            max_variants,
        )
    if short_name == "DUAL_MOMENTUM":
        return _grid(
            params,
            {
                "lookback": _around(params.get("lookback", 126), 63, low=21),
                "top_n": _around(params.get("top_n", 2), 1, low=1),
            },
            max_variants,
        )
    if short_name == "QUEUE_MOM_DD":
        return _grid(
            params,
            {
                "lookback": _around(params.get("lookback", 126), 63, low=21),
                "top_n": _around(params.get("top_n", 2), 1, low=1),
                "risk_sma": _around(params.get("risk_sma", 200), 50, low=50),
            },
            max_variants,
        )
    if short_name == "RSI_PULLBACK":
        return _grid(
            params,
            {
                "trend_sma": _around(params.get("trend_sma", 100), 50, low=20),
                "rsi_entry": _around(params.get("rsi_entry", 35), 5, low=10, high=60),
                "rsi_exit": _around(params.get("rsi_exit", 55), 5, low=30, high=80),
            },
            max_variants,
        )
    if short_name == "QUEUE_PULLBACK":
        return _grid(
            params,
            {
                "fast_sma": _around(params.get("fast_sma", 50), 20, low=10),
                "slow_sma": _around(params.get("slow_sma", 150), 30, low=30),
                "rsi_entry": _around(params.get("rsi_entry", 40), 5, low=10, high=65),
                "rsi_exit": _around(params.get("rsi_exit", 58), 4, low=35, high=85),
                "atr_stop": _around_float(params.get("atr_stop", 2.0), 0.5, low=0.5),
            },
            max_variants,
        )
    return [dict(params)]


def _grid(params: dict[str, Any], options: dict[str, list[Any]], max_variants: int) -> list[dict[str, Any]]:
    keys = list(options)
    variants = []
    base = dict(params)
    variants.append(base)
    for values in itertools.product(*(options[key] for key in keys)):
        item = dict(params)
        for key, value in zip(keys, values):
            item[key] = value
        if item not in variants:
            variants.append(item)
        if len(variants) >= max_variants:
            break
    return variants[:max_variants]


def _around(value: Any, step: int, low: int, high: int | None = None) -> list[int]:
    center = int(value)
    values = [center, center - step, center + step]
    return _unique_ints(values, low, high)


def _around_float(value: Any, step: float, low: float, high: float | None = None) -> list[float]:
    center = float(value)
    values = [center, center - step, center + step]
    output = []
    for item in values:
        clipped = max(low, item)
        if high is not None:
            clipped = min(high, clipped)
        rounded = round(clipped, 4)
        if rounded not in output:
            output.append(rounded)
    return output


def _unique_ints(values: list[int], low: int, high: int | None) -> list[int]:
    output = []
    for item in values:
        clipped = max(low, item)
        if high is not None:
            clipped = min(high, clipped)
        if clipped not in output:
            output.append(clipped)
    return output


def _missing_symbols(spec: StrategySpec, panel) -> bool:
    available = set(panel.columns.get_level_values(0)) if getattr(panel.columns, "nlevels", 1) > 1 else set(panel.columns)
    required = []
    if "symbol" in spec.parameters:
        required.append(str(spec.parameters["symbol"]).upper())
    if "symbols" in spec.parameters:
        required.extend(str(symbol).upper() for symbol in spec.parameters["symbols"])
    return any(symbol not in available for symbol in required)


def _row(
    spec: StrategySpec,
    variant_number: int,
    params: dict[str, Any],
    split_metrics: dict[str, Any],
    stress: dict[str, Any],
    walk_forward: dict[str, Any],
    tier: str,
    tier_reason: str,
) -> dict[str, Any]:
    unseen = split_metrics["unseen"]
    train = split_metrics["train"]
    validation = split_metrics["validation"]
    cost_survives = bool(stress.get("survives_double_cost"))
    wf_pass_rate = float(walk_forward.get("pass_rate", 0.0) or 0.0)
    verdict = _variant_verdict(train["cagr"], validation["cagr"], unseen["cagr"], unseen["max_drawdown"], cost_survives, wf_pass_rate)
    return {
        "family": spec.family,
        "short_name": spec.short_name,
        "variant": variant_number,
        "parameters_json": json.dumps(params, sort_keys=True),
        "tier": tier,
        "train_cagr": train["cagr"],
        "validation_cagr": validation["cagr"],
        "unseen_cagr": unseen["cagr"],
        "unseen_max_drawdown": unseen["max_drawdown"],
        "wf_window_count": int(walk_forward.get("window_count", 0) or 0),
        "wf_pass_rate": wf_pass_rate,
        "wf_median_test_cagr": float(walk_forward.get("median_test_cagr", 0.0) or 0.0),
        "wf_worst_test_drawdown": float(walk_forward.get("worst_test_drawdown", 0.0) or 0.0),
        "wf_status": str(walk_forward.get("status", "")),
        "cost_survives": cost_survives,
        "verdict": verdict,
        "final_verdict": verdict,
        "tier_reason": tier_reason,
    }


def _variant_verdict(
    train_cagr: float,
    validation_cagr: float,
    unseen_cagr: float,
    unseen_dd: float,
    cost_survives: bool,
    wf_pass_rate: float = 0.0,
) -> str:
    if not cost_survives:
        return "fail"
    if wf_pass_rate < 0.50:
        return "fail"
    if train_cagr > 0 and validation_cagr > 0 and unseen_cagr > 0 and unseen_dd >= -0.15 and wf_pass_rate >= 0.67:
        return "pass"
    if validation_cagr > 0 and unseen_cagr > 0 and unseen_dd >= -0.20:
        return "borderline"
    return "fail"


def _group_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["family"], row["short_name"]), []).append(row)
    return groups


def _group_pass_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row["verdict"] == "pass") / len(rows)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PARAMETER_SWEEP_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in PARAMETER_SWEEP_COLUMNS})
