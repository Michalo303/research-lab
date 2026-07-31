from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd


FACTOR_SCREEN_VERSION = "qlib_factor_screen_v1"
LABEL_COLUMN = "forward_return_5d"
_COST_FIELDS = {"base_bps_one_way", "stress_bps_one_way", "severe_bps_one_way"}


def run_qlib_factor_screen_v1(
    development_frame: pd.DataFrame,
    *,
    factor_metadata: dict[str, object],
    costs: dict[str, float],
) -> dict[str, object]:
    """Evaluate every catalog factor on weekly eligible cross-sections."""

    return _run_qlib_factor_screen_v1(
        development_frame,
        factor_metadata=factor_metadata,
        costs=costs,
        minimum_cross_section_size=30,
    )


def _run_qlib_factor_screen_v1(
    development_frame: pd.DataFrame,
    *,
    factor_metadata: dict[str, object],
    costs: dict[str, float],
    minimum_cross_section_size: int,
) -> dict[str, object]:
    factor_ids, directions, catalog_sha256 = _validate_metadata(factor_metadata)
    validated_costs = _validate_costs(costs)
    if isinstance(minimum_cross_section_size, bool) or not isinstance(minimum_cross_section_size, int):
        raise ValueError("minimum_cross_section_size must be an integer.")
    if minimum_cross_section_size < 3:
        raise ValueError("minimum_cross_section_size must be at least three.")
    frame = _validate_and_sort_frame(development_frame, factor_ids)
    weekly = _last_session_rows(frame)

    factor_results: dict[str, dict[str, object]] = {}
    observation_dates: list[pd.Timestamp] = []
    for factor_id in factor_ids:
        result, dates = _evaluate_factor(
            weekly,
            factor_id=factor_id,
            direction=directions[factor_id],
            costs=validated_costs,
            minimum_cross_section_size=minimum_cross_section_size,
        )
        factor_results[factor_id] = result
        observation_dates.extend(dates)
    minimum_weeks = min(int(result["weekly_observation_count"]) for result in factor_results.values())
    if minimum_weeks < 52:
        raise ValueError("insufficient weekly observations")

    result: dict[str, object] = {
        "version": FACTOR_SCREEN_VERSION,
        "status": "COMPLETED",
        "factor_catalog_sha256": catalog_sha256,
        "factor_count": len(factor_ids),
        "ordered_factor_ids": list(factor_ids),
        "weekly_observation_count": minimum_weeks,
        "maximum_observation_date": max(observation_dates).date().isoformat(),
        "costs": validated_costs,
        "factors": factor_results,
        "sector_concentration_evaluated": False,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
    }
    result["canonical_screen_sha256"] = _canonical_sha256(result)
    return result


def _evaluate_factor(
    weekly: pd.DataFrame,
    *,
    factor_id: str,
    direction: float,
    costs: dict[str, float],
    minimum_cross_section_size: int,
) -> tuple[dict[str, object], list[pd.Timestamp]]:
    rank_ics: list[float] = []
    gross_spreads: list[float] = []
    gross_top_universe: list[float] = []
    dates: list[pd.Timestamp] = []
    instrument_contributions: dict[str, float] = {}
    year_net_returns: dict[str, list[float]] = {}
    instrument_counts: list[int] = []

    for timestamp, timestamp_frame in weekly.groupby(level="datetime", sort=True):
        cross_section = timestamp_frame.loc[
            timestamp_frame["eligible"] & timestamp_frame[factor_id].notna() & timestamp_frame[LABEL_COLUMN].notna(),
            [factor_id, LABEL_COLUMN],
        ].copy()
        if cross_section.empty:
            continue
        if len(cross_section) < minimum_cross_section_size:
            raise ValueError("insufficient cross-section size")
        cross_section = cross_section.reset_index()
        cross_section["effective_factor"] = cross_section[factor_id] * direction
        factor_ranks = cross_section["effective_factor"].rank(method="average")
        label_ranks = cross_section[LABEL_COLUMN].rank(method="average")
        rank_ic = factor_ranks.corr(label_ranks)
        if pd.isna(rank_ic):
            continue
        top = cross_section.sort_values(
            ["effective_factor", "instrument"],
            ascending=[False, True],
            kind="mergesort",
        )
        bottom = cross_section.sort_values(
            ["effective_factor", "instrument"],
            ascending=[True, True],
            kind="mergesort",
        )
        quantile_size = max(1, len(cross_section) // 5)
        top = top.head(quantile_size)
        bottom = bottom.head(quantile_size)
        top_mean = float(top[LABEL_COLUMN].mean())
        universe_mean = float(cross_section[LABEL_COLUMN].mean())
        gross_spread = top_mean - float(bottom[LABEL_COLUMN].mean())
        top_universe = top_mean - universe_mean

        top_instruments = set(top["instrument"])
        for row in cross_section.itertuples(index=False):
            weight = (1.0 / quantile_size if row.instrument in top_instruments else 0.0) - (
                1.0 / len(cross_section)
            )
            instrument_contributions[row.instrument] = instrument_contributions.get(row.instrument, 0.0) + (
                weight * float(getattr(row, LABEL_COLUMN))
            )

        base_net = top_universe - 2.0 * costs["base_bps_one_way"] / 10_000.0
        year_net_returns.setdefault(str(pd.Timestamp(timestamp).year), []).append(base_net)
        rank_ics.append(float(rank_ic))
        gross_spreads.append(gross_spread)
        gross_top_universe.append(top_universe)
        dates.append(pd.Timestamp(timestamp))
        instrument_counts.append(len(cross_section))

    if len(rank_ics) < 52:
        raise ValueError("insufficient weekly observations")
    median_rank_ic = float(np.median(rank_ics))
    mean_rank_ic = float(np.mean(rank_ics))
    std_rank_ic = float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else 0.0
    if std_rank_ic > 0.0:
        icir = mean_rank_ic / std_rank_ic
    elif mean_rank_ic > 0.0:
        icir = 999.0
    elif mean_rank_ic < 0.0:
        icir = -999.0
    else:
        icir = 0.0
    positive_share = sum(value > 0.0 for value in rank_ics) / len(rank_ics)
    mean_gross_spread = float(np.mean(gross_spreads))
    base_net_spread = mean_gross_spread - 2.0 * costs["base_bps_one_way"] / 10_000.0
    stress_net_spread = mean_gross_spread - 2.0 * costs["stress_bps_one_way"] / 10_000.0
    severe_net_spread = mean_gross_spread - 2.0 * costs["severe_bps_one_way"] / 10_000.0
    weekly_base_top_universe = [
        value - 2.0 * costs["base_bps_one_way"] / 10_000.0 for value in gross_top_universe
    ]
    annualized = _annualized_return(weekly_base_top_universe)
    per_year = {
        year: _compound(values)
        for year, values in sorted(year_net_returns.items())
    }
    year_share = _positive_concentration(per_year)
    instrument_share = _positive_concentration(instrument_contributions)

    failures: list[str] = []
    if positive_share < 0.55:
        failures.append("RANK_IC_DIRECTION_UNSTABLE")
    if stress_net_spread <= 0.0:
        failures.append("EDGE_DESTROYED_BY_STRESS_COSTS")
    if year_share > 0.40:
        failures.append("SINGLE_YEAR_PROFIT_CONCENTRATION")
    if instrument_share > 0.20:
        failures.append("SINGLE_INSTRUMENT_PROFIT_CONCENTRATION")
    if median_rank_ic < 0.015 and annualized < 0.02:
        failures.append("INSUFFICIENT_EDGE_MAGNITUDE")

    result: dict[str, object] = {
        "factor_id": factor_id,
        "decision": "FACTOR_CONTINUE" if not failures else "FACTOR_STOP",
        "failure_taxonomy": failures,
        "weekly_observation_count": len(rank_ics),
        "minimum_instrument_count": min(instrument_counts),
        "maximum_instrument_count": max(instrument_counts),
        "median_rank_ic": median_rank_ic,
        "icir": float(icir),
        "positive_rank_ic_week_share": float(positive_share),
        "gross_top_bottom_spread": mean_gross_spread,
        "gross_top_minus_universe_return": float(np.mean(gross_top_universe)),
        "base_net_spread": base_net_spread,
        "stress_net_spread": stress_net_spread,
        "severe_net_spread": severe_net_spread,
        "annualized_net_top_minus_universe_return": annualized,
        "per_year_net_return": per_year,
        "single_year_profit_share": year_share,
        "per_instrument_gross_contribution": dict(sorted(instrument_contributions.items())),
        "single_instrument_profit_share": instrument_share,
    }
    return result, dates


def _last_session_rows(frame: pd.DataFrame) -> pd.DataFrame:
    value = frame.reset_index()
    value["week"] = value["datetime"].dt.to_period("W-FRI")
    last_dates = value.groupby("week", sort=True)["datetime"].transform("max")
    value = value.loc[value["datetime"] == last_dates].drop(columns="week")
    return value.set_index(["datetime", "instrument"]).sort_index(kind="mergesort")


def _validate_and_sort_frame(frame: Any, factor_ids: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("development_frame must be a DataFrame.")
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["datetime", "instrument"]:
        raise ValueError("development_frame index is invalid.")
    if frame.index.has_duplicates:
        raise ValueError("development_frame index contains duplicates.")
    required = {*factor_ids, LABEL_COLUMN, "eligible"}
    if set(frame.columns) != required:
        raise ValueError("development_frame columns are invalid.")
    dates = frame.index.get_level_values("datetime")
    if not isinstance(dates, pd.DatetimeIndex) or dates.tz is not None:
        raise ValueError("development_frame datetime index is invalid.")
    if not pd.api.types.is_bool_dtype(frame["eligible"]):
        raise ValueError("eligible must be boolean.")
    numeric = frame[[*factor_ids, LABEL_COLUMN]].to_numpy(dtype="float64")
    if np.isinf(numeric).any():
        raise ValueError("factor and label values must not be infinite.")
    return frame.sort_index(kind="mergesort").copy()


def _validate_metadata(raw: Any) -> tuple[tuple[str, ...], dict[str, float], str]:
    if not isinstance(raw, dict):
        raise ValueError("factor_metadata must be a mapping.")
    required = {
        "version",
        "factor_count",
        "ordered_factor_ids",
        "definitions",
        "label",
        "canonical_catalog_sha256",
    }
    if set(raw) != required:
        raise ValueError("factor_metadata fields are invalid.")
    declared = _required_sha256(raw.get("canonical_catalog_sha256"), "canonical_catalog_sha256")
    hashable = {key: value for key, value in raw.items() if key != "canonical_catalog_sha256"}
    if _canonical_sha256(hashable) != declared:
        raise ValueError("factor catalog hash mismatch.")
    ordered = raw.get("ordered_factor_ids")
    definitions = raw.get("definitions")
    if not isinstance(ordered, list) or not ordered or not all(isinstance(item, str) and item for item in ordered):
        raise ValueError("ordered_factor_ids are invalid.")
    if len(ordered) != len(set(ordered)) or raw.get("factor_count") != len(ordered):
        raise ValueError("factor count is invalid.")
    if not isinstance(definitions, list) or len(definitions) != len(ordered):
        raise ValueError("factor definitions are invalid.")
    directions: dict[str, float] = {}
    for expected_id, definition in zip(ordered, definitions):
        if not isinstance(definition, dict) or set(definition) != {
            "factor_id",
            "family_id",
            "description",
            "higher_is_better",
        }:
            raise ValueError("factor definition fields are invalid.")
        if definition.get("factor_id") != expected_id or not isinstance(definition.get("higher_is_better"), bool):
            raise ValueError("factor definition identity is invalid.")
        directions[expected_id] = 1.0 if definition["higher_is_better"] else -1.0
    label = raw.get("label")
    if not isinstance(label, dict) or label.get("label_id") != LABEL_COLUMN:
        raise ValueError("factor label is invalid.")
    return tuple(ordered), directions, declared


def _validate_costs(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != _COST_FIELDS:
        raise ValueError("cost fields are invalid.")
    result: dict[str, float] = {}
    for field in ("base_bps_one_way", "stress_bps_one_way", "severe_bps_one_way"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
            raise ValueError("cost value is invalid.")
        result[field] = float(value)
    if not result["base_bps_one_way"] < result["stress_bps_one_way"] < result["severe_bps_one_way"]:
        raise ValueError("cost scenarios must be strictly increasing.")
    return result


def _annualized_return(weekly_returns: list[float]) -> float:
    if any(value <= -1.0 for value in weekly_returns):
        return -1.0
    compounded = _compound(weekly_returns)
    return float((1.0 + compounded) ** (52.0 / len(weekly_returns)) - 1.0)


def _compound(returns: list[float]) -> float:
    if any(value <= -1.0 for value in returns):
        return -1.0
    return float(np.prod(np.asarray(returns, dtype="float64") + 1.0) - 1.0)


def _positive_concentration(values: dict[str, float]) -> float:
    positives = [float(value) for value in values.values() if value > 0.0]
    if not positives:
        return 1.0
    return max(positives) / sum(positives)


def _required_sha256(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"{name} must be lowercase SHA-256.")
    return raw


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
