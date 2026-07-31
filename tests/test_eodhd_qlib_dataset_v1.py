from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from research_lab.research.eodhd_qlib_dataset_v1 import load_eodhd_qlib_development_frame_v1


CSV_COLUMNS = ("timestamp", "open", "high", "low", "close", "adjusted_close", "volume")


def _rows(
    dates: pd.DatetimeIndex,
    *,
    close: float = 50.0,
    volume: float = 1_000_000.0,
) -> list[dict[str, object]]:
    return [
        {
            "timestamp": timestamp.date().isoformat(),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "adjusted_close": close,
            "volume": volume,
        }
        for timestamp in dates
    ]


def _write_csv(path: Path, rows: list[dict[str, object]], *, columns: tuple[str, ...] = CSV_COLUMNS) -> str:
    body = pd.DataFrame(rows).loc[:, list(columns)].to_csv(index=False, lineterminator="\n")
    path.write_text(body, encoding="utf-8", newline="")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    root: Path,
    rows: list[dict[str, object]],
    *,
    listing_end: str | None = None,
    instrument_updates: dict[str, object] | None = None,
    manifest_updates: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object]]:
    csv_path = root / "AAA.US.csv"
    csv_sha = _write_csv(csv_path, rows)
    instrument: dict[str, object] = {
        "instrument_id": "US-XNAS-AAA",
        "symbol": "AAA.US",
        "qlib_instrument": "AAA",
        "instrument_type": "COMMON_STOCK",
        "exchange_mic": "XNAS",
        "listing_start": rows[0]["timestamp"],
        "listing_end": listing_end,
        "ohlcv_path": csv_path.name,
        "ohlcv_sha256": csv_sha,
    }
    instrument.update(instrument_updates or {})
    manifest: dict[str, object] = {
        "version": "eodhd_qlib_dataset_manifest_v1",
        "dataset_id": "EODHD-TEST-V1",
        "created_utc": "2026-07-31T00:00:00Z",
        "instruments": [instrument],
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    manifest.update(manifest_updates or {})
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    request = _request(manifest_path, rows)
    return manifest_path, request


def _request(manifest_path: Path, rows: list[dict[str, object]]) -> dict[str, object]:
    dates = [pd.Timestamp(row["timestamp"]) for row in rows]
    split = dates[min(125, len(dates) - 2)]
    sealed_start = max(dates) + pd.Timedelta(days=1)
    return {
        "version": "eodhd_qlib_development_frame_request_v1",
        "manifest_path": str(manifest_path.resolve()),
        "expected_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "discovery_interval": {"start": min(dates).date().isoformat(), "end": split.date().isoformat()},
        "development_interval": {
            "start": (split + pd.Timedelta(days=1)).date().isoformat(),
            "end": max(dates).date().isoformat(),
        },
        "sealed_oos_interval": {
            "dataset_version": "SEALED-TEST-V1",
            "start": sealed_start.date().isoformat(),
            "end": (sealed_start + pd.Timedelta(days=365)).date().isoformat(),
        },
        "universe": {
            "minimum_price": 5.0,
            "minimum_history_sessions": 252,
            "minimum_median_dollar_volume": 10_000_000.0,
            "maximum_instruments": 1500,
        },
        "provenance": {"source": "operator_approved_local_snapshot"},
    }


def test_loads_adjusted_prices_and_point_in_time_eligibility(tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=253)
    rows = _rows(dates)
    rows[-1].update({"open": 49.0, "high": 51.0, "low": 48.0, "close": 50.0, "adjusted_close": 100.0})
    _, request = _write_manifest(tmp_path, rows)

    frame, metadata = load_eodhd_qlib_development_frame_v1(request)

    last = (dates[-1], "AAA")
    assert frame.index.names == ["datetime", "instrument"]
    assert frame.loc[last, "open"] == pytest.approx(98.0)
    assert frame.loc[last, "high"] == pytest.approx(102.0)
    assert frame.loc[last, "low"] == pytest.approx(96.0)
    assert frame.loc[last, "close"] == pytest.approx(100.0)
    assert frame.loc[last, "raw_close"] == pytest.approx(50.0)
    assert bool(frame.loc[last, "eligible"]) is True
    assert set(frame.columns) == {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "raw_close",
        "dollar_volume",
        "eligible",
    }
    assert metadata["provider_calls_used"] == 0
    assert metadata["sealed_oos_rows_read"] == 0
    assert metadata["dataset_manifest_sha256"] == request["expected_manifest_sha256"]
    assert len(metadata["canonical_metadata_sha256"]) == 64


def test_future_liquidity_rows_never_change_earlier_eligibility(tmp_path: Path) -> None:
    dates = pd.bdate_range("2019-01-02", periods=316)
    all_rows = _rows(dates[:253], volume=1_000.0) + _rows(dates[253:], volume=1_000_000.0)
    first_root = tmp_path / "first"
    first_root.mkdir()
    _, first_request = _write_manifest(first_root, all_rows[:253])
    second_root = tmp_path / "second"
    second_root.mkdir()
    _, second_request = _write_manifest(second_root, all_rows)

    first, _ = load_eodhd_qlib_development_frame_v1(first_request)
    second, _ = load_eodhd_qlib_development_frame_v1(second_request)

    pd.testing.assert_series_equal(first["eligible"], second.loc[first.index, "eligible"])
    assert not bool(first["eligible"].any())


def test_delisted_instrument_is_ineligible_after_listing_end(tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=254)
    rows = _rows(dates)
    _, request = _write_manifest(tmp_path, rows, listing_end=dates[-2].date().isoformat())

    frame, metadata = load_eodhd_qlib_development_frame_v1(request)

    assert bool(frame.loc[(dates[-2], "AAA"), "eligible"]) is True
    assert bool(frame.loc[(dates[-1], "AAA"), "eligible"]) is False
    assert metadata["active_instrument_count"] == 0
    assert metadata["delisted_instrument_count"] == 1


def test_rejects_rows_before_declared_listing_start(tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=300)
    rows = _rows(dates)
    _, request = _write_manifest(
        tmp_path,
        rows,
        instrument_updates={"listing_start": dates[100].date().isoformat()},
    )

    with pytest.raises(ValueError, match="precedes listing_start"):
        load_eodhd_qlib_development_frame_v1(request)


@pytest.mark.parametrize("corruption", ["hash", "escape", "symlink", "duplicate", "unknown"])
def test_rejects_manifest_identity_and_path_corruption(
    corruption: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.bdate_range("2020-01-02", periods=3)
    rows = _rows(dates)
    manifest_path, request = _write_manifest(tmp_path, rows)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if corruption == "hash":
        request["expected_manifest_sha256"] = "0" * 64
    elif corruption == "escape":
        manifest["instruments"][0]["ohlcv_path"] = "../escape.csv"
    elif corruption == "symlink":
        csv_path = (tmp_path / "AAA.US.csv").resolve()
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self.resolve() == csv_path or original(self))
    elif corruption == "duplicate":
        duplicate = dict(manifest["instruments"][0])
        duplicate["instrument_id"] = "US-XNAS-BBB"
        duplicate["qlib_instrument"] = "BBB"
        manifest["instruments"].append(duplicate)
    else:
        manifest["instruments"][0]["unexpected"] = True
    if corruption in {"escape", "duplicate", "unknown"}:
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        request["expected_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError):
        load_eodhd_qlib_development_frame_v1(request)


@pytest.mark.parametrize("corruption", ["missing_adjusted", "bad_ohlc", "duplicate", "unordered", "sealed"])
def test_rejects_corrupt_or_sealed_csv_rows(corruption: str, tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=4)
    rows = _rows(dates)
    columns = CSV_COLUMNS
    if corruption == "missing_adjusted":
        columns = tuple(column for column in CSV_COLUMNS if column != "adjusted_close")
    elif corruption == "bad_ohlc":
        rows[1]["high"] = 1.0
    elif corruption == "duplicate":
        rows[2]["timestamp"] = rows[1]["timestamp"]
    elif corruption == "unordered":
        rows[1], rows[2] = rows[2], rows[1]

    manifest_path, request = _write_manifest(tmp_path, rows)
    if corruption != "sealed":
        csv_path = tmp_path / "AAA.US.csv"
        csv_sha = _write_csv(csv_path, rows, columns=columns)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["instruments"][0]["ohlcv_sha256"] = csv_sha
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        request["expected_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    else:
        request["sealed_oos_interval"]["start"] = rows[-1]["timestamp"]

    with pytest.raises(ValueError):
        load_eodhd_qlib_development_frame_v1(request)
