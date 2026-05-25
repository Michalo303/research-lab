from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.backtest import close_frame, cost_stress, weighted_backtest
from research_lab.data.eodhd import get_eod_history
from research_lab.data_quality_eodhd import (
    EODHD_VALIDATION_UNIVERSE,
    TARGET_VALIDATION_STRATEGIES,
    audit_history_rows,
    write_quality_outputs,
    write_vendor_reports,
)
from research_lab.strategies.baselines import StrategySpec, build_weights


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only EODHD historical validation audit.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--symbols", default=",".join(EODHD_VALIDATION_UNIVERSE))
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--raw", action="store_true", help="Use raw close instead of adjusted close.")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    root = Path(args.root)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    histories = {}
    audits = []
    for symbol in symbols:
        result = get_eod_history(
            symbol,
            start_date=args.start_date,
            end_date=args.end_date,
            adjusted=not args.raw,
            max_retries=args.max_retries,
        )
        rows = result.get("rows", [])
        histories[symbol] = rows
        audit = audit_history_rows(symbol, rows, adjusted=not args.raw)
        if result.get("coverage_status") != "available" and not rows:
            audit["coverage_status"] = result.get("coverage_status", "missing")
        audits.append(audit)
        if rows:
            _write_history_sample(root, symbol, rows)

    output_paths = write_quality_outputs(root, audits)
    report_paths = write_vendor_reports(root, audits)
    validation_rows = _run_strategy_validation(histories)
    validation_path = _write_validation_results(root, validation_rows)
    print(json.dumps({**output_paths, **report_paths, "strategy_validation_path": str(validation_path)}, indent=2))


def _write_history_sample(root: Path, symbol: str, rows: list[dict]) -> None:
    path = root / "data" / "processed" / "eodhd" / f"{symbol.replace('.', '_')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "raw_close", "adjusted_close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


def _run_strategy_validation(histories: dict[str, list[dict]]) -> list[dict]:
    required = ["SPY.US", "QQQ.US", "TLT.US", "GLD.US"]
    if any(not histories.get(symbol) for symbol in required):
        return [
            {
                "strategy_id": strategy,
                "coverage_status": "missing",
                "reason": "Required EODHD histories for SPY.US, QQQ.US, TLT.US, and GLD.US are not all available.",
                "research_only": True,
                "not_trading_signal": True,
            }
            for strategy in TARGET_VALIDATION_STRATEGIES
        ]
    panel = _panel_from_histories({symbol: histories[symbol] for symbol in required})
    specs = _validation_specs()
    rows = []
    for spec in specs:
        weights = build_weights(spec, panel)
        close = close_frame(panel)
        backtest = weighted_backtest(close, weights, cost_bps=2.0, periods_per_year=252)
        stress = cost_stress(close, weights, cost_bps=2.0, periods_per_year=252)
        rows.append(
            {
                "strategy_id": f"{spec.family}_{spec.asset_class}_{spec.timeframe}_{spec.short_name}",
                "coverage_status": "available",
                "start": str(panel.index.min().date()),
                "end": str(panel.index.max().date()),
                "years": round((panel.index.max() - panel.index.min()).days / 365.25, 2),
                "cagr": backtest["metrics"]["cagr"],
                "sharpe": backtest["metrics"]["sharpe"],
                "max_drawdown": backtest["metrics"]["max_drawdown"],
                "unseen_cagr": backtest["split_metrics"]["unseen"]["cagr"],
                "unseen_sharpe": backtest["split_metrics"]["unseen"]["sharpe"],
                "survives_double_cost": stress["survives_double_cost"],
                "research_only": True,
                "not_trading_signal": True,
            }
        )
    return rows


def _panel_from_histories(histories: dict[str, list[dict]]) -> pd.DataFrame:
    frames = {}
    for eodhd_symbol, rows in histories.items():
        symbol = eodhd_symbol.split(".")[0]
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date").sort_index()
        frames[symbol] = frame[["open", "high", "low", "close", "volume"]].astype(float)
    return pd.concat(frames, axis=1).dropna().sort_index()


def _validation_specs() -> list[StrategySpec]:
    return [
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_MOM_DD",
            hypothesis="Longer-history EODHD validation scaffold for queued momentum drawdown filter.",
            parameters={"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2, "risk_symbol": "SPY", "risk_sma": 200},
            rules="Monthly top-2 momentum rotation with SPY SMA200 risk filter.",
            builder="rotation_momentum_drawdown_filter",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DUAL_MOMENTUM",
            hypothesis="Longer-history EODHD validation scaffold for ETF dual momentum.",
            parameters={"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
            rules="At month end rank by 126-day momentum and hold top two assets.",
            builder="active_momentum_rotation",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_VOL_TARGET",
            hypothesis="Longer-history EODHD validation scaffold for SPY volatility target.",
            parameters={"symbol": "SPY", "sma": 150, "vol_window": 63, "target_vol": 0.12},
            rules="Hold SPY above SMA150 with volatility-scaled exposure.",
            builder="long_term_vol_target",
        ),
    ]


def _write_validation_results(root: Path, rows: list[dict]) -> Path:
    path = root / "reports" / "vendor_reviews" / "eodhd_strategy_validation.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


if __name__ == "__main__":
    main()
