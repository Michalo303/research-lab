from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


from research_lab.research.price_volume_factor_catalog_v1 import (  # noqa: E402
    FACTOR_DEFINITIONS_V1,
    compute_price_volume_factor_frame_v1,
)
from research_lab.research.real_qlib_runtime_v1 import (  # noqa: E402
    build_real_qlib_preparation_parity_v1,
    build_real_qlib_runtime_metadata_v1,
    prepare_real_qlib_segments_v1,
)


SEGMENTS = {
    "discovery": ("2020-01-01", "2020-12-31"),
    "development": ("2021-01-01", "2022-12-31"),
}


def _source_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", "2022-12-30")
    instruments = [f"S{index:03d}" for index in range(40)]
    index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    day = np.repeat(np.arange(len(dates), dtype="float64"), len(instruments))
    instrument = np.tile(np.arange(len(instruments), dtype="float64"), len(dates))
    close = 20.0 + day * 0.02 + instrument * 0.05 + np.sin((day + instrument) / 17.0)
    frame = pd.DataFrame(index=index)
    frame["open"] = close * (1.0 + np.cos(day / 11.0) * 0.001)
    frame["high"] = np.maximum(frame["open"], close) * 1.01
    frame["low"] = np.minimum(frame["open"], close) * 0.99
    frame["close"] = close
    frame["volume"] = 1_000_000.0 + instrument * 10_000.0 + day * 100.0
    frame["raw_close"] = close
    frame["dollar_volume"] = frame["raw_close"] * frame["volume"]
    frame["eligible"] = True
    return frame


def _weekly_momentum_stream(frame: pd.DataFrame) -> pd.Series:
    value = frame.reset_index()
    value["week"] = value["datetime"].dt.to_period("W-FRI")
    value = value.loc[value["datetime"] == value.groupby("week")["datetime"].transform("max")]
    records: dict[pd.Timestamp, float] = {}
    for timestamp, cross_section in value.groupby("datetime", sort=True):
        cross_section = cross_section.dropna(subset=["MOM_12_1", "forward_return_5d"])
        if cross_section.empty:
            continue
        ordered = cross_section.sort_values(
            ["MOM_12_1", "instrument"],
            ascending=[False, True],
            kind="mergesort",
        )
        top = ordered.head(max(1, len(ordered) // 5))
        records[pd.Timestamp(timestamp)] = float(
            top["forward_return_5d"].mean() - cross_section["forward_return_5d"].mean()
        )
    return pd.Series(records, name="MOM_12_1_top_minus_universe", dtype="float64")


def test_genuine_qlib_preparation_matches_independent_reference(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("qlib")
    factor_frame = compute_price_volume_factor_frame_v1(_source_frame())

    metadata = build_real_qlib_runtime_metadata_v1()
    segments = prepare_real_qlib_segments_v1(
        factor_frame,
        feature_columns=tuple(FACTOR_DEFINITIONS_V1),
        label_column="forward_return_5d",
        segments=SEGMENTS,
    )
    parity = build_real_qlib_preparation_parity_v1(
        factor_frame,
        segments,
        feature_columns=tuple(FACTOR_DEFINITIONS_V1),
        label_column="forward_return_5d",
        segments=SEGMENTS,
    )

    assert metadata["status"] == "AVAILABLE"
    assert metadata["is_real_qlib"] is True
    assert metadata["qlib_version"] == "0.9.7"
    assert set(segments) == {"discovery", "development"}
    assert not segments["discovery"].empty
    assert not segments["development"].empty
    assert segments["development"].index.get_level_values("datetime").max() <= pd.Timestamp("2022-12-31")
    assert parity["status"] == "PASS"

    dates = factor_frame.index.get_level_values("datetime")
    direct_development = factor_frame.loc[
        (dates >= pd.Timestamp("2021-01-01")) & (dates <= pd.Timestamp("2022-12-31")),
        [*FACTOR_DEFINITIONS_V1, "forward_return_5d"],
    ]
    pd.testing.assert_series_equal(
        _weekly_momentum_stream(direct_development),
        _weekly_momentum_stream(segments["development"]),
        check_exact=True,
    )
    captured = capsys.readouterr()
    evidence = json.dumps({"metadata": metadata, "parity": parity, "stdout": captured.out, "stderr": captured.err})
    assert "COMPLETED_LOCAL_STUB" not in evidence
