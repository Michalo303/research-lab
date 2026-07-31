from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from research_lab.research.qlib_factor_screen_v1 import (
    _run_qlib_factor_screen_v1,
    run_qlib_factor_screen_v1,
)


COSTS = {"base_bps_one_way": 15.0, "stress_bps_one_way": 30.0, "severe_bps_one_way": 50.0}


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _metadata(factors: tuple[str, ...] = ("PREDICTIVE", "NOISE")) -> dict[str, object]:
    value: dict[str, object] = {
        "version": "synthetic_factor_catalog_v1",
        "factor_count": len(factors),
        "ordered_factor_ids": list(factors),
        "definitions": [
            {
                "factor_id": factor,
                "family_id": "TEST",
                "description": factor,
                "higher_is_better": True,
            }
            for factor in factors
        ],
        "label": {
            "label_id": "forward_return_5d",
            "definition": "synthetic",
            "execution_timing": "next_session_open_to_fifth_future_close",
        },
    }
    value["canonical_catalog_sha256"] = _sha(value)
    return value


def _predictive_frame(
    *,
    weeks: int = 180,
    instruments: int = 40,
    return_scale: float = 0.006,
) -> pd.DataFrame:
    dates = pd.date_range("2019-01-04", periods=weeks, freq="W-FRI")
    names = [f"S{index:03d}" for index in range(instruments)]
    records: list[dict[str, object]] = []
    midpoint = (instruments - 1) / 2.0
    for week_index, timestamp in enumerate(dates):
        for instrument_index, instrument in enumerate(names):
            rotated_rank = (instrument_index + week_index) % instruments
            predictive = (rotated_rank - midpoint) / midpoint
            records.append(
                {
                    "datetime": timestamp,
                    "instrument": instrument,
                    "PREDICTIVE": predictive,
                    "NOISE": -predictive,
                    "forward_return_5d": predictive * return_scale,
                    "eligible": True,
                }
            )
    return pd.DataFrame(records).set_index(["datetime", "instrument"]).sort_index()


def test_predictive_factor_continues_and_inverse_factor_stops() -> None:
    result = run_qlib_factor_screen_v1(
        _predictive_frame(),
        factor_metadata=_metadata(),
        costs=COSTS,
    )

    assert result["version"] == "qlib_factor_screen_v1"
    assert result["weekly_observation_count"] == 180
    assert result["factors"]["PREDICTIVE"]["decision"] == "FACTOR_CONTINUE"
    assert result["factors"]["NOISE"]["decision"] == "FACTOR_STOP"
    assert result["factors"]["PREDICTIVE"]["stress_net_spread"] > 0.0
    assert result["factors"]["PREDICTIVE"]["single_year_profit_share"] <= 0.40
    assert result["factors"]["PREDICTIVE"]["single_instrument_profit_share"] <= 0.20
    assert result["sector_concentration_evaluated"] is False
    assert len(result["canonical_screen_sha256"]) == 64


def test_screen_is_invariant_to_instrument_input_order() -> None:
    frame = _predictive_frame()

    baseline = run_qlib_factor_screen_v1(frame, factor_metadata=_metadata(), costs=COSTS)
    shuffled = run_qlib_factor_screen_v1(
        frame.sample(frac=1.0, random_state=17),
        factor_metadata=_metadata(),
        costs=COSTS,
    )

    assert baseline == shuffled


def test_screen_uses_last_eligible_session_of_each_week() -> None:
    friday = _predictive_frame(weeks=52)
    monday = friday.reset_index()
    monday["datetime"] = monday["datetime"] - pd.Timedelta(days=4)
    monday["eligible"] = False
    combined = pd.concat([friday.reset_index(), monday], ignore_index=True).set_index(
        ["datetime", "instrument"]
    )

    result = run_qlib_factor_screen_v1(combined, factor_metadata=_metadata(), costs=COSTS)

    assert result["weekly_observation_count"] == 52
    assert result["maximum_observation_date"] == friday.index.get_level_values("datetime").max().date().isoformat()


def test_factor_stops_when_stress_cost_destroys_edge() -> None:
    frame = _predictive_frame(return_scale=0.0025)
    # For a uniform [-1, 1] cross-section, this puts the top-bottom spread between
    # the base 30 bps round trip and stress 60 bps round trip.
    frame["forward_return_5d"] *= 1.0

    result = run_qlib_factor_screen_v1(frame, factor_metadata=_metadata(), costs=COSTS)

    predictive = result["factors"]["PREDICTIVE"]
    assert predictive["base_net_spread"] > 0.0
    assert predictive["stress_net_spread"] <= 0.0
    assert predictive["decision"] == "FACTOR_STOP"
    assert "EDGE_DESTROYED_BY_STRESS_COSTS" in predictive["failure_taxonomy"]


@pytest.mark.parametrize("concentration", ["year", "instrument"])
def test_factor_stops_when_profit_is_concentrated(concentration: str) -> None:
    frame = _predictive_frame()
    if concentration == "year":
        dates = frame.index.get_level_values("datetime")
        best_year = int(dates.year.min())
        frame.loc[dates.year == best_year, "forward_return_5d"] *= 20.0
    else:
        instruments = frame.index.get_level_values("instrument")
        frame.loc[instruments == "S039", "PREDICTIVE"] = 2.0
        frame.loc[instruments == "S039", "NOISE"] = -2.0
        frame.loc[instruments == "S039", "forward_return_5d"] = 0.20

    result = run_qlib_factor_screen_v1(frame, factor_metadata=_metadata(), costs=COSTS)
    predictive = result["factors"]["PREDICTIVE"]

    assert predictive["decision"] == "FACTOR_STOP"
    expected = (
        "SINGLE_YEAR_PROFIT_CONCENTRATION"
        if concentration == "year"
        else "SINGLE_INSTRUMENT_PROFIT_CONCENTRATION"
    )
    assert expected in predictive["failure_taxonomy"]


def test_screen_rejects_fewer_than_52_weekly_observations() -> None:
    with pytest.raises(ValueError, match="insufficient weekly observations"):
        run_qlib_factor_screen_v1(
            _predictive_frame(weeks=51),
            factor_metadata=_metadata(),
            costs=COSTS,
        )


def test_private_helper_allows_small_cross_sections_only_for_unit_tests() -> None:
    frame = _predictive_frame(instruments=3)

    with pytest.raises(ValueError, match="cross-section"):
        run_qlib_factor_screen_v1(frame, factor_metadata=_metadata(), costs=COSTS)
    result = _run_qlib_factor_screen_v1(
        frame,
        factor_metadata=_metadata(),
        costs=COSTS,
        minimum_cross_section_size=3,
    )
    assert result["weekly_observation_count"] == 180
