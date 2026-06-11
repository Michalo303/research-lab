from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import re
from time import perf_counter

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.config import LabConfig, ensure_project_structure
from research_lab.data import DataBundle, load_daily_universe, load_eodhd_daily_universe, load_intraday_symbol, load_massive_daily_universe
from research_lab.drawdown_diagnostics import compute_drawdown_diagnostics
from research_lab.registry import append_jsonl, write_allocation_model, write_leaderboard
from research_lab.reports import write_daily_report_artifacts, write_strategy_card
from research_lab.strategies.baselines import (
    build_weights,
    baseline_strategies,
    dedupe_strategy_specs,
    next_run_guided_strategies,
    queued_daily_symbols,
    queued_hypothesis_strategies,
)
from research_lab.tiering import classify_strategy
from research_lab.walk_forward import run_true_walk_forward


def run_daily_research(root: Path | None = None) -> list[dict]:
    run_start = perf_counter()
    config = LabConfig.from_env(root)
    _log_daily_progress(f"start provider={config.data_provider} root={config.root}")
    if config.mode != "research_only":
        raise RuntimeError("Refusing to run unless RESEARCH_LAB_MODE=research_only.")
    ensure_project_structure(config.root)

    with _timed_daily_stage("loading daily universe", "daily universe"):
        daily_bundle = _load_daily_data_bundle(config)
    _print_daily_data_diagnostics(daily_bundle)
    with _timed_daily_stage("loading intraday BTCUSDT", "intraday BTCUSDT"):
        intraday_bundle = load_intraday_symbol(config.root, "BTCUSDT")

    results = []
    start_sequence = _next_sequence(config.root)
    specs = dedupe_strategy_specs(
        baseline_strategies()
        + next_run_guided_strategies(config.root, limit=2)
        + queued_hypothesis_strategies(config.root, limit=4)
    )
    for offset, spec in enumerate(specs):
        experiment_start = perf_counter()
        sequence = start_sequence + offset
        strategy_id = spec.strategy_id(sequence)
        _log_daily_progress(f"running experiment {offset + 1}/{len(specs)} {strategy_id}")
        data_bundle = intraday_bundle if spec.family == "INTRADAY" else daily_bundle
        panel = data_bundle.data
        missing_symbols = _missing_symbols(spec, panel)
        if missing_symbols:
            _log_daily_progress(f"experiment skipped strategy={strategy_id} reason=missing_symbols elapsed={perf_counter() - experiment_start:.2f}s")
            append_jsonl(
                config.root / "registry" / "data_gaps.jsonl",
                {
                    "strategy_id": strategy_id,
                    "family": spec.family,
                    "short_name": spec.short_name,
                    "missing_symbols": missing_symbols,
                    "reason": "Required symbol is not present in the loaded data universe.",
                    "research_only": True,
                },
            )
            continue
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
        walk_forward = run_true_walk_forward(
            spec,
            daily_bundle.data,
            intraday_bundle.data if spec.family == "INTRADAY" else None,
            close,
            cost_bps,
            periods_per_year,
            progress_log=_log_daily_progress,
        )
        tier_args = [
            spec.family,
            backtest["split_metrics"],
            stress,
            data_bundle.manifest["source"],
            float(data_bundle.manifest.get("years", 0.0)),
        ]
        if "walk_forward" in classify_strategy.__code__.co_varnames[: classify_strategy.__code__.co_argcount]:
            tier_args.append(walk_forward)
        tier, tier_reason = classify_strategy(*tier_args)
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
            "data_source": data_bundle.manifest["source"],
            "data_start": data_bundle.manifest["start"],
            "data_end": data_bundle.manifest["end"],
            "history_length": float(data_bundle.manifest.get("years", 0.0)),
            "cost_model": {
                "type": "turnover_bps",
                "cost_bps": cost_bps,
                "double_cost_bps": cost_bps * 2.0,
                "turnover_source": "target_weight_diff_abs_sum",
            },
            "universe": list(data_bundle.manifest.get("symbols", [])),
            "cost_stress": stress,
            "metrics": backtest["metrics"],
            "split_metrics": backtest["split_metrics"],
            "drawdown_diagnostics": compute_drawdown_diagnostics(backtest["equity"], cagr=backtest["metrics"].get("cagr")),
            "walk_forward": walk_forward,
            "average_turnover": backtest["average_turnover"],
            "average_exposure": backtest["average_exposure"],
            "return_series": _series_records(backtest["returns"]),
            "equity_curve": _series_records(backtest["equity"]),
            "target_weight_series": _weight_records(weights),
            "latest_signal": _latest_signal(weights),
            "tier": tier,
            "tier_reason": tier_reason,
            "research_only": True,
        }
        with _timed_daily_stage("writing strategy cards and registry", "strategy cards and registry"):
            _persist_result(config.root, result)
            _persist_hypothesis_result(config.root, result)
        results.append(result)
        _log_daily_progress(
            f"experiment done strategy={strategy_id} tier={tier} elapsed={perf_counter() - experiment_start:.2f}s"
        )

    leaderboard_rows = [_leaderboard_row(r) for r in results]
    with _timed_daily_stage("writing registry summaries", "registry summaries"):
        write_leaderboard(config.root / "registry" / "leaderboard.csv", leaderboard_rows)
        write_allocation_model(config.root / "registry" / "allocation_model.csv", leaderboard_rows)
    report_start = perf_counter()
    _log_daily_progress("writing daily report start")
    report_artifacts = write_daily_report_artifacts(config.root, results)
    report_path = report_artifacts.get("latest_report_path", config.root / "reports" / "daily")
    _log_daily_progress(f"daily report written in {perf_counter() - report_start:.2f}s path={report_path}")
    _log_daily_progress(f"completed in {perf_counter() - run_start:.2f}s")
    return results


def _log_daily_progress(message: str) -> None:
    print(f"[daily] {message}", flush=True)


@contextmanager
def _timed_daily_stage(start_label: str, done_label: str):
    start = perf_counter()
    _log_daily_progress(f"{start_label} start")
    try:
        yield
    except Exception:
        _log_daily_progress(f"{done_label} failed in {perf_counter() - start:.2f}s")
        raise
    else:
        _log_daily_progress(f"{done_label} done in {perf_counter() - start:.2f}s")


def _unique(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _missing_symbols(spec, panel) -> list[str]:
    if spec.family == "INTRADAY":
        return []
    if not hasattr(panel, "columns"):
        return []
    available = set(panel.columns.get_level_values(0)) if getattr(panel.columns, "nlevels", 1) > 1 else set(panel.columns)
    required = []
    if "symbol" in spec.parameters:
        required.append(str(spec.parameters["symbol"]).upper())
    if "symbols" in spec.parameters:
        required.extend(str(symbol).upper() for symbol in spec.parameters["symbols"])
    return [symbol for symbol in required if symbol not in available]


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
            "data_source": result["data_manifest"]["source"],
            "data_years": result["data_manifest"].get("years", 0.0),
        },
    )
    write_strategy_card(root / "reports" / "strategy_cards" / f"{result['strategy_id']}.md", result)


def _persist_hypothesis_result(root: Path, result: dict) -> None:
    hypothesis_id = result["parameters"].get("source_hypothesis_id")
    if not hypothesis_id:
        return
    append_jsonl(
        root / "registry" / "hypothesis_results.jsonl",
        {
            "hypothesis_id": hypothesis_id,
            "strategy_id": result["strategy_id"],
            "tier": result["tier"],
            "tier_reason": result["tier_reason"],
            "family": result["family"],
            "unseen_cagr": result["split_metrics"]["unseen"]["cagr"],
            "unseen_max_drawdown": result["split_metrics"]["unseen"]["max_drawdown"],
        },
    )


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


def _series_records(series) -> list[dict]:
    return [{"date": _format_ts(ts), "value": float(value)} for ts, value in series.dropna().items()]


def _weight_records(weights) -> list[dict]:
    records = []
    for ts, row in weights.fillna(0.0).iterrows():
        item = {"date": _format_ts(ts)}
        for symbol, value in row.items():
            item[str(symbol)] = float(value)
        records.append(item)
    return records


def _latest_signal(weights) -> dict:
    if weights.empty:
        return {"as_of": "", "target_weights": {}, "previous_weights": {}, "actions": []}
    clean = weights.fillna(0.0)
    latest = clean.iloc[-1]
    previous = clean.iloc[-2] if len(clean) > 1 else latest * 0.0
    actions = []
    for symbol in clean.columns:
        before = float(previous.get(symbol, 0.0))
        after = float(latest.get(symbol, 0.0))
        delta = after - before
        if abs(delta) < 1e-9:
            action = "hold" if after > 0 else "flat"
        elif delta > 0:
            action = "buy"
        else:
            action = "sell"
        actions.append({"symbol": str(symbol), "action": action, "from_weight": before, "to_weight": after, "delta": delta})
    return {
        "as_of": _format_ts(clean.index[-1]),
        "target_weights": {str(symbol): float(value) for symbol, value in latest.items()},
        "previous_weights": {str(symbol): float(value) for symbol, value in previous.items()},
        "actions": actions,
    }


def _format_ts(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


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


def _load_daily_data_bundle(config: LabConfig, symbols: list[str] | None = None) -> DataBundle:
    eod_symbols = _unique(symbols or ["SPY", "QQQ", "TLT", "GLD"] + queued_daily_symbols(config.root, limit=8))
    fallback_reason = ""
    if config.eodhd_api_key:
        try:
            bundle = load_eodhd_daily_universe(config.root, eod_symbols, config.eodhd_api_key, config.eodhd_start_date)
            _print_daily_selection_trace(config, bundle, "")
            return bundle
        except Exception as exc:
            fallback_reason = f"EODHD failed: {exc}"
            if config.data_provider == "eodhd":
                raise RuntimeError("EODHD daily data failed and no fallback provider was configured") from exc

    if config.data_provider == "eodhd":
        raise ValueError("EODHD_API_KEY is required when RESEARCH_LAB_DATA_PROVIDER=eodhd")

    if config.data_provider == "massive":
        bundle = load_massive_daily_universe(
            config.root,
            eod_symbols,
            config.massive_api_key,
            config.massive_base_url,
            config.massive_start_date,
            config.massive_adjusted,
        )
        if fallback_reason:
            _mark_fallback(bundle, fallback_reason)
        _warn_if_eodhd_not_selected(config, bundle, fallback_reason)
        _print_daily_selection_trace(config, bundle, fallback_reason)
        return bundle

    synthetic_symbols = eod_symbols if symbols is not None else ["SPY", "QQQ", "TLT", "GLD", "BTC-USD"]
    bundle = load_daily_universe(config.root, synthetic_symbols, config.use_yfinance)
    if fallback_reason:
        _mark_fallback(bundle, fallback_reason)
    _warn_if_eodhd_not_selected(config, bundle, fallback_reason)
    _print_daily_selection_trace(config, bundle, fallback_reason)
    return bundle


def _mark_fallback(bundle: DataBundle, reason: str) -> None:
    bundle.manifest["fallback_used"] = True
    bundle.manifest["fallback_reason"] = reason
    for row in bundle.manifest.get("symbol_diagnostics", []):
        row["fallback_used"] = True
        row["fallback_reason"] = reason


def _warn_if_eodhd_not_selected(config: LabConfig, bundle: DataBundle, reason: str) -> None:
    if config.eodhd_api_key and bundle.manifest.get("source") != "eodhd":
        suffix = f"; reason={reason}" if reason else ""
        print(
            "WARNING: EODHD credentials exist but EODHD was not selected; "
            f"selected_provider={bundle.manifest.get('source')}{suffix}"
        )


def _print_daily_selection_trace(config: LabConfig, bundle: DataBundle, reason: str) -> None:
    parts = [
        "daily_data_selection",
        f"requested_provider={config.data_provider}",
        f"selected_provider={bundle.manifest.get('source')}",
        f"eodhd_credentials_present={bool(config.eodhd_api_key)}",
        f"massive_credentials_present={bool(config.massive_api_key)}",
    ]
    if reason:
        parts.append(f"fallback_reason={reason}")
    print(" | ".join(parts))


def _print_daily_data_diagnostics(bundle: DataBundle) -> None:
    for row in bundle.manifest.get("symbol_diagnostics", []):
        print(
            "daily_data_symbol"
            f" | requested_symbol={row.get('requested_symbol', '')}"
            f" | selected_provider={row.get('selected_provider', bundle.manifest.get('source', ''))}"
            f" | fallback_used={row.get('fallback_used', False)}"
            f" | first_date={row.get('first_date', '')}"
            f" | last_date={row.get('last_date', '')}"
            f" | daily_bars={row.get('daily_bars', 0)}"
            f" | history_years={float(row.get('history_years', 0.0)):.2f}"
        )
