from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)
from research_lab.research.minervini_evaluation_gate_v1 import (
    evaluate_minervini_result_v1,
)
from research_lab.research.minervini_portfolio_evaluator_v1 import (
    run_minervini_portfolio_v1,
)
from research_lab.research.minervini_price_volume_core_v1 import (
    build_minervini_signals_v1,
)


MANIFEST_VERSION = "minervini_local_dataset_manifest_v1"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one local, read-only Minervini Core V1 evaluation."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--write-result", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest_path = _absolute_regular_file(args.manifest, "manifest")
        manifest = _load_manifest(manifest_path)
        panel, instrument_types, snapshot_sha256 = _load_panel(manifest)
        signals = build_minervini_signals_v1(
            panel, instrument_types=instrument_types
        )
        portfolio = run_minervini_portfolio_v1(
            panel=panel,
            signals=signals,
            instrument_types=instrument_types,
            evaluation_start=manifest["evaluation_start"],
            evaluation_end=manifest["evaluation_end"],
            terminal_values=manifest["terminal_values"],
        )
        blockers = _data_blockers(manifest)
        gate = evaluate_minervini_result_v1(
            portfolio, data_blockers=blockers
        )
        result = {
            **gate,
            "snapshot_sha256": snapshot_sha256,
            "portfolio_metrics": {
                key: portfolio[key]
                for key in (
                    "cagr",
                    "cumulative_return",
                    "evaluation_start",
                    "evaluation_end",
                    "maximum_drawdown",
                    "mar",
                    "trade_count",
                    "win_rate",
                    "average_exposure_fraction",
                    "turnover",
                    "transaction_costs",
                    "cost_drag_fraction",
                )
            },
        }
        result["result_sha256"] = _hash(result)
        if args.write_result is not None:
            _write_result(args.write_result, args.output_dir, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("status=FAIL")
        print(f"reason={exc}")
        return 1
    print(f"status={result['verdict']}")
    print(f"cagr={result['portfolio_metrics']['cagr']}")
    print(
        "maximum_drawdown="
        f"{result['portfolio_metrics']['maximum_drawdown']}"
    )
    print(f"trade_count={result['portfolio_metrics']['trade_count']}")
    print(f"snapshot_sha256={result['snapshot_sha256']}")
    print("provider_calls_used=0")
    print("network_used=False")
    print("broker_actions_used=0")
    return 0


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be an object.")
    allowed = {
        "version",
        "dataset_id",
        "point_in_time_classification",
        "survivorship_status",
        "evaluation_classification",
        "evaluation_start",
        "evaluation_end",
        "price_adjustment_status",
        "corporate_action_lineage_sha256",
        "corporate_action_lineage_path",
        "universe_lineage_sha256",
        "universe_lineage_path",
        "market_proxy_symbol",
        "terminal_values",
        "instruments",
    }
    _unknown(payload, allowed, "manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise ValueError(f"manifest version must be {MANIFEST_VERSION}.")
    for field in (
        "dataset_id",
        "point_in_time_classification",
        "survivorship_status",
        "evaluation_classification",
        "evaluation_start",
        "evaluation_end",
        "price_adjustment_status",
        "corporate_action_lineage_sha256",
        "corporate_action_lineage_path",
        "universe_lineage_sha256",
        "universe_lineage_path",
        "market_proxy_symbol",
    ):
        _text(payload.get(field), field)
    if payload["evaluation_classification"] != "OUT_OF_SAMPLE_FROZEN":
        raise ValueError(
            "evaluation_classification must be OUT_OF_SAMPLE_FROZEN."
        )
    if payload["price_adjustment_status"] != "SPLIT_ADJUSTED":
        raise ValueError("price_adjustment_status must be SPLIT_ADJUSTED.")
    _sha256(
        payload["corporate_action_lineage_sha256"],
        "corporate_action_lineage_sha256",
    )
    _sha256(payload["universe_lineage_sha256"], "universe_lineage_sha256")
    _verify_local_hash(
        payload["corporate_action_lineage_path"],
        payload["corporate_action_lineage_sha256"],
        "corporate_action_lineage_path",
    )
    _verify_local_hash(
        payload["universe_lineage_path"],
        payload["universe_lineage_sha256"],
        "universe_lineage_path",
    )
    start = _naive_timestamp(payload["evaluation_start"], "evaluation_start")
    end = _naive_timestamp(payload["evaluation_end"], "evaluation_end")
    if start > end:
        raise ValueError("evaluation_start must not follow evaluation_end.")
    if payload["market_proxy_symbol"] != "SPY":
        raise ValueError("market_proxy_symbol must be SPY.")
    instruments = payload.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("manifest instruments must be a non-empty list.")
    normalized = [_instrument(item) for item in instruments]
    symbols = [item["symbol"] for item in normalized]
    if len(symbols) != len(set(symbols)):
        raise ValueError("manifest symbols must be unique.")
    proxies = [
        item for item in normalized if item["role"] == "MARKET_PROXY"
    ]
    investable = [
        item for item in normalized if item["role"] == "INVESTABLE"
    ]
    if (
        len(proxies) != 1
        or proxies[0]["symbol"] != "SPY"
        or proxies[0]["instrument_type"].casefold() != "etf"
    ):
        raise ValueError("manifest requires exactly one SPY ETF market proxy.")
    if not investable or any(
        item["instrument_type"].casefold() != "common stock"
        for item in investable
    ):
        raise ValueError(
            "manifest requires at least one investable common stock."
        )
    terminal_values = _terminal_values(
        payload.get("terminal_values", {}), set(symbols)
    )
    return {
        **payload,
        "instruments": sorted(normalized, key=lambda x: x["symbol"]),
        "terminal_values": terminal_values,
    }


def _instrument(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("manifest instrument must be an object.")
    allowed = {
        "symbol",
        "role",
        "instrument_type",
        "exchange",
        "file_path",
        "format",
        "dataset_id",
        "expected_sha256",
    }
    _unknown(raw, allowed, "manifest instrument")
    result = {field: _text(raw.get(field), field) for field in allowed}
    result["symbol"] = result["symbol"].upper()
    if result["role"] not in {"INVESTABLE", "MARKET_PROXY"}:
        raise ValueError("instrument role must be INVESTABLE or MARKET_PROXY.")
    if result["format"] not in {"json", "jsonl"}:
        raise ValueError("instrument format must be json or jsonl.")
    _sha256(result["expected_sha256"], "expected_sha256")
    _absolute_regular_file(result["file_path"], "instrument file")
    return result


def _load_panel(
    manifest: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, str], str]:
    frames: dict[str, pd.DataFrame] = {}
    instrument_types: dict[str, str] = {}
    identities: list[dict[str, object]] = []
    for item in manifest["instruments"]:
        path = _absolute_regular_file(item["file_path"], "instrument file")
        adapter = build_local_ohlcv_file_input_adapter(
            {
                "version": "local_ohlcv_file_input_adapter_request_v1",
                "file_path": str(path),
                "format": item["format"],
                "dataset_id": item["dataset_id"],
                "symbol": item["symbol"],
                "exchange": item["exchange"],
                "timezone": None,
                "expected_sha256": item["expected_sha256"],
                "max_bytes": 100_000_000,
                "max_rows": 100_000,
                "provenance": {
                    "source": "minervini_local_dataset_manifest_v1"
                },
            }
        )
        if adapter["status"] != "SUCCESS":
            raise ValueError("local OHLCV adapter did not succeed.")
        frame = pd.DataFrame(adapter["normalized_bars"])
        frame["timestamp"] = pd.to_datetime(
            frame["timestamp"], utc=True
        ).dt.tz_convert(None)
        normalized_frame = frame.set_index("timestamp")[
            ["open", "high", "low", "close", "volume"]
        ]
        if item["role"] == "INVESTABLE":
            frames[item["symbol"]] = normalized_frame
            instrument_types[item["symbol"]] = item["instrument_type"]
        identities.append(
            {
                "symbol": item["symbol"],
                "source_sha256": adapter["source_sha256"],
                "normalized_rows_hash": adapter["normalized_rows_hash"],
                "row_count": adapter["row_count"],
            }
        )
    panel = pd.concat(frames, axis=1).sort_index()
    snapshot_sha256 = _hash(
        {
            "version": MANIFEST_VERSION,
            "dataset_id": manifest["dataset_id"],
            "point_in_time_classification": manifest[
                "point_in_time_classification"
            ],
            "survivorship_status": manifest["survivorship_status"],
            "evaluation_classification": manifest[
                "evaluation_classification"
            ],
            "evaluation_start": manifest["evaluation_start"],
            "evaluation_end": manifest["evaluation_end"],
            "price_adjustment_status": manifest[
                "price_adjustment_status"
            ],
            "corporate_action_lineage_sha256": manifest[
                "corporate_action_lineage_sha256"
            ],
            "universe_lineage_sha256": manifest[
                "universe_lineage_sha256"
            ],
            "market_proxy_symbol": manifest["market_proxy_symbol"],
            "terminal_values": manifest["terminal_values"],
            "instruments": identities,
        }
    )
    return panel, instrument_types, snapshot_sha256


def _data_blockers(manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if manifest["point_in_time_classification"] != "POINT_IN_TIME_VERIFIED":
        blockers.append("POINT_IN_TIME_UNSAFE")
    if manifest["survivorship_status"] != "INCLUDES_DELISTED":
        blockers.append("SURVIVORSHIP_BIAS_PRESENT")
    return blockers


def _absolute_regular_file(raw: object, name: str) -> Path:
    value = _text(raw, name)
    if "://" in value or value.casefold().startswith("file:"):
        raise ValueError(f"{name} must be an absolute local path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute local path.")
    if path.is_symlink():
        raise ValueError(f"{name} must not be a symlink.")
    if not path.exists() or not path.is_file():
        raise ValueError(f"{name} must be an existing regular file.")
    return path.resolve()


def _write_result(
    requested_path: Path,
    output_dir: Path | None,
    result: dict[str, object],
) -> None:
    if output_dir is None:
        raise ValueError("--output-dir is required with --write-result.")
    root = output_dir.resolve()
    target = requested_path.resolve()
    if target.parent != root:
        raise ValueError("--write-result must be directly inside --output-dir.")
    root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=root,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(result, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            temporary_path = Path(handle.name)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _unknown(payload: dict[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}.")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be normalized non-empty text.")
    return value


def _sha256(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256.")
    return text


def _naive_timestamp(value: object, name: str) -> pd.Timestamp:
    text = _text(value, name)
    try:
        timestamp = pd.Timestamp(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid timestamp.") from exc
    if timestamp.tzinfo is not None:
        raise ValueError(f"{name} must be timezone-naive.")
    return timestamp


def _verify_local_hash(
    raw_path: object,
    expected_sha256: object,
    name: str,
) -> None:
    path = _absolute_regular_file(raw_path, name)
    expected = _sha256(expected_sha256, f"{name} expected SHA-256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"{name} does not match its expected SHA-256.")


def _terminal_values(
    raw: object,
    symbols: set[str],
) -> dict[str, dict[str, object]]:
    if not isinstance(raw, dict):
        raise ValueError("terminal_values must be an object.")
    unknown = sorted(set(raw) - symbols)
    if unknown:
        raise ValueError("terminal_values contains an unknown symbol.")
    result: dict[str, dict[str, object]] = {}
    for symbol, value in raw.items():
        if not isinstance(value, dict) or set(value) != {
            "timestamp",
            "price",
            "evidence_path",
            "evidence_sha256",
        }:
            raise ValueError(
                "terminal value evidence fields are invalid; evidence_path "
                "and its SHA-256 are required."
            )
        timestamp = _naive_timestamp(
            value["timestamp"], "terminal value timestamp"
        )
        price = value["price"]
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not float("-inf") < float(price) < float("inf")
            or float(price) < 0
        ):
            raise ValueError(
                "terminal value price must be finite and non-negative."
            )
        evidence = _sha256(
            value["evidence_sha256"], "terminal value evidence_sha256"
        )
        _verify_local_hash(
            value["evidence_path"],
            evidence,
            "terminal value evidence_path",
        )
        result[symbol] = {
            "timestamp": timestamp.isoformat(),
            "price": float(price),
            "evidence_sha256": evidence,
        }
    return result


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
