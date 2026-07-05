from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import re
from time import perf_counter

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.config import LabConfig, ensure_project_structure
from research_lab.data import DataBundle, load_cached_eodhd_daily_universe, load_daily_universe, load_eodhd_daily_universe, load_intraday_symbol, load_massive_daily_universe
from research_lab.drawdown_diagnostics import compute_drawdown_diagnostics
from research_lab.jsonl import iter_jsonl
from research_lab.registry import append_jsonl, write_allocation_model, write_leaderboard
from research_lab.reports import write_daily_report_artifacts, write_strategy_card
from research_lab.strategies.baselines import (
    baseline_strategies,
    build_weights,
    dedupe_strategy_specs,
    next_run_guided_strategies,
    select_daily_experiment_candidates,
    select_queued_hypothesis_candidates,
)
from research_lab.tiering import classify_strategy
from research_lab.walk_forward import run_true_walk_forward


def run_daily_research(
    root: Path | None = None,
    *,
    recovery_mode: bool = False,
    recovery_day: int | None = None,
) -> list[dict]:
    run_start = perf_counter()
    config = LabConfig.from_env(root)
    _log_daily_progress(f"start provider={config.data_provider} root={config.root}")
    if config.mode != "research_only":
        raise RuntimeError("Refusing to run unless RESEARCH_LAB_MODE=research_only.")
    if not recovery_mode:
        ensure_project_structure(config.root)

    selection = select_daily_candidates(
        config.root,
        recovery_mode=recovery_mode,
        recovery_day=recovery_day,
    )
    _require_resolved_recovery(selection["diagnostics"])
    if recovery_mode:
        ensure_project_structure(config.root)
    specs = dedupe_strategy_specs(selection["specs"])
    selection["diagnostics"]["selected"] = len(specs)
    selection["diagnostics"]["budget_selected"] = len(specs)
    selection["diagnostics"].update(
        {"attempted": 0, "completed": 0, "missing_data_skipped": 0}
    )
    used_note_ids = _load_used_note_ids(
        config.root,
        {
            str(spec.parameters.get("source_hypothesis_id"))
            for spec in specs
            if spec.parameters.get("source_hypothesis_id")
        },
    )
    daily_bundle = None
    if specs:
        with _timed_daily_stage("loading daily universe", "daily universe"):
            daily_bundle = _load_daily_data_bundle(config, symbols=_spec_symbols(specs))
        _print_daily_data_diagnostics(daily_bundle)

    results = []
    start_sequence = _next_sequence(config.root)
    intraday_bundle = None
    for offset, spec in enumerate(specs):
        assert daily_bundle is not None
        experiment_start = perf_counter()
        sequence = start_sequence + offset
        strategy_id = spec.strategy_id(sequence)
        _log_daily_progress(f"running experiment {offset + 1}/{len(specs)} {strategy_id}")
        if spec.family == "INTRADAY":
            if intraday_bundle is None:
                with _timed_daily_stage("loading intraday BTCUSDT", "intraday BTCUSDT"):
                    intraday_bundle = load_intraday_symbol(config.root, "BTCUSDT")
            data_bundle = intraday_bundle
        else:
            data_bundle = daily_bundle
        panel = data_bundle.data
        missing_symbols = _missing_symbols(spec, panel)
        if missing_symbols:
            selection["diagnostics"]["missing_data_skipped"] += 1
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
        selection["diagnostics"]["attempted"] += 1
        weights = build_weights(spec, daily_bundle.data, intraday_bundle.data if intraday_bundle is not None else None)
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
            "builder": spec.builder,
            "hypothesis": spec.hypothesis,
            "rules": spec.rules,
            "parameters": spec.parameters,
            "used_note_ids": used_note_ids.get(str(spec.parameters.get("source_hypothesis_id") or ""), []),
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
        selection["diagnostics"]["completed"] += 1
        _log_daily_progress(
            f"experiment done strategy={strategy_id} tier={tier} elapsed={perf_counter() - experiment_start:.2f}s"
        )

    leaderboard_rows = [_leaderboard_row(r) for r in results]
    with _timed_daily_stage("writing registry summaries", "registry summaries"):
        write_leaderboard(config.root / "registry" / "leaderboard.csv", leaderboard_rows)
        write_allocation_model(config.root / "registry" / "allocation_model.csv", leaderboard_rows)
    report_start = perf_counter()
    _log_daily_progress("writing daily report start")
    report_artifacts = write_daily_report_artifacts(
        config.root,
        results,
        extra_metadata={"daily_experiment_selection": selection["diagnostics"]},
    )
    report_path = report_artifacts.get("latest_report_path", config.root / "reports" / "daily")
    _log_daily_progress(f"daily report written in {perf_counter() - report_start:.2f}s path={report_path}")
    _log_daily_progress(f"completed in {perf_counter() - run_start:.2f}s")
    return results


def select_daily_candidates(
    root: Path,
    *,
    recovery_mode: bool = False,
    recovery_day: int | None = None,
) -> dict:
    if not recovery_mode:
        return _select_normal_daily_candidates(root)
    if isinstance(recovery_day, bool) or not isinstance(recovery_day, int) or recovery_day <= 0:
        raise ValueError("recovery_day must be a positive integer when recovery_mode is enabled")
    if recovery_day > 7:
        return _select_normal_daily_candidates(root)
    selection = select_daily_experiment_candidates(root, recovery_day=recovery_day)
    diagnostics = selection["diagnostics"]
    diagnostics.setdefault("recovery_target", int(diagnostics.get("proposed", len(selection["specs"]))))
    diagnostics.setdefault("selected_new", len(selection["specs"]))
    diagnostics.setdefault("covered_by_recent_real", 0)
    diagnostics.setdefault(
        "recovery_resolved",
        int(diagnostics["selected_new"]) + int(diagnostics["covered_by_recent_real"]),
    )
    diagnostics.setdefault(
        "recovery_shortfall",
        max(int(diagnostics["recovery_target"]) - int(diagnostics["recovery_resolved"]), 0),
    )
    selection["diagnostics"].update(
        {
            "selection_mode": "bounded_recovery",
            "queue_inspected": False,
            "queue_consumed": False,
            "candidate_source": "internal_recovery_manifest",
        }
    )
    return selection


def _require_resolved_recovery(diagnostics: dict) -> None:
    if diagnostics.get("selection_mode") != "bounded_recovery":
        return
    target = int(diagnostics.get("recovery_target", 0))
    resolved = int(diagnostics.get("recovery_resolved", 0))
    if target <= 0 or resolved != target:
        shortfall = max(target - resolved, 0)
        raise RuntimeError(
            f"bounded recovery must resolve all manifest candidates before execution: "
            f"target={target} resolved={resolved} recovery_shortfall={shortfall}"
        )


def _select_normal_daily_candidates(root: Path) -> dict:
    queued = select_queued_hypothesis_candidates(root, limit=4)
    specs = dedupe_strategy_specs(
        baseline_strategies()
        + next_run_guided_strategies(root, limit=2)
        + queued["specs"]
    )
    return {
        "specs": specs,
        "diagnostics": {
            "selection_mode": "normal_daily",
            "queue_inspected": True,
            "queue_consumed": False,
            "candidate_source": "normal_baseline_guided_queue",
            "queue_rows_consumed": False,
            "proposed": len(specs),
            "selected": len(specs),
            "budget_selected": len(specs),
            "queued_candidate_dedupe": queued["diagnostics"],
        },
    }


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
            "hermes_run_id": result["parameters"].get("source_hermes_run_id", ""),
            "hermes_provider": result["parameters"].get("source_hermes_provider", ""),
            "used_note_ids": list(result.get("used_note_ids", [])),
            "strategy_id": result["strategy_id"],
            "tier": result["tier"],
            "tier_reason": result["tier_reason"],
            "family": result["family"],
            "unseen_cagr": result["split_metrics"]["unseen"]["cagr"],
            "unseen_max_drawdown": result["split_metrics"]["unseen"]["max_drawdown"],
        },
    )


def _load_used_note_ids(root: Path, hypothesis_ids: set[str]) -> dict[str, list[str]]:
    if not hypothesis_ids:
        return {}
    path = Path(root) / "registry" / "hypothesis_queue.jsonl"
    found = {}
    for item in iter_jsonl(path):
        hypothesis_id = str(item.get("hypothesis_id", ""))
        if hypothesis_id not in hypothesis_ids:
            continue
        note_ids = item.get("used_note_ids", [])
        if not isinstance(note_ids, list):
            found[hypothesis_id] = []
            continue
        found[hypothesis_id] = [
            note_id
            for note_id in note_ids[:5]
            if isinstance(note_id, str)
            and re.fullmatch(r"note-[0-9a-fA-F]{16}", note_id)
        ]
    return found


def _used_note_ids(root: Path, hypothesis_id: object) -> list[str]:
    """Compatibility wrapper for callers that request one hypothesis."""
    if not hypothesis_id:
        return []
    key = str(hypothesis_id)
    return _load_used_note_ids(root, {key}).get(key, [])


def _next_sequence(root: Path) -> int:
    registry = root / "registry" / "strategy_registry.jsonl"
    today = date.today().strftime("%Y%m%d")
    if not registry.exists():
        return 1
    max_sequence = 0
    pattern = re.compile(rf"_{today}_(\d{{3}})")
    with registry.open("r", encoding="utf-8") as handle:
        for line in handle:
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
    eod_symbols = _unique(symbols or ["SPY", "QQQ", "TLT", "GLD"])
    if config.data_provider == "eodhd_cache":
        bundle = load_cached_eodhd_daily_universe(config.root, eod_symbols)
        _print_daily_selection_trace(config, bundle, "")
        return bundle
    fallback_reason = ""
    if config.eodhd_api_key and config.data_provider in {"eodhd", "massive"}:
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


def _spec_symbols(specs) -> list[str]:
    symbols = []
    for spec in specs:
        parameters = spec.parameters
        values = [parameters.get("symbol"), parameters.get("risk_symbol")]
        for key in ("symbols", "risk_assets", "defensive_assets"):
            value = parameters.get(key)
            if isinstance(value, (list, tuple)):
                values.extend(value)
        for value in values:
            symbol = str(value or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols


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
