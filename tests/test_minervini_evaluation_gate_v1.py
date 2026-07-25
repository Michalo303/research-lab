from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from research_lab.research.minervini_evaluation_gate_v1 import (
    evaluate_minervini_result_v1,
)
from scripts.run_minervini_price_volume_core_v1 import main


def _portfolio_result(
    *,
    cagr: float,
    drawdown: float,
    trades: int,
) -> dict[str, object]:
    return {
        "version": "minervini_portfolio_evaluator_result_v1",
        "cagr": cagr,
        "maximum_drawdown": drawdown,
        "trade_count": trades,
        "output_sha256": "a" * 64,
        "provider_calls_used": 0,
        "network_used": False,
        "broker_actions_used": 0,
    }


@pytest.mark.parametrize(
    ("cagr", "drawdown", "trades", "blockers", "verdict"),
    [
        (0.10, -0.15, 100, [], "CANDIDATE"),
        (0.0999, -0.10, 100, [], "FAIL"),
        (0.20, -0.1501, 100, [], "FAIL"),
        (0.20, -0.10, 99, [], "INSUFFICIENT_EVIDENCE"),
        (
            0.20,
            -0.10,
            100,
            ["SURVIVORSHIP_BIAS_PRESENT"],
            "INSUFFICIENT_EVIDENCE",
        ),
    ],
)
def test_gate_is_closed_world(cagr, drawdown, trades, blockers, verdict):
    result = evaluate_minervini_result_v1(
        _portfolio_result(cagr=cagr, drawdown=drawdown, trades=trades),
        data_blockers=blockers,
    )

    assert result["verdict"] == verdict
    assert result["provider_calls_used"] == 0
    assert result["broker_actions_used"] == 0


def _write_local_manifest(tmp_path: Path) -> Path:
    rows = []
    for number, timestamp in enumerate(
        pd.bdate_range("2024-01-02", periods=260)
    ):
        price = 100.0 + number * 0.01
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 1_000_000.0,
            }
        )
    source = tmp_path / "AAA.json"
    source.write_text(
        json.dumps(
            {
                "dataset_id": "AAA_LOCAL_V1",
                "symbol": "AAA",
                "exchange": "US",
                "timezone": "UTC",
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "minervini_local_dataset_manifest_v1",
                "dataset_id": "MINERVINI_TEST_V1",
                "point_in_time_classification": "POINT_IN_TIME_VERIFIED",
                "survivorship_status": "INCLUDES_DELISTED",
                "instruments": [
                    {
                        "symbol": "AAA",
                        "instrument_type": "Common Stock",
                        "exchange": "US",
                        "file_path": str(source),
                        "format": "json",
                        "dataset_id": "AAA_LOCAL_V1",
                        "expected_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_cli_reads_only_local_hash_bound_files_and_writes_nothing_by_default(
    tmp_path, capsys
):
    manifest = _write_local_manifest(tmp_path)

    exit_code = main(["--manifest", str(manifest)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=INSUFFICIENT_EVIDENCE" in output
    assert "provider_calls_used=0" in output
    assert "network_used=False" in output
    assert "snapshot_sha256=" in output
    assert not list(tmp_path.glob("*result*.json"))


def test_cli_rejects_relative_manifest_path(capsys):
    exit_code = main(["--manifest", "manifest.json"])

    assert exit_code == 1
    assert "absolute local path" in capsys.readouterr().out


def test_cli_writes_result_only_to_explicit_direct_child(tmp_path):
    manifest = _write_local_manifest(tmp_path)
    output_dir = tmp_path / "results"
    result_path = output_dir / "minervini-result.json"

    exit_code = main(
        [
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--write-result",
            str(result_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert list(output_dir.iterdir()) == [result_path]


def test_minervini_public_api_exports_the_frozen_pipeline():
    from research_lab import research

    expected = {
        "MinerviniCoreConfigV1",
        "build_minervini_signals_v1",
        "evaluate_minervini_result_v1",
        "run_minervini_eodhd_capability_v1",
        "run_minervini_portfolio_v1",
    }

    assert expected <= set(research.__all__)
    assert all(callable(getattr(research, name)) for name in expected)
