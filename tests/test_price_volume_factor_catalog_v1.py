from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_lab.research.price_volume_factor_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_price_volume_factor_catalog_metadata_v1,
    compute_price_volume_factor_frame_v1,
)


FACTOR_IDS = (
    "MOM_12_1",
    "MOM_6_1",
    "TREND_200",
    "HIGH_252",
    "LOW_VOL_60",
    "DRAWDOWN_252",
    "VOLUME_CONFIRM_20",
    "SHORT_REVERSAL_5",
)


def _source_frame(periods: int = 300, instruments: tuple[str, ...] = ("AAA", "BBB")) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-02", periods=periods)
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    # MultiIndex.from_product varies instrument fastest, so use repeat for the time trend.
    sequence = np.repeat(np.arange(periods, dtype="float64"), len(instruments))
    close = 50.0 + sequence * 0.1 + np.tile(np.arange(len(instruments)), periods)
    frame = pd.DataFrame(index=index)
    frame["open"] = close * 0.999
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    frame["close"] = close
    frame["volume"] = 1_000_000.0 + sequence * 100.0
    frame["raw_close"] = close
    frame["dollar_volume"] = frame["raw_close"] * frame["volume"]
    frame["eligible"] = True
    return frame


def test_catalog_is_closed_and_has_exactly_eight_factors() -> None:
    assert tuple(FACTOR_DEFINITIONS_V1) == FACTOR_IDS
    assert all(definition["factor_id"] == factor_id for factor_id, definition in FACTOR_DEFINITIONS_V1.items())
    metadata = build_price_volume_factor_catalog_metadata_v1()
    assert metadata["factor_count"] == 8
    assert metadata["ordered_factor_ids"] == list(FACTOR_IDS)
    assert len(metadata["canonical_catalog_sha256"]) == 64


def test_forward_label_uses_next_open_and_fifth_future_close() -> None:
    frame = _source_frame(periods=10, instruments=("AAA",))
    dates = frame.index.get_level_values("datetime").unique()
    frame.loc[(dates[1], "AAA"), "open"] = 100.0
    frame.loc[(dates[5], "AAA"), "close"] = 110.0

    result = compute_price_volume_factor_frame_v1(frame)

    assert result.loc[(dates[0], "AAA"), "forward_return_5d"] == pytest.approx(0.10)


def test_future_mutation_never_changes_current_or_prior_factor_values() -> None:
    source = _source_frame()
    dates = source.index.get_level_values("datetime").unique()
    cutoff = dates[270]
    mutated = source.copy()
    future = mutated.index.get_level_values("datetime") > cutoff
    mutated.loc[future, ["open", "high", "low", "close", "raw_close", "dollar_volume"]] *= 3.0

    baseline = compute_price_volume_factor_frame_v1(source)
    changed = compute_price_volume_factor_frame_v1(mutated)
    visible = baseline.index.get_level_values("datetime") <= cutoff

    pd.testing.assert_frame_equal(baseline.loc[visible, list(FACTOR_IDS)], changed.loc[visible, list(FACTOR_IDS)])


def test_fifth_future_close_changes_label_but_not_current_factors() -> None:
    source = _source_frame()
    dates = source.index.get_level_values("datetime").unique()
    signal_date = dates[260]
    future_date = dates[265]
    changed_source = source.copy()
    changed_source.loc[(future_date, "AAA"), "close"] *= 1.5

    baseline = compute_price_volume_factor_frame_v1(source)
    changed = compute_price_volume_factor_frame_v1(changed_source)

    pd.testing.assert_series_equal(
        baseline.loc[(signal_date, "AAA"), list(FACTOR_IDS)],
        changed.loc[(signal_date, "AAA"), list(FACTOR_IDS)],
    )
    assert baseline.loc[(signal_date, "AAA"), "forward_return_5d"] != changed.loc[
        (signal_date, "AAA"), "forward_return_5d"
    ]


def test_ineligible_and_unlabeled_rows_are_retained() -> None:
    source = _source_frame()
    source.iloc[0:10, source.columns.get_loc("eligible")] = False

    result = compute_price_volume_factor_frame_v1(source)

    assert result.index.equals(source.index)
    assert set(result["eligible"].unique()) == {False, True}
    assert result.groupby(level="instrument")["forward_return_5d"].tail(5).isna().all()


@pytest.mark.parametrize("corruption", ["unknown", "duplicate", "unsorted", "nonfinite"])
def test_catalog_rejects_invalid_source_frame(corruption: str) -> None:
    source = _source_frame()
    if corruption == "unknown":
        source["future_secret"] = 1.0
    elif corruption == "duplicate":
        source = pd.concat([source, source.iloc[[0]]])
    elif corruption == "unsorted":
        source = source.iloc[::-1]
    else:
        source.iloc[0, source.columns.get_loc("close")] = np.inf

    with pytest.raises(ValueError):
        compute_price_volume_factor_frame_v1(source)
