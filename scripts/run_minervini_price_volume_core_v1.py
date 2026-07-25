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
        "instruments",
    }
    _unknown(payload, allowed, "manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise ValueError(f"manifest version must be {MANIFEST_VERSION}.")
    for field in (
        "dataset_id",
        "point_in_time_classification",
        "survivorship_status",
    ):
        _text(payload.get(field), field)
    instruments = payload.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("manifest instruments must be a non-empty list.")
    normalized = [_instrument(item) for item in instruments]
    symbols = [item["symbol"] for item in normalized]
    if len(symbols) != len(set(symbols)):
        raise ValueError("manifest symbols must be unique.")
    return {**payload, "instruments": sorted(normalized, key=lambda x: x["symbol"])}


def _instrument(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("manifest instrument must be an object.")
    allowed = {
        "symbol",
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
    if result["format"] not in {"json", "jsonl"}:
        raise ValueError("instrument format must be json or jsonl.")
    if (
        len(result["expected_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in result["expected_sha256"])
    ):
        raise ValueError("expected_sha256 must be a lowercase SHA-256.")
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
        frames[item["symbol"]] = frame.set_index("timestamp")[
            ["open", "high", "low", "close", "volume"]
        ]
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
