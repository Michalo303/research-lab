from __future__ import annotations

import copy
import hashlib
import json

import pytest

from research_lab.research.edge_discovery_scorecard_v1 import build_edge_discovery_scorecard_v1


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


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _factor_screen(*, continuing: int) -> dict[str, object]:
    factors = {
        factor_id: {
            "factor_id": factor_id,
            "decision": "FACTOR_CONTINUE" if index < continuing else "FACTOR_STOP",
            "failure_taxonomy": [] if index < continuing else ["WEAK_RANK_IC_AND_ECONOMIC_SPREAD"],
            "median_rank_ic": 0.02 if index < continuing else 0.0,
            "stress_net_spread": 0.001 if index < continuing else -0.001,
        }
        for index, factor_id in enumerate(FACTOR_IDS)
    }
    result: dict[str, object] = {
        "version": "qlib_factor_screen_v1",
        "status": "COMPLETED",
        "factor_catalog_sha256": "1" * 64,
        "factor_count": 8,
        "ordered_factor_ids": list(FACTOR_IDS),
        "weekly_observation_count": 104,
        "maximum_observation_date": "2022-12-30",
        "costs": {
            "base_bps_one_way": 15.0,
            "stress_bps_one_way": 30.0,
            "severe_bps_one_way": 50.0,
        },
        "factors": factors,
        "sector_concentration_evaluated": False,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
    }
    result["canonical_screen_sha256"] = _sha(result)
    return result


def _runtime(*, real: bool = True) -> dict[str, object]:
    result: dict[str, object] = {
        "version": "real_qlib_runtime_v1",
        "status": "AVAILABLE" if real else "QLIB_RUNTIME_UNAVAILABLE",
        "is_real_qlib": real,
        "qlib_version": "0.9.7" if real else "UNAVAILABLE",
        "python_version": "3.12.13",
    }
    result["runtime_sha256"] = _sha(result)
    return result


def _parity(*, passing: bool = True) -> dict[str, object]:
    result: dict[str, object] = {
        "version": "real_qlib_preparation_parity_v1",
        "status": "PASS" if passing else "FAIL",
        "segment_row_counts": {"development": 1000, "discovery": 2000},
        "source_segment_sha256": {"development": "2" * 64, "discovery": "3" * 64},
        "prepared_segment_sha256": {"development": "2" * 64, "discovery": "3" * 64},
        "source_frame_sha256": "4" * 64,
        "prepared_frame_sha256": "4" * 64,
    }
    result["canonical_parity_sha256"] = _sha(result)
    return result


def _ledger_summary(*, trials: int = 8, hypotheses: int = 8) -> dict[str, object]:
    return {
        "prior_trial_count": 3,
        "updated_trial_count": 3 + trials,
        "new_trial_count": trials,
        "new_hypothesis_count": hypotheses,
        "new_experiment_ids": [f"QLIB-PV-001-{index:02d}" for index in range(trials)],
        "new_hypothesis_ids": [f"H-QLIB-PV-001-{index:02d}" for index in range(hypotheses)],
        "new_trial_statuses_by_factor": {
            factor_id: "WALK_FORWARD_COMPLETE" if index == 0 else "STRATEGY_GATE_FAIL"
            for index, factor_id in enumerate(FACTOR_IDS[:trials])
        },
        "dataset_manifest_sha256": "5" * 64,
        "updated_ledger_sha256": "6" * 64,
        "sealed_oos_consumptions": 0,
    }


def test_edge_candidate_requires_at_least_one_continuing_factor() -> None:
    result = build_edge_discovery_scorecard_v1(
        _factor_screen(continuing=1),
        qlib_runtime_metadata=_runtime(),
        preparation_parity=_parity(),
        ledger_summary=_ledger_summary(),
    )

    assert result["status"] == "EDGE_CANDIDATE_FOUND"
    assert result["continuing_factor_ids"] == ["MOM_12_1"]
    assert result["next_authorized_milestone"] == "PORTFOLIO_CONSTRUCTION_DESIGN_V1"
    assert result["promotion_authorized"] is False
    assert result["production_runtime_supported"] is False
    assert result["sealed_oos_opened"] is False


def test_no_edge_when_every_factor_stops() -> None:
    result = build_edge_discovery_scorecard_v1(
        _factor_screen(continuing=0),
        qlib_runtime_metadata=_runtime(),
        preparation_parity=_parity(),
        ledger_summary=_ledger_summary(),
    )

    assert result["status"] == "NO_PRICE_VOLUME_EDGE"
    assert result["continuing_factor_ids"] == []
    assert result["next_authorized_milestone"] == "SHARADAR_FUNDAMENTAL_EDGE_DISCOVERY_V1"


def test_duplicate_ledger_classification_stops_an_observed_factor() -> None:
    ledger = _ledger_summary()
    ledger["new_trial_statuses_by_factor"]["MOM_12_1"] = "REJECTED_DUPLICATE"

    result = build_edge_discovery_scorecard_v1(
        _factor_screen(continuing=1),
        qlib_runtime_metadata=_runtime(),
        preparation_parity=_parity(),
        ledger_summary=ledger,
    )

    assert result["status"] == "NO_PRICE_VOLUME_EDGE"
    assert result["factor_metrics"]["MOM_12_1"]["decision"] == "FACTOR_STOP"
    assert "REJECTED_DUPLICATE" in result["factor_metrics"]["MOM_12_1"]["failure_taxonomy"]


@pytest.mark.parametrize(
    ("corruption", "value"),
    [
        ("runtime", False),
        ("parity", False),
        ("trials", 7),
        ("trials", 9),
        ("hypotheses", 7),
        ("hypotheses", 9),
    ],
)
def test_scorecard_rejects_stub_failed_parity_or_unaccounted_attempts(
    corruption: str,
    value: bool | int,
) -> None:
    runtime = _runtime(real=bool(value)) if corruption == "runtime" else _runtime()
    parity = _parity(passing=bool(value)) if corruption == "parity" else _parity()
    ledger = _ledger_summary(
        trials=int(value) if corruption == "trials" else 8,
        hypotheses=int(value) if corruption == "hypotheses" else 8,
    )

    with pytest.raises(ValueError):
        build_edge_discovery_scorecard_v1(
            _factor_screen(continuing=1),
            qlib_runtime_metadata=runtime,
            preparation_parity=parity,
            ledger_summary=ledger,
        )


def test_scorecard_rejects_duplicate_attempt_ids_or_sealed_consumption() -> None:
    duplicate = _ledger_summary()
    duplicate["new_experiment_ids"][1] = duplicate["new_experiment_ids"][0]
    sealed = _ledger_summary()
    sealed["sealed_oos_consumptions"] = 1

    for ledger in (duplicate, sealed):
        with pytest.raises(ValueError):
            build_edge_discovery_scorecard_v1(
                _factor_screen(continuing=1),
                qlib_runtime_metadata=_runtime(),
                preparation_parity=_parity(),
                ledger_summary=ledger,
            )


def test_scorecard_is_deterministic_under_factor_mapping_reordering() -> None:
    baseline = _factor_screen(continuing=2)
    reordered = copy.deepcopy(baseline)
    reordered["factors"] = dict(reversed(list(reordered["factors"].items())))
    reordered.pop("canonical_screen_sha256")
    reordered["canonical_screen_sha256"] = _sha(reordered)

    first = build_edge_discovery_scorecard_v1(
        baseline,
        qlib_runtime_metadata=_runtime(),
        preparation_parity=_parity(),
        ledger_summary=_ledger_summary(),
    )
    second = build_edge_discovery_scorecard_v1(
        reordered,
        qlib_runtime_metadata=_runtime(),
        preparation_parity=_parity(),
        ledger_summary=_ledger_summary(),
    )

    assert first == second
    assert len(first["canonical_scorecard_sha256"]) == 64
