from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import (
    _build_evaluator_signal_sequence,
    _canonical_sha256,
    _macro_series_alignment_result,
    _macro_series_snapshot_adapter_result,
)
from research_lab.execution.immutable_macro_snapshot_contract_v1 import (
    build_immutable_macro_snapshot_contract,
)
from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)
from research_lab.execution.macro_aware_pilot_runner_v1 import (
    PILOT_RUN_LABEL,
    REQUEST_VERSION,
)
from research_lab.execution.macro_feature_set_contract_v1 import (
    build_macro_feature_set_contract,
)
from research_lab.execution.macro_market_asof_alignment_contract_v1 import (
    build_macro_market_asof_alignment_contract,
)
from research_lab.execution.macro_regime_filter_candidate_v1 import (
    build_macro_regime_filter_candidate,
)
from research_lab.execution.macro_series_contract_v1 import build_macro_series_contract
from research_lab.execution.macro_strategy_filter_evaluator_v1 import (
    build_macro_strategy_filter_evaluator,
)
from research_lab.execution.swing_trend_filtered_pullback_strategy_contract_v1 import (
    build_swing_trend_filtered_pullback_strategy_contract,
)


BUILDER_VERSION = "macro_aware_pilot_request_builder_v1"
EXPECTED_DATASET_ID = "eodhd-spy-us-daily-2015-2026-v1"
EXPECTED_SYMBOL = "SPY.US"
EXPECTED_SNAPSHOT_SHA256 = "cbe71c7e501407137f41d708d8fc72018c8a864b2ea4fcb0beb9c37ca8f8c00e"
EXPECTED_ROW_COUNT = 2889
EXPECTED_FIRST_TIMESTAMP = "2015-01-02T00:00:00Z"
EXPECTED_LAST_TIMESTAMP = "2026-06-30T00:00:00Z"
PRIVATE_RUN_ROOT = Path("/opt/trading/private/research_orchestrator_runs")


def prepare_controlled_synthetic_macro_pilot_request(
    *,
    market_snapshot_path: str | Path,
    request_output_path: str | Path,
    run_id: str,
    created_at: str,
) -> dict[str, Any]:
    snapshot_path = Path(market_snapshot_path).expanduser()
    if not snapshot_path.is_absolute() or not snapshot_path.is_file() or snapshot_path.is_symlink():
        raise ValueError("market_snapshot_path must be an absolute non-symlink file.")
    output_path = Path(request_output_path).expanduser()
    _validate_request_output_path(output_path)
    source_bytes_before = _file_sha256(snapshot_path)
    snapshot_payload = _load_json(snapshot_path)
    snapshot_sha256 = _canonical_sha256(snapshot_payload)
    if snapshot_sha256 != EXPECTED_SNAPSHOT_SHA256:
        raise ValueError("normalized market snapshot hash mismatch.")
    if snapshot_payload.get("dataset_id") != EXPECTED_DATASET_ID:
        raise ValueError("market snapshot dataset identity mismatch.")
    if snapshot_payload.get("symbol") != EXPECTED_SYMBOL:
        raise ValueError("market snapshot symbol identity mismatch.")

    provenance = {
        "source": BUILDER_VERSION,
        "run_label": PILOT_RUN_LABEL,
        "synthetic_macro_label": PILOT_RUN_LABEL,
        "macro_values_representation": "SYNTHETIC_NOT_REAL_HISTORY",
        "market_snapshot_sha256": snapshot_sha256,
        "market_source_file_sha256": source_bytes_before,
    }
    local_adapter = build_local_ohlcv_file_input_adapter(
        {
            "version": "local_ohlcv_file_input_adapter_request_v1",
            "file_path": str(snapshot_path),
            "format": "json",
            "dataset_id": EXPECTED_DATASET_ID,
            "symbol": EXPECTED_SYMBOL,
            "expected_sha256": source_bytes_before,
            "max_bytes": 1_000_000,
            "max_rows": 3_000,
            "provenance": provenance,
        }
    )
    _validate_local_market_adapter(local_adapter)
    downstream_bars = local_adapter["downstream_adapter_result"]["synthetic_bars"]
    snapshot_rows = snapshot_payload["rows"]
    bars = [
        {**bar, "volume": float(snapshot_rows[index]["volume"])}
        for index, bar in enumerate(downstream_bars)
    ]
    market_provenance = {
        **provenance,
        "dataset_id": EXPECTED_DATASET_ID,
        "source_file_sha256": source_bytes_before,
        "input_format": "json",
    }
    market_data_request = {
        "version": "isolated_real_data_adapter_contract_request_v1",
        "symbol": EXPECTED_SYMBOL,
        "input_bars": bars,
        "provenance": market_provenance,
    }
    market_adapter = build_isolated_real_data_adapter_contract(market_data_request)
    if market_adapter != local_adapter["downstream_adapter_result"]:
        raise ValueError("local market adapter lineage mismatch.")

    acceptance_request = _build_acceptance_request(
        bars=bars,
        market_data_request=market_data_request,
        market_adapter=market_adapter,
        provenance=provenance,
    )
    pilot_request = {
        "version": REQUEST_VERSION,
        "run_id": _required_text(run_id, name="run_id"),
        "run_label": PILOT_RUN_LABEL,
        "acceptance_request": acceptance_request,
        "expected_acceptance_request_sha256": _canonical_sha256(acceptance_request),
        "expected_market_dataset_identity": EXPECTED_DATASET_ID,
        "expected_market_symbol": EXPECTED_SYMBOL,
        "expected_market_bars_sha256": acceptance_request["expected_hashes"]["market_data_sha256"],
        "expected_market_source_artifact_sha256": market_adapter["output_payload_sha256"],
        "expected_synthetic_macro_label": PILOT_RUN_LABEL,
        "created_at": _required_text(created_at, name="created_at"),
        "provenance": provenance,
    }
    _write_verified_json(output_path, pilot_request)
    if _file_sha256(snapshot_path) != source_bytes_before:
        raise ValueError("market snapshot changed during pilot request preparation.")
    return {
        "version": BUILDER_VERSION,
        "request_status": "PREPARED",
        "run_id": pilot_request["run_id"],
        "run_label": PILOT_RUN_LABEL,
        "request_output_path": str(output_path),
        "request_sha256": _canonical_sha256(pilot_request),
        "acceptance_request_sha256": pilot_request["expected_acceptance_request_sha256"],
        "market_snapshot_sha256": snapshot_sha256,
        "market_source_file_sha256": source_bytes_before,
        "market_bars_sha256": pilot_request["expected_market_bars_sha256"],
        "market_source_artifact_sha256": pilot_request[
            "expected_market_source_artifact_sha256"
        ],
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
    }


def _build_acceptance_request(
    *,
    bars: list[dict[str, Any]],
    market_data_request: dict[str, Any],
    market_adapter: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    first_timestamp = str(bars[0]["timestamp"])
    last_timestamp = str(bars[-1]["timestamp"])
    midpoint = len(bars) // 2
    second_fold_start = str(bars[midpoint]["timestamp"])
    first_fold_end = str(bars[midpoint - 1]["timestamp"])
    macro_series_requests = _synthetic_macro_series_requests(
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        provenance=provenance,
    )
    macro_snapshot_request = {
        "version": "immutable_macro_snapshot_contract_request_v1",
        "snapshot_id": "synthetic-macro-integration-pilot-snapshot-v1",
        "snapshot_date": last_timestamp[:10],
    }
    macro_alignment_request = {
        "version": "macro_market_asof_alignment_contract_request_v1",
        "market_timezone": "America/New_York",
        "decision_timestamp_convention": "LOCAL_TIME_ON_BAR_DATE",
        "decision_time_local": "09:30:00",
        "macro_availability_convention": "AT_START_OF_DAY",
        "minimum_release_lag_minutes": 0,
        "maximum_staleness_days": 40,
        "missing_data_policy": "MARK_MISSING",
        "unsafe_series_policy": "REJECT",
    }
    macro_feature_request = {
        "version": "macro_feature_set_contract_request_v1",
        "feature_definitions": [
            {
                "feature_id": "growth_z",
                "operation": "level",
                "source_series_id": "SYNTHETIC:GROWTH",
                "minimum_observations": 1,
            },
            {
                "feature_id": "inflation_state",
                "operation": "bounded_categorical_state",
                "source_series_id": "SYNTHETIC:INFLATION_STATE",
                "bounds": [0.5, 1.5],
                "labels": ["LOW", "MID", "HIGH"],
                "minimum_observations": 1,
            },
        ],
        "missing_data_policy": "MARK_MISSING",
        "clipping_policy": {"mode": "NONE"},
    }
    macro_regime_request = {
        "version": "macro_regime_filter_candidate_request_v1",
        "candidate_id": "synthetic-macro-integration-pilot-regime-v1",
        "mode": "deterministic_rules",
        "state_policy": {
            "allowed_regime_labels": [
                "RISK_SUPPORTIVE",
                "NEUTRAL",
                "RISK_RESTRICTIVE",
                "INSUFFICIENT_EVIDENCE",
            ],
            "label_policies": {
                "RISK_SUPPORTIVE": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {
                            "feature_id": "growth_z",
                            "operation": "greater_than",
                            "threshold": 0.5,
                            "weight": 1.0,
                        },
                        {
                            "feature_id": "inflation_state",
                            "operation": "categorical_equals",
                            "value": "LOW",
                            "weight": 0.6,
                        },
                    ],
                },
                "NEUTRAL": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {
                            "feature_id": "growth_z",
                            "operation": "between_inclusive",
                            "lower": -0.25,
                            "upper": 0.25,
                            "weight": 1.0,
                        },
                        {
                            "feature_id": "inflation_state",
                            "operation": "categorical_equals",
                            "value": "MID",
                            "weight": 0.5,
                        },
                    ],
                },
                "RISK_RESTRICTIVE": {
                    "minimum_score": 1.0,
                    "minimum_supporting_rules": 1,
                    "rules": [
                        {
                            "feature_id": "growth_z",
                            "operation": "less_than",
                            "threshold": -0.5,
                            "weight": 1.0,
                        },
                        {
                            "feature_id": "inflation_state",
                            "operation": "categorical_equals",
                            "value": "HIGH",
                            "weight": 0.6,
                        },
                    ],
                },
            },
        },
        "minimum_supporting_features": 1,
        "minimum_available_features": 1,
        "transition_policy": {"count_label_changes": True},
        "confidence_policy": {"max_feature_age_days": 40},
    }
    strategy_request = {
        "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
        "symbol": market_adapter["symbol"],
        "strategy_parameters": {
            "fast_sma": 2,
            "slow_sma": 3,
            "rsi_entry": 80.0,
            "rsi_exit": 85.0,
            "atr_stop": 2.0,
            "max_exposure": 1.0,
        },
    }
    evaluation_request = {
        "version": "macro_strategy_filter_evaluator_request_v1",
        "evaluation_id": "synthetic-macro-integration-pilot-evaluation-v1",
        "strategy_identity": {
            "strategy_id": "STFP_SYNTHETIC_MACRO_PILOT",
            "strategy_version": "swing_trend_filtered_pullback_strategy_contract_v1",
            "strategy_builder": "swing_trend_filtered_pullback",
            "symbol": market_adapter["symbol"],
            "allows_short": False,
        },
        "baseline_variant_identity": "BASELINE_UNCHANGED",
        "market_data_identity": EXPECTED_DATASET_ID,
        "filter_policy": {
            "regime_action_map": {
                "RISK_SUPPORTIVE": {"action": "ALLOW_ENTRY"},
                "NEUTRAL": {"action": "REDUCE_EXPOSURE", "factor": 0.5},
                "RISK_RESTRICTIVE": {"action": "BLOCK_ENTRY"},
                "INSUFFICIENT_EVIDENCE": {"action": "LEAVE_UNCHANGED"},
            }
        },
        "ablation_policy": {
            "enable_inverse_filter": True,
            "inverse_regime_action_map": {
                "RISK_SUPPORTIVE": {"action": "BLOCK_ENTRY"},
                "NEUTRAL": {"action": "REDUCE_EXPOSURE", "factor": 1.0},
                "RISK_RESTRICTIVE": {"action": "ALLOW_ENTRY"},
                "INSUFFICIENT_EVIDENCE": {"action": "LEAVE_UNCHANGED"},
            },
        },
        "evaluation_windows": [
            {
                "window_id": "full",
                "start_timestamp": first_timestamp,
                "end_timestamp": last_timestamp,
            }
        ],
        "chronological_folds": [
            {
                "fold_id": "fold-1",
                "start_timestamp": first_timestamp,
                "end_timestamp": first_fold_end,
                "min_total_return": -1.0,
                "max_drawdown_limit": 1.0,
                "min_trade_count": 0,
            },
            {
                "fold_id": "fold-2",
                "start_timestamp": second_fold_start,
                "end_timestamp": last_timestamp,
                "min_total_return": -1.0,
                "max_drawdown_limit": 1.0,
                "min_trade_count": 0,
            },
        ],
        "transaction_cost_assumptions": {"per_unit_turnover_cost": 0.001},
        "slippage_assumptions": {"per_unit_turnover_slippage": 0.001},
        "execution_policy": {
            "initial_capital": 100000.0,
            "fill_convention": "next_open",
            "decision_to_fill_delay_bars": 1,
            "allow_same_bar_fill": False,
        },
        "classification_policy": {
            "risk": {"min_drawdown_improvement": 0.02, "max_return_degradation": 0.02},
            "return": {"min_return_improvement": 0.02, "max_drawdown_degradation": 0.02},
            "mixed": {"min_drawdown_improvement": 0.02, "min_return_improvement": 0.02},
            "no_value": {"max_abs_return_delta": 0.000001, "max_abs_drawdown_delta": 0.000001},
            "unstable": {"min_fold_pass_rate": 0.5},
        },
        "minimum_evidence_policy": {
            "min_candidate_trade_count": 0,
            "min_fold_pass_rate": 0.0,
            "min_regime_observations": 0,
        },
    }
    request = {
        "version": "e2e_macro_aware_research_acceptance_request_v1",
        "acceptance_id": "synthetic-macro-integration-pilot-acceptance-v1",
        "market_data_request": market_data_request,
        "macro_series_requests": macro_series_requests,
        "macro_snapshot_request": macro_snapshot_request,
        "macro_alignment_request": macro_alignment_request,
        "macro_feature_request": macro_feature_request,
        "macro_regime_request": macro_regime_request,
        "strategy_request": strategy_request,
        "macro_filter_evaluation_request": evaluation_request,
        "expected_identities": {
            "market_data_identity": EXPECTED_DATASET_ID,
            "market_symbol": market_adapter["symbol"],
            "strategy_id": evaluation_request["strategy_identity"]["strategy_id"],
            "strategy_builder": evaluation_request["strategy_identity"]["strategy_builder"],
            "baseline_variant_identity": evaluation_request["baseline_variant_identity"],
        },
        "expected_hashes": {},
        "provenance": provenance,
    }
    request["expected_hashes"] = _expected_hashes(request, market_adapter=market_adapter)
    return request


def _expected_hashes(request: dict[str, Any], *, market_adapter: dict[str, Any]) -> dict[str, str]:
    series_results = [build_macro_series_contract(item) for item in request["macro_series_requests"]]
    snapshot_result = build_immutable_macro_snapshot_contract(
        {
            **request["macro_snapshot_request"],
            "series_adapter_results": [
                _macro_series_snapshot_adapter_result(item) for item in series_results
            ],
            "provenance": request["provenance"],
        }
    )
    alignment_result = build_macro_market_asof_alignment_contract(
        {
            **request["macro_alignment_request"],
            "market_bars": request["market_data_request"]["input_bars"],
            "macro_series_results": [
                _macro_series_alignment_result(item) for item in series_results
            ],
            "provenance": request["provenance"],
        }
    )
    feature_result = build_macro_feature_set_contract(
        {
            **request["macro_feature_request"],
            "aligned_macro_result": alignment_result,
            "provenance": request["provenance"],
        }
    )
    regime_result = build_macro_regime_filter_candidate(
        {
            **request["macro_regime_request"],
            "macro_feature_set": feature_result,
            "provenance": request["provenance"],
        }
    )
    strategy_result = build_swing_trend_filtered_pullback_strategy_contract(
        {
            **request["strategy_request"],
            "synthetic_bars": market_adapter["synthetic_bars"],
            "provenance": request["provenance"],
        }
    )
    evaluation_request = request["macro_filter_evaluation_request"]
    evaluator_result = build_macro_strategy_filter_evaluator(
        {
            **evaluation_request,
            "baseline_signal_sequence": _build_evaluator_signal_sequence(
                strategy_contract_result=strategy_result,
                strategy_identity=evaluation_request["strategy_identity"],
                baseline_variant_identity=evaluation_request["baseline_variant_identity"],
                market_data_identity=evaluation_request["market_data_identity"],
                market_symbol=market_adapter["symbol"],
            ),
            "market_bars": market_adapter["synthetic_bars"],
            "market_data_sha256": _canonical_sha256(market_adapter["synthetic_bars"]),
            "market_source_artifact_sha256": market_adapter["output_payload_sha256"],
            "macro_snapshot_sha256": snapshot_result["output_payload_sha256"],
            "alignment_output_sha256": alignment_result["output_payload_sha256"],
            "feature_set_output_sha256": feature_result["output_payload_sha256"],
            "macro_regime_candidate_output_sha256": regime_result["output_payload_sha256"],
            "macro_regime_candidate_result": regime_result,
            "provenance": request["provenance"],
        }
    )
    return {
        "market_data_sha256": _canonical_sha256(market_adapter["synthetic_bars"]),
        "macro_snapshot_sha256": snapshot_result["output_payload_sha256"],
        "alignment_output_sha256": alignment_result["output_payload_sha256"],
        "feature_set_output_sha256": feature_result["output_payload_sha256"],
        "macro_regime_candidate_output_sha256": regime_result["output_payload_sha256"],
        "evaluator_output_sha256": evaluator_result["output_payload_sha256"],
    }


def _synthetic_macro_series_requests(
    *,
    first_timestamp: str,
    last_timestamp: str,
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    first_date = date.fromisoformat(first_timestamp[:10])
    last_date = date.fromisoformat(last_timestamp[:10])
    start_year, start_month = _previous_month(first_date.year, first_date.month)
    months = list(_month_range(start_year, start_month, last_date.year, last_date.month))
    growth_values = (1.0, 0.0, -1.0, 0.0)
    inflation_values = (0.0, 1.0, 2.0, 1.0)

    def observations(values: tuple[float, ...]) -> list[dict[str, Any]]:
        rows = []
        for index, (year, month) in enumerate(months):
            available_day = 15
            available_date = date(year, month, available_day)
            if available_date > last_date:
                continue
            rows.append(
                {
                    "observation_date": f"{year:04d}-{month:02d}-01",
                    "value": values[index % len(values)],
                    "point_in_time": {
                        "classification": "exact_release_timestamp",
                        "available_date": available_date.isoformat(),
                        "available_timestamp_utc": (
                            f"{year:04d}-{month:02d}-{available_day:02d}T12:00:00Z"
                        ),
                    },
                }
            )
        return rows

    return [
        {
            "version": "macro_series_contract_request_v1",
            "provider": "SYNTHETIC",
            "series_id": "GROWTH",
            "frequency": "monthly",
            "units": "synthetic_index",
            "observations": observations(growth_values),
            "provenance": provenance,
        },
        {
            "version": "macro_series_contract_request_v1",
            "provider": "SYNTHETIC",
            "series_id": "INFLATION_STATE",
            "frequency": "monthly",
            "units": "synthetic_bucket",
            "observations": observations(inflation_values),
            "provenance": provenance,
        },
    ]


def _validate_local_market_adapter(result: dict[str, Any]) -> None:
    if result.get("status") != "SUCCESS":
        raise ValueError("local market adapter did not complete successfully.")
    if result.get("dataset_id") != EXPECTED_DATASET_ID or result.get("symbol") != EXPECTED_SYMBOL:
        raise ValueError("local market adapter identity mismatch.")
    if result.get("row_count") != EXPECTED_ROW_COUNT:
        raise ValueError("local market adapter row count mismatch.")
    if result.get("first_timestamp") != EXPECTED_FIRST_TIMESTAMP:
        raise ValueError("local market adapter first timestamp mismatch.")
    if result.get("last_timestamp") != EXPECTED_LAST_TIMESTAMP:
        raise ValueError("local market adapter last timestamp mismatch.")
    safety = {
        "source_modified": False,
        "network_used": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
    }
    for field, expected in safety.items():
        if result.get(field) != expected:
            raise ValueError(f"local market adapter safety violation: {field}")


def _validate_request_output_path(path: Path) -> None:
    if any(part == ".." for part in path.parts):
        raise ValueError("unsafe request output path traversal.")
    allowed_root = PRIVATE_RUN_ROOT.expanduser().resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        relative = resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("request output must be inside the private research run root.") from exc
    if not relative.parts or not path.parent.is_dir():
        raise ValueError("request output parent must be an existing private run directory.")
    if path.exists() or path.is_symlink():
        raise ValueError("request output must be fresh; overwrite is forbidden.")


def _write_verified_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    if _load_json(path) != payload:
        raise OSError("pilot request post-write verification failed.")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_range(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
):
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    return value.strip()
