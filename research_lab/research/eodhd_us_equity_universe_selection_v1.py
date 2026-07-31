from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.research.eodhd_qlib_dataset_v1 import (
    CSV_COLUMNS,
    _read_instrument_csv,
    _validate_manifest,
)
from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import (
    BULK_EXCHANGES,
    END_DATE,
    SUPPORTED_EXCHANGE_MICS,
)


MANIFEST_VERSION = "eodhd_qlib_dataset_manifest_v1"
DATASET_ID = "EODHD-US-EQUITY-2006-2022-V1"
PROVENANCE = {"source": "operator_approved_local_snapshot"}
MAXIMUM_MISSING_INTERNAL_MONTHS = 2


def _build_membership_intervals(
    *,
    identities: Sequence[Mapping[str, object]],
    month_ends: Sequence[str],
    bulk_by_date: Mapping[str, Mapping[str, str]],
) -> dict[str, object]:
    ordered_months = list(month_ends)
    if not ordered_months or ordered_months != sorted(set(ordered_months)):
        raise ValueError("month-end dates must be nonempty, unique, and sorted.")
    month_indexes = {value: index for index, value in enumerate(ordered_months)}
    intervals: dict[str, dict[str, str]] = {}
    exclusions: dict[str, str] = {}
    for identity in identities:
        code = str(identity.get("code", ""))
        observations: list[tuple[str, str]] = []
        for month_end in ordered_months:
            exchange = bulk_by_date.get(month_end, {}).get(code)
            if exchange in SUPPORTED_EXCHANGE_MICS:
                observations.append((month_end, SUPPORTED_EXCHANGE_MICS[exchange]))
        if not observations:
            exclusions[code] = "NO_VERIFIED_MAJOR_EXCHANGE_MEMBERSHIP"
            continue
        observed_mics = {mic for _, mic in observations}
        if len(observed_mics) != 1:
            exclusions[code] = "AMBIGUOUS_EXCHANGE_MEMBERSHIP"
            continue
        indexes = [month_indexes[month_end] for month_end, _ in observations]
        if any(
            right - left - 1 > MAXIMUM_MISSING_INTERNAL_MONTHS
            for left, right in zip(indexes, indexes[1:], strict=False)
        ):
            exclusions[code] = "AMBIGUOUS_EXCHANGE_MEMBERSHIP"
            continue
        intervals[code] = {
            "first": observations[0][0],
            "last": observations[-1][0],
            "exchange_mic": next(iter(observed_mics)),
        }
    return {
        "intervals": dict(sorted(intervals.items())),
        "exclusions": dict(sorted(exclusions.items())),
    }


def _select_daily_top_union(
    connection: sqlite3.Connection,
    *,
    maximum_instruments: int,
) -> dict[str, object]:
    if isinstance(maximum_instruments, bool) or not isinstance(maximum_instruments, int) or maximum_instruments <= 0:
        raise ValueError("maximum_instruments must be positive.")
    connection.execute("DROP TABLE IF EXISTS selected_candidates")
    connection.execute(
        """
        CREATE TABLE selected_candidates AS
        SELECT timestamp, instrument_id, liquidity
        FROM (
            SELECT timestamp, instrument_id, liquidity,
                   ROW_NUMBER() OVER (
                       PARTITION BY timestamp
                       ORDER BY liquidity DESC, instrument_id ASC
                   ) AS daily_rank
            FROM candidates
        )
        WHERE daily_rank <= ?
        """,
        (maximum_instruments,),
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS selected_candidates_timestamp_idx ON selected_candidates(timestamp)"
    )
    connection.commit()
    rows = connection.execute(
        "SELECT timestamp, instrument_id FROM selected_candidates ORDER BY timestamp, instrument_id"
    ).fetchall()
    selected_by_date: dict[str, list[str]] = {}
    for timestamp, instrument_id in rows:
        selected_by_date.setdefault(str(timestamp), []).append(str(instrument_id))
    selected_union = sorted({instrument_id for values in selected_by_date.values() for instrument_id in values})
    return {
        "selected_row_count": len(rows),
        "daily_selected_counts": {
            timestamp: len(values) for timestamp, values in selected_by_date.items()
        },
        "selected_by_date": selected_by_date,
        "selected_instrument_ids": selected_union,
    }


def build_point_in_time_qlib_manifest_v1(
    *,
    staging_root: Path,
    state_connection: sqlite3.Connection,
) -> dict[str, object]:
    """Build and hash the selected Qlib manifest without loading all histories together."""

    root = Path(staging_root).resolve()
    identity_path = root / "identity_universe.json"
    identity_payload = json.loads(identity_path.read_text(encoding="utf-8"))
    if not isinstance(identity_payload, dict) or not isinstance(identity_payload.get("identities"), list):
        raise ValueError("identity universe is invalid.")
    identities = identity_payload["identities"]
    identity_by_code = {str(item["code"]): item for item in identities}
    if len(identity_by_code) != len(identities):
        raise ValueError("identity codes are not unique.")
    spy_path = root / "raw" / "session-proxy" / "spy.json.gz"
    spy_payload = json.loads(gzip.decompress(spy_path.read_bytes()).decode("utf-8"))
    if not isinstance(spy_payload, list):
        raise ValueError("SPY session proxy is invalid.")
    expected_development_sessions = []
    previous_spy_date: str | None = None
    for row in spy_payload:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise ValueError("SPY session proxy row is invalid.")
        spy_date = str(row["date"])
        if previous_spy_date is not None and spy_date <= previous_spy_date:
            raise ValueError("SPY session proxy must be strictly ordered.")
        if "2019-01-01" <= spy_date <= END_DATE:
            expected_development_sessions.append(spy_date)
        if spy_date > END_DATE:
            raise ValueError("sealed row is present in SPY session proxy.")
        previous_spy_date = spy_date
    if not expected_development_sessions:
        raise ValueError("development SPY session coverage is empty.")

    bulk_by_date: dict[str, dict[str, str]] = {}
    snapshots_by_date: dict[str, set[str]] = {}
    for bulk_path in sorted((root / "raw" / "bulk").rglob("*.json.gz")):
        relative = bulk_path.relative_to(root / "raw" / "bulk")
        if len(relative.parts) != 2 or relative.parts[0] not in BULK_EXCHANGES:
            raise ValueError("bulk exchange snapshot identity is invalid.")
        exchange = relative.parts[0]
        month_end = bulk_path.name.removesuffix(".json.gz")
        payload = json.loads(gzip.decompress(bulk_path.read_bytes()).decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("bulk artifact is invalid.")
        if exchange in snapshots_by_date.setdefault(month_end, set()):
            raise ValueError("duplicate bulk exchange snapshot is invalid.")
        snapshots_by_date[month_end].add(exchange)
        by_code = bulk_by_date.setdefault(month_end, {})
        seen_in_file: set[str] = set()
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("bulk row is invalid.")
            code = str(row.get("code", "")).strip().upper()
            provider_exchange = str(row.get("exchange_short_name", "")).strip().upper()
            if not code or provider_exchange != "US" or code in seen_in_file:
                raise ValueError("bulk row identity is invalid.")
            seen_in_file.add(code)
            if code not in identity_by_code:
                continue
            previous_exchange = by_code.get(code)
            by_code[code] = (
                exchange
                if previous_exchange in {None, exchange}
                else "AMBIGUOUS"
            )
    month_ends = sorted(bulk_by_date)
    if any(
        snapshots_by_date.get(month_end) != set(BULK_EXCHANGES)
        for month_end in month_ends
    ):
        raise ValueError("bulk exchange snapshot coverage is incomplete.")
    membership = _build_membership_intervals(
        identities=identities,
        month_ends=month_ends,
        bulk_by_date=bulk_by_date,
    )
    intervals = membership["intervals"]
    exclusions = dict(membership["exclusions"])
    history_status_by_subject = {
        str(subject): str(status)
        for subject, status in state_connection.execute(
            "SELECT subject, status FROM requests WHERE kind='SYMBOL_HISTORY' AND subject IS NOT NULL"
        ).fetchall()
    }

    state_connection.execute("DROP TABLE IF EXISTS candidates")
    state_connection.execute(
        "CREATE TABLE candidates(timestamp TEXT NOT NULL, instrument_id TEXT NOT NULL, liquidity REAL NOT NULL)"
    )
    state_connection.execute(
        "CREATE INDEX candidates_timestamp_idx ON candidates(timestamp)"
    )
    retained: dict[str, dict[str, object]] = {}
    candidate_row_count = 0
    sealed_rows = 0
    unresolved_histories = 0
    resolved_empty_histories = 0
    for code, interval in sorted(intervals.items()):
        identity = identity_by_code[code]
        instrument_id = _instrument_id(identity, str(interval["exchange_mic"]))
        digest = hashlib.sha256(instrument_id.encode("utf-8")).hexdigest()
        full_path = root / "ohlcv-full" / digest[:2] / f"{digest}.csv"
        history_status = history_status_by_subject.get(instrument_id)
        if not full_path.is_file() or full_path.is_symlink():
            if history_status == "RESOLVED_EMPTY":
                exclusions[code] = "RESOLVED_EMPTY_HISTORY"
                resolved_empty_histories += 1
            else:
                exclusions[code] = "UNRESOLVED_HISTORY_FAILURE"
                unresolved_histories += 1
            continue
        if history_status is not None and history_status.startswith("FAILED_"):
            exclusions[code] = "UNRESOLVED_HISTORY_FAILURE"
            unresolved_histories += 1
            continue
        body = full_path.read_bytes()
        frame = _read_instrument_csv(body)
        sealed_count = int((frame["timestamp"] > pd.Timestamp(END_DATE)).sum())
        sealed_rows += sealed_count
        if sealed_count:
            raise ValueError("sealed row is present in acquisition history.")
        trimmed = frame.loc[
            (frame["timestamp"] >= pd.Timestamp(str(interval["first"])))
            & (frame["timestamp"] <= pd.Timestamp(str(interval["last"])))
        ].copy()
        if trimmed.empty:
            exclusions[code] = "NO_ROWS_DURING_VERIFIED_MEMBERSHIP"
            continue
        trimmed["prior_session_count"] = range(len(trimmed))
        trimmed["trailing_median_dollar_volume"] = trimmed["dollar_volume"].rolling(
            63, min_periods=63
        ).median()
        eligible = trimmed.loc[
            (trimmed["raw_close"] >= 5.0)
            & (trimmed["prior_session_count"] >= 252)
            & (trimmed["trailing_median_dollar_volume"] >= 10_000_000.0),
            ["timestamp", "trailing_median_dollar_volume"],
        ]
        records = [
            (timestamp.date().isoformat(), instrument_id, float(liquidity))
            for timestamp, liquidity in eligible.itertuples(index=False, name=None)
        ]
        state_connection.executemany(
            "INSERT INTO candidates(timestamp, instrument_id, liquidity) VALUES(?, ?, ?)",
            records,
        )
        candidate_row_count += len(records)
        retained[instrument_id] = {
            "identity": identity,
            "interval": interval,
            "full_path": full_path,
            "digest": digest,
        }
    if unresolved_histories / max(1, len(identities)) > 0.01:
        raise ValueError("unresolved history failures exceed one percent.")
    state_connection.commit()
    selection = _select_daily_top_union(state_connection, maximum_instruments=1_500)
    selected_ids = list(selection["selected_instrument_ids"])
    development_counts = [
        int(selection["daily_selected_counts"].get(timestamp, 0))
        for timestamp in expected_development_sessions
    ]
    if not development_counts or min(development_counts) < 30:
        raise ValueError("development cross-section has fewer than 30 instruments.")

    instruments: list[dict[str, object]] = []
    for instrument_id in selected_ids:
        metadata = retained[instrument_id]
        identity = metadata["identity"]
        interval = metadata["interval"]
        original = pd.read_csv(metadata["full_path"])
        timestamps = pd.to_datetime(original["timestamp"], format="%Y-%m-%d", errors="raise")
        output = original.loc[
            (timestamps >= pd.Timestamp(str(interval["first"])))
            & (timestamps <= pd.Timestamp(str(interval["last"])))
        ].copy()
        if output.empty:
            raise ValueError("selected instrument has no retained rows.")
        relative = Path("ohlcv") / str(metadata["digest"])[:2] / f"{metadata['digest']}.csv"
        body = output.to_csv(index=False, lineterminator="\n").encode("utf-8")
        observed_sha256 = _write_verified(root / relative, body)
        instruments.append(
            {
                "instrument_id": instrument_id,
                "symbol": str(identity["symbol"]),
                "qlib_instrument": str(identity["code"]),
                "instrument_type": "COMMON_STOCK",
                "exchange_mic": str(interval["exchange_mic"]),
                "listing_start": str(output.iloc[0]["timestamp"]),
                "listing_end": (
                    str(output.iloc[-1]["timestamp"])
                    if identity["status"] == "DELISTED"
                    else None
                ),
                "ohlcv_path": relative.as_posix(),
                "ohlcv_sha256": observed_sha256,
            }
        )
    created_row = state_connection.execute(
        "SELECT value FROM meta WHERE key='created_utc'"
    ).fetchone()
    created_utc = str(created_row[0]) if created_row is not None else "2000-01-01T00:00:00Z"
    manifest: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "dataset_id": DATASET_ID,
        "created_utc": created_utc,
        "instruments": instruments,
        "provenance": PROVENANCE,
    }
    _validate_manifest(manifest)
    manifest_path = root / "dataset_manifest.json"
    manifest_sha256 = _write_json(manifest_path, manifest)
    membership_report = {
        "version": "eodhd_us_equity_membership_report_v1",
        "month_end_count": len(month_ends),
        "verified_identity_count": len(intervals),
        "exclusions": dict(sorted(exclusions.items())),
        "sealed_oos_rows": sealed_rows,
        "unresolved_history_count": unresolved_histories,
        "resolved_empty_history_count": resolved_empty_histories,
    }
    membership_report["canonical_report_sha256"] = _canonical_sha256(membership_report)
    _write_json(root / "membership_report.json", membership_report)
    sorted_counts = sorted(int(value) for value in development_counts)
    selection_report = {
        "version": "eodhd_us_equity_selection_report_v1",
        "candidate_row_count": candidate_row_count,
        "selected_row_count": selection["selected_row_count"],
        "selected_instrument_count": len(selected_ids),
        "minimum_development_cross_section": min(development_counts),
        "median_development_cross_section": _median(sorted_counts),
        "expected_development_session_count": len(expected_development_sessions),
        "daily_selected_counts": selection["daily_selected_counts"],
        "manifest_sha256": manifest_sha256,
        "sealed_oos_rows": sealed_rows,
        "unresolved_history_count": unresolved_histories,
    }
    selection_report["canonical_report_sha256"] = _canonical_sha256(selection_report)
    _write_json(root / "selection_report.json", selection_report)
    return {
        "status": "POINT_IN_TIME_MANIFEST_COMPLETE",
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "selected_instrument_count": len(selected_ids),
        "minimum_development_cross_section": min(development_counts),
        "median_development_cross_section": _median(sorted_counts),
        "sealed_oos_rows": sealed_rows,
        "membership_report_path": str(root / "membership_report.json"),
        "selection_report_path": str(root / "selection_report.json"),
    }


def _instrument_id(identity: Mapping[str, object], exchange_mic: str) -> str:
    return f"EODHD-US-{exchange_mic}-{identity['code']}"


def _write_verified(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    if temporary.read_bytes() != body:
        raise OSError("artifact verification failed.")
    temporary.replace(path)
    return hashlib.sha256(body).hexdigest()


def _write_json(path: Path, payload: object) -> str:
    body = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return _write_verified(path, body)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _median(values: Sequence[int]) -> float:
    if not values:
        raise ValueError("median requires observations.")
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return (float(values[midpoint - 1]) + float(values[midpoint])) / 2.0
