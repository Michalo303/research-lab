from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import _open_state
from research_lab.research.eodhd_us_equity_universe_selection_v1 import (
    _build_membership_intervals,
    _select_daily_top_union,
    build_point_in_time_qlib_manifest_v1,
)


def _identity(code: str, *, status: str = "ACTIVE", mic: str = "XNAS") -> dict[str, object]:
    return {
        "code": code,
        "symbol": f"{code}.US",
        "status": status,
        "exchange": "NASDAQ" if mic == "XNAS" else "NYSE",
        "exchange_mic": mic,
        "currency": "USD",
        "isin": f"US-{code}",
    }


def test_membership_intervals_are_conservative_and_reject_material_internal_gaps() -> None:
    identities = [_identity("AAA"), _identity("BBB", mic="XNYS")]
    month_ends = ["2020-01-31", "2020-02-28", "2020-03-31", "2020-04-30", "2020-05-29"]
    bulk_by_date = {
        "2020-01-31": {"AAA": "NASDAQ", "BBB": "NYSE"},
        "2020-02-28": {"AAA": "NASDAQ"},
        "2020-03-31": {"AAA": "NASDAQ"},
        "2020-04-30": {"AAA": "NASDAQ"},
        "2020-05-29": {"AAA": "NASDAQ", "BBB": "NYSE"},
    }

    result = _build_membership_intervals(
        identities=identities,
        month_ends=month_ends,
        bulk_by_date=bulk_by_date,
    )

    assert result["intervals"] == {
        "AAA": {"first": "2020-01-31", "last": "2020-05-29", "exchange_mic": "XNAS"}
    }
    assert result["exclusions"] == {"BBB": "AMBIGUOUS_EXCHANGE_MEMBERSHIP"}


def test_sqlite_selection_is_point_in_time_capped_and_deterministic(tmp_path: Path) -> None:
    connection = _open_state(tmp_path / "state.sqlite", "a" * 64)
    connection.execute(
        "CREATE TABLE candidates(timestamp TEXT NOT NULL, instrument_id TEXT NOT NULL, liquidity REAL NOT NULL)"
    )
    rows = []
    for day in ("2020-01-02", "2020-01-03"):
        for index in range(40):
            liquidity = float(index)
            if day == "2020-01-03" and index == 0:
                liquidity = 10_000.0
            rows.append((day, f"I{index:02d}", liquidity))
    connection.executemany("INSERT INTO candidates VALUES(?, ?, ?)", rows)
    connection.commit()

    result = _select_daily_top_union(connection, maximum_instruments=30)

    assert result["selected_row_count"] == 60
    assert result["daily_selected_counts"] == {"2020-01-02": 30, "2020-01-03": 30}
    assert "I00" not in result["selected_by_date"]["2020-01-02"]
    assert "I00" in result["selected_by_date"]["2020-01-03"]
    assert result["selected_by_date"]["2020-01-02"] == [f"I{index:02d}" for index in range(10, 40)]
    connection.close()


def _write_json_gzip(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))


def _build_selection_fixture(tmp_path: Path) -> tuple[Path, object]:
    staging = tmp_path / "stage"
    staging.mkdir()
    connection = _open_state(staging / "state.sqlite", "a" * 64)
    identities = [_identity(f"S{index:02d}", status="DELISTED" if index < 5 else "ACTIVE") for index in range(40)]
    identity_bytes = (json.dumps(
        {"identities": identities}, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")
    (staging / "identity_universe.json").write_bytes(identity_bytes)

    dates = pd.bdate_range("2017-01-02", "2022-12-30")
    _write_json_gzip(
        staging / "raw" / "session-proxy" / "spy.json.gz",
        [
            {
                "date": stamp.date().isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "adjusted_close": 100.0,
                "volume": 10_000_000,
            }
            for stamp in dates
        ],
    )
    month_ends = [
        group.index[-1].date().isoformat()
        for _, group in pd.DataFrame(index=dates).groupby(dates.to_period("M"))
    ]
    for month_end in month_ends:
        nasdaq_bulk = [
            {
                "code": str(identity["code"]),
                "exchange_short_name": "US",
                "date": month_end,
                "open": 20.0,
                "high": 21.0,
                "low": 19.0,
                "close": 20.0,
                "adjusted_close": 20.0,
                "volume": 2_000_000,
            }
            for identity in identities
        ]
        for exchange in ("AMEX", "NASDAQ", "NYSE"):
            _write_json_gzip(
                staging / "raw" / "bulk" / exchange / f"{month_end}.json.gz",
                nasdaq_bulk if exchange == "NASDAQ" else [],
            )

    for index, identity in enumerate(identities):
        instrument_id = f"EODHD-US-XNAS-{identity['code']}"
        digest = hashlib.sha256(instrument_id.encode("utf-8")).hexdigest()
        frame = pd.DataFrame(
            {
                "timestamp": dates.date.astype(str),
                "open": 20.0 + index / 100.0,
                "high": 21.0 + index / 100.0,
                "low": 19.0 + index / 100.0,
                "close": 20.0 + index / 100.0,
                "adjusted_close": 20.0 + index / 100.0,
                "volume": 2_000_000.0 + index * 10_000.0,
            }
        )
        path = staging / "ohlcv-full" / digest[:2] / f"{digest}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return staging, connection


def test_manifest_builder_streams_point_in_time_union_and_writes_loader_contract(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)

    result = build_point_in_time_qlib_manifest_v1(
        staging_root=staging,
        state_connection=connection,
    )

    assert result["status"] == "POINT_IN_TIME_MANIFEST_COMPLETE"
    assert result["selected_instrument_count"] == 40
    assert result["minimum_development_cross_section"] == 40
    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "eodhd_qlib_dataset_manifest_v1"
    assert len(manifest["instruments"]) == 40
    assert sum(item["listing_end"] is not None for item in manifest["instruments"]) == 5
    assert all(not Path(item["ohlcv_path"]).is_absolute() for item in manifest["instruments"])
    assert all((staging / item["ohlcv_path"]).is_file() for item in manifest["instruments"])
    assert all(len(item["ohlcv_sha256"]) == 64 for item in manifest["instruments"])
    first_csv = staging / manifest["instruments"][0]["ohlcv_path"]
    first_frame = pd.read_csv(first_csv)
    assert first_frame["timestamp"].min() >= "2017-01-31"
    assert first_frame["timestamp"].max() <= "2022-12-30"
    assert result["sealed_oos_rows"] == 0
    connection.close()


def test_manifest_builder_reads_history_by_source_identity_after_exchange_change(
    tmp_path: Path,
) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    identity_path = staging / "identity_universe.json"
    identity_payload = json.loads(identity_path.read_text(encoding="utf-8"))
    changed = identity_payload["identities"][0]
    assert changed["code"] == "S00"
    changed["exchange"] = "NYSE"
    changed["exchange_mic"] = "XNYS"
    identity_path.write_text(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    historical_id = "EODHD-US-XNAS-S00"
    source_id = "EODHD-US-XNYS-S00"
    historical_digest = hashlib.sha256(historical_id.encode("utf-8")).hexdigest()
    source_digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
    historical_path = staging / "ohlcv-full" / historical_digest[:2] / f"{historical_digest}.csv"
    source_path = staging / "ohlcv-full" / source_digest[:2] / f"{source_digest}.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    historical_path.replace(source_path)

    result = build_point_in_time_qlib_manifest_v1(
        staging_root=staging,
        state_connection=connection,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    instrument = next(item for item in manifest["instruments"] if item["qlib_instrument"] == "S00")
    assert instrument["instrument_id"] == historical_id
    assert instrument["exchange_mic"] == "XNAS"
    assert (staging / instrument["ohlcv_path"]).is_file()
    connection.close()


def test_manifest_builder_excludes_provider_rows_outside_spy_sessions(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    holiday = "2021-12-24"
    spy_path = staging / "raw" / "session-proxy" / "spy.json.gz"
    spy_rows = json.loads(gzip.decompress(spy_path.read_bytes()).decode("utf-8"))
    assert any(row["date"] == holiday for row in spy_rows)
    _write_json_gzip(spy_path, [row for row in spy_rows if row["date"] != holiday])

    result = build_point_in_time_qlib_manifest_v1(
        staging_root=staging,
        state_connection=connection,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    for instrument in manifest["instruments"]:
        frame = pd.read_csv(staging / instrument["ohlcv_path"])
        assert holiday not in set(frame["timestamp"])
    report = json.loads((staging / "selection_report.json").read_text(encoding="utf-8"))
    assert holiday not in report["daily_selected_counts"]
    connection.close()


def test_manifest_builder_rejects_any_sealed_row(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    path = next((staging / "ohlcv-full").rglob("*.csv"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2023-01-03,20,21,19,20,20,2000000\n")

    with pytest.raises(ValueError, match="sealed"):
        build_point_in_time_qlib_manifest_v1(
            staging_root=staging,
            state_connection=connection,
        )
    connection.close()


def test_manifest_builder_rejects_missing_expected_development_session(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    missing_day = "2020-06-15"
    for path in (staging / "ohlcv-full").rglob("*.csv"):
        frame = pd.read_csv(path)
        frame = frame.loc[frame["timestamp"] != missing_day]
        path.write_bytes(frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))

    with pytest.raises(ValueError, match="cross-section"):
        build_point_in_time_qlib_manifest_v1(
            staging_root=staging,
            state_connection=connection,
        )
    connection.close()


def test_manifest_builder_rejects_more_than_one_percent_unresolved_histories(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    next((staging / "ohlcv-full").rglob("*.csv")).unlink()

    with pytest.raises(ValueError, match="unresolved"):
        build_point_in_time_qlib_manifest_v1(
            staging_root=staging,
            state_connection=connection,
        )
    connection.close()


def test_manifest_builder_rejects_missing_exchange_month_snapshot(tmp_path: Path) -> None:
    staging, connection = _build_selection_fixture(tmp_path)
    next((staging / "raw" / "bulk" / "AMEX").glob("*.json.gz")).unlink()

    with pytest.raises(ValueError, match="exchange snapshot"):
        build_point_in_time_qlib_manifest_v1(
            staging_root=staging,
            state_connection=connection,
        )
    connection.close()
