from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.research.eodhd_qlib_dataset_v1 import load_eodhd_qlib_development_frame_v1
from research_lab.research.fundamental_portfolio_screen_v1 import run_fundamental_portfolio_screen_v1
from research_lab.research.global_experiment_ledger_v1 import (
    apply_global_experiment_ledger_operation_v1,
    validate_global_experiment_ledger_v1,
)
from research_lab.research.massive_fundamental_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_massive_fundamental_catalog_metadata_v1,
)
from research_lab.research.massive_fundamental_dataset_v1 import (
    build_point_in_time_fundamental_factor_panel_v1,
    load_massive_fundamental_histories_v1,
)


REQUEST_VERSION = "massive_fundamental_edge_discovery_request_v1"
PILOT_VERSION = "massive_fundamental_edge_discovery_v1"
STRATEGY_FAMILY_ID = "FUNDAMENTAL_FACTOR"
LEDGER_BINDING_FAILED = "LEDGER_BINDING_FAILED"
SEC_AUDIT_BINDING_FAILED = "SEC_AUDIT_BINDING_FAILED"
FUNDAMENTAL_COVERAGE_INSUFFICIENT = "FUNDAMENTAL_COVERAGE_INSUFFICIENT"
FUNDAMENTAL_EDGE_CANDIDATE_FOUND = "FUNDAMENTAL_EDGE_CANDIDATE_FOUND"
NO_FUNDAMENTAL_EDGE = "NO_FUNDAMENTAL_EDGE"
_REQUEST_FIELDS = {
    "version",
    "pilot_id",
    "eodhd_bundle_root",
    "eodhd_manifest_path",
    "expected_eodhd_manifest_sha256",
    "expected_spy_raw_sha256",
    "fundamental_bundle_root",
    "expected_fundamental_manifest_sha256",
    "expected_fundamental_canonical_manifest_sha256",
    "sec_audit_path",
    "expected_sec_audit_sha256",
    "expected_sec_audit_canonical_sha256",
    "previous_ledger_path",
    "expected_previous_ledger_sha256",
    "output_dir",
    "discovery_interval",
    "development_interval",
    "sealed_oos_interval",
    "universe",
    "costs",
    "provenance",
}
_EXPECTED_UNIVERSE = {
    "minimum_price": 5.0,
    "minimum_history_sessions": 252,
    "minimum_median_dollar_volume": 10_000_000.0,
    "maximum_instruments": 1500,
}
_EXPECTED_COSTS = {
    "base_bps_one_way": 15.0,
    "stress_bps_one_way": 30.0,
    "severe_bps_one_way": 50.0,
}
_SAFETY = {
    "provider_calls_used": 0,
    "broker_calls_used": 0,
    "registry_write_performed": False,
    "deployment_performed": False,
    "knihomol_used": False,
    "rd_agent_used": False,
    "sealed_oos_opened": False,
}


def build_massive_fundamental_edge_discovery_plan_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Preflight exact inputs and ledger capacity without reading economic data."""

    validated = _validate_request(request)
    previous_ledger = _read_and_preflight_ledger(validated)
    sec_audit = _read_sec_audit(validated)
    result: dict[str, object] = {
        "version": "massive_fundamental_edge_discovery_plan_v1",
        "pilot_id": validated["pilot_id"],
        "request_sha256": _canonical_sha(validated),
        "eodhd_manifest_sha256": validated["expected_eodhd_manifest_sha256"],
        "fundamental_manifest_sha256": validated["expected_fundamental_canonical_manifest_sha256"],
        "sec_audit_sha256": sec_audit["canonical_audit_sha256"],
        "sec_audit_status": sec_audit["status"],
        "previous_ledger_sha256": previous_ledger["canonical_ledger_sha256"],
        "planned_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "planned_hypothesis_count": 10,
        "planned_trial_count": 10,
        "development_interval": copy.deepcopy(validated["development_interval"]),
        "sealed_oos_interval": copy.deepcopy(validated["sealed_oos_interval"]),
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_plan_sha256"] = _canonical_sha(result)
    return result


def run_massive_fundamental_edge_discovery_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Run one offline ten-trial fundamental program without writing or opening sealed OOS."""

    validated = _validate_request(request)
    previous_ledger = _read_and_preflight_ledger(validated)
    sec_audit = _read_sec_audit(validated)
    loader_request = {
        "version": "eodhd_qlib_development_frame_request_v1",
        "manifest_path": validated["eodhd_manifest_path"],
        "expected_manifest_sha256": validated["expected_eodhd_manifest_sha256"],
        "discovery_interval": copy.deepcopy(validated["discovery_interval"]),
        "development_interval": copy.deepcopy(validated["development_interval"]),
        "sealed_oos_interval": copy.deepcopy(validated["sealed_oos_interval"]),
        "universe": copy.deepcopy(validated["universe"]),
        "provenance": copy.deepcopy(validated["provenance"]),
    }
    price_frame, price_metadata = load_eodhd_qlib_development_frame_v1(loader_request)
    if (
        price_metadata.get("dataset_manifest_sha256") != validated["expected_eodhd_manifest_sha256"]
        or price_metadata.get("provider_calls_used") != 0
        or price_metadata.get("sealed_oos_rows_read") != 0
    ):
        raise ValueError("EODHD_DATASET_VALIDATION_FAILED")
    histories, history_metadata = load_massive_fundamental_histories_v1(
        validated["fundamental_bundle_root"],
        validated["expected_fundamental_manifest_sha256"],
    )
    if (
        history_metadata.get("fundamental_canonical_manifest_sha256")
        != validated["expected_fundamental_canonical_manifest_sha256"]
        or history_metadata.get("provider_calls_used") != 0
        or history_metadata.get("sealed_oos_rows_read") != 0
    ):
        raise ValueError("FUNDAMENTAL_DATASET_VALIDATION_FAILED")
    panel, panel_metadata = build_point_in_time_fundamental_factor_panel_v1(
        histories,
        price_frame,
        development_start=validated["development_interval"]["start"],
        development_end=validated["development_interval"]["end"],
    )
    if panel_metadata.get("provider_calls_used") != 0 or panel_metadata.get("sealed_oos_rows_read") != 0:
        raise ValueError("FUNDAMENTAL_PANEL_VALIDATION_FAILED")
    gross_coverage = panel_metadata.get("coverage_by_factor", {}).get("GROSS_PROFITABILITY", {})
    if float(gross_coverage.get("median", 0.0)) < 500.0:
        return _coverage_failure_result(
            validated=validated,
            previous_ledger=previous_ledger,
            price_metadata=price_metadata,
            history_metadata=history_metadata,
            panel_metadata=panel_metadata,
            sec_audit=sec_audit,
        )
    spy_prices = _load_spy_prices(
        Path(validated["eodhd_bundle_root"]),
        validated["expected_spy_raw_sha256"],
        development_end=validated["development_interval"]["end"],
    )
    catalog = build_massive_fundamental_catalog_metadata_v1()
    screen = run_fundamental_portfolio_screen_v1(panel, price_frame, spy_prices, catalog)
    if (
        screen.get("status") != "COMPLETED"
        or screen.get("factor_catalog_sha256") != catalog["canonical_catalog_sha256"]
        or screen.get("promotion_authorized") is not False
        or screen.get("sealed_oos_opened") is not False
    ):
        raise ValueError("FUNDAMENTAL_SCREEN_VALIDATION_FAILED")

    updated_ledger = copy.deepcopy(previous_ledger)
    hypothesis_ids: list[str] = []
    experiment_ids: list[str] = []
    accounted_factor_ids: list[str] = []
    for factor_id in FACTOR_DEFINITIONS_V1:
        hypothesis_id = f"H-{validated['pilot_id']}-{factor_id}"
        experiment_id = f"{validated['pilot_id']}-{factor_id}"
        semantic_fingerprint = _canonical_sha(
            {
                "version": PILOT_VERSION,
                "factor_id": factor_id,
                "definition": FACTOR_DEFINITIONS_V1[factor_id],
                "universe": validated["universe"],
                "portfolio": "TOP_15_EQUAL_WEIGHT_WEEKLY_NEXT_OPEN",
            }
        )
        hypothesis = _build_hypothesis(
            hypothesis_id=hypothesis_id,
            factor_id=factor_id,
            semantic_fingerprint=semantic_fingerprint,
            request=validated,
            catalog_sha256=str(catalog["canonical_catalog_sha256"]),
            screen_sha256=str(screen["canonical_screen_sha256"]),
        )
        try:
            updated_ledger = _apply_operation(
                updated_ledger,
                {
                    "operation_id": f"OP-{validated['pilot_id']}-REGISTER-{factor_id}",
                    "kind": "REGISTER_HYPOTHESIS",
                    "hypothesis": hypothesis,
                },
            )
            hypothesis_ids.append(hypothesis_id)
            factor_result = screen["factors"][factor_id]
            trial = _build_trial(
                experiment_id=experiment_id,
                hypothesis_id=hypothesis_id,
                factor_id=factor_id,
                semantic_fingerprint=semantic_fingerprint,
                factor_result=factor_result,
                request=validated,
                catalog_sha256=str(catalog["canonical_catalog_sha256"]),
                screen_sha256=str(screen["canonical_screen_sha256"]),
            )
            updated_ledger = _apply_operation(
                updated_ledger,
                {
                    "operation_id": f"OP-{validated['pilot_id']}-APPEND-{factor_id}",
                    "kind": "APPEND_TRIAL",
                    "trial": trial,
                },
            )
            experiment_ids.append(experiment_id)
            accounted_factor_ids.append(factor_id)
        except ValueError:
            return _accounting_failure_result(
                validated=validated,
                previous_ledger=previous_ledger,
                updated_ledger=updated_ledger,
                price_metadata=price_metadata,
                history_metadata=history_metadata,
                panel_metadata=panel_metadata,
                screen=screen,
                hypothesis_ids=hypothesis_ids,
                experiment_ids=experiment_ids,
                accounted_factor_ids=accounted_factor_ids,
                sec_audit=sec_audit,
            )

    trial_lookup = {trial["experiment_id"]: trial for trial in updated_ledger["trials"]}
    hypothesis_lookup = {item["hypothesis_id"]: item for item in updated_ledger["hypotheses"]}
    new_trials = [copy.deepcopy(trial_lookup[experiment_id]) for experiment_id in experiment_ids]
    new_hypotheses = [copy.deepcopy(hypothesis_lookup[hypothesis_id]) for hypothesis_id in hypothesis_ids]
    continuing = list(screen["continuing_factor_ids"])
    status = FUNDAMENTAL_EDGE_CANDIDATE_FOUND if continuing else NO_FUNDAMENTAL_EDGE
    scorecard = _build_scorecard(
        status=status,
        request=validated,
        screen=screen,
        updated_ledger=updated_ledger,
        experiment_ids=experiment_ids,
        hypothesis_ids=hypothesis_ids,
        sec_audit=sec_audit,
    )
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": validated["pilot_id"],
        "status": status,
        "price_metadata": copy.deepcopy(price_metadata),
        "fundamental_history_metadata": copy.deepcopy(history_metadata),
        "fundamental_panel_metadata": copy.deepcopy(panel_metadata),
        "sec_filing_audit": copy.deepcopy(sec_audit),
        "fundamental_screen": copy.deepcopy(screen),
        "economic_scorecard": scorecard,
        "updated_ledger": updated_ledger,
        "new_hypotheses": new_hypotheses,
        "new_trials": new_trials,
        "new_hypothesis_count": len(new_hypotheses),
        "new_trial_count": len(new_trials),
        "attempted_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "accounted_factor_ids": accounted_factor_ids,
        "accounting_complete": True,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha(result)
    return result


def _validate_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS:
        raise ValueError("request fields are invalid.")
    if raw.get("version") != REQUEST_VERSION:
        raise ValueError("request version is invalid.")
    paths: dict[str, str] = {}
    for field in (
        "eodhd_bundle_root",
        "eodhd_manifest_path",
        "fundamental_bundle_root",
        "sec_audit_path",
        "previous_ledger_path",
        "output_dir",
    ):
        path = Path(_text(raw.get(field), field))
        if not path.is_absolute():
            raise ValueError(f"{field} must be absolute.")
        paths[field] = str(path)
    eodhd_root = Path(paths["eodhd_bundle_root"])
    fundamental_root = Path(paths["fundamental_bundle_root"])
    eodhd_manifest = Path(paths["eodhd_manifest_path"])
    ledger_path = Path(paths["previous_ledger_path"])
    sec_audit_path = Path(paths["sec_audit_path"])
    output = Path(paths["output_dir"])
    if not eodhd_root.is_dir() or not fundamental_root.is_dir():
        raise ValueError("bundle roots are invalid.")
    if eodhd_manifest.is_symlink() or not eodhd_manifest.is_file():
        raise ValueError("EODHD manifest path is invalid.")
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ValueError(LEDGER_BINDING_FAILED)
    if sec_audit_path.is_symlink() or not sec_audit_path.is_file():
        raise ValueError(SEC_AUDIT_BINDING_FAILED)
    if output.exists() or output.is_symlink():
        raise ValueError("output_dir must not exist.")
    eodhd_sha = _sha_field(raw.get("expected_eodhd_manifest_sha256"), "expected_eodhd_manifest_sha256")
    if _file_sha(eodhd_manifest) != eodhd_sha:
        raise ValueError("EODHD manifest hash mismatch.")
    fundamental_sha = _sha_field(raw.get("expected_fundamental_manifest_sha256"), "expected_fundamental_manifest_sha256")
    fundamental_manifest = fundamental_root / "fundamental_manifest.json"
    if fundamental_manifest.is_symlink() or not fundamental_manifest.is_file() or _file_sha(fundamental_manifest) != fundamental_sha:
        raise ValueError("fundamental manifest hash mismatch.")
    previous_sha = _sha_field(raw.get("expected_previous_ledger_sha256"), "expected_previous_ledger_sha256")
    sec_audit_sha = _sha_field(raw.get("expected_sec_audit_sha256"), "expected_sec_audit_sha256")
    if _file_sha(sec_audit_path) != sec_audit_sha:
        raise ValueError(SEC_AUDIT_BINDING_FAILED)
    discovery = _interval(raw.get("discovery_interval"), "discovery_interval")
    development = _interval(raw.get("development_interval"), "development_interval")
    sealed = _sealed_interval(raw.get("sealed_oos_interval"))
    if pd.Timestamp(discovery["end"]) >= pd.Timestamp(development["start"]) or pd.Timestamp(development["end"]) >= pd.Timestamp(sealed["start"]):
        raise ValueError("intervals overlap or are out of order.")
    if development != {"start": "2019-01-01", "end": "2022-12-31"} or sealed["start"] != "2023-01-01":
        raise ValueError("frozen development or sealed interval is invalid.")
    if raw.get("universe") != _EXPECTED_UNIVERSE or raw.get("costs") != _EXPECTED_COSTS:
        raise ValueError("frozen policy inputs are invalid.")
    if raw.get("provenance") != {"source": "operator_approved_local_snapshot"}:
        raise ValueError("provenance is invalid.")
    return {
        "version": REQUEST_VERSION,
        "pilot_id": _text(raw.get("pilot_id"), "pilot_id"),
        **paths,
        "expected_eodhd_manifest_sha256": eodhd_sha,
        "expected_spy_raw_sha256": _sha_field(raw.get("expected_spy_raw_sha256"), "expected_spy_raw_sha256"),
        "expected_fundamental_manifest_sha256": fundamental_sha,
        "expected_fundamental_canonical_manifest_sha256": _sha_field(
            raw.get("expected_fundamental_canonical_manifest_sha256"),
            "expected_fundamental_canonical_manifest_sha256",
        ),
        "expected_sec_audit_sha256": sec_audit_sha,
        "expected_sec_audit_canonical_sha256": _sha_field(
            raw.get("expected_sec_audit_canonical_sha256"),
            "expected_sec_audit_canonical_sha256",
        ),
        "expected_previous_ledger_sha256": previous_sha,
        "discovery_interval": discovery,
        "development_interval": development,
        "sealed_oos_interval": sealed,
        "universe": copy.deepcopy(_EXPECTED_UNIVERSE),
        "costs": copy.deepcopy(_EXPECTED_COSTS),
        "provenance": {"source": "operator_approved_local_snapshot"},
    }


def _read_sec_audit(request: dict[str, Any]) -> dict[str, object]:
    try:
        raw = json.loads(Path(request["sec_audit_path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(SEC_AUDIT_BINDING_FAILED) from exc
    if not isinstance(raw, dict):
        raise ValueError(SEC_AUDIT_BINDING_FAILED)
    declared = raw.get("canonical_audit_sha256")
    expected = request["expected_sec_audit_canonical_sha256"]
    records = raw.get("records")
    if (
        declared != expected
        or _canonical_sha({key: value for key, value in raw.items() if key != "canonical_audit_sha256"}) != expected
        or raw.get("version") != "massive_sec_filing_audit_v1"
        or raw.get("status") != "PASS"
        or raw.get("sample_size") != 30
        or raw.get("minimum_matched_fields_per_record") != 3
        or raw.get("passed_record_count") != 30
        or raw.get("failed_record_count") != 0
        or not isinstance(records, list)
        or len(records) != 30
        or raw.get("fundamental_manifest_sha256") != request["expected_fundamental_manifest_sha256"]
        or raw.get("fundamental_canonical_manifest_sha256")
        != request["expected_fundamental_canonical_manifest_sha256"]
        or raw.get("sealed_oos_opened") is not False
    ):
        raise ValueError(SEC_AUDIT_BINDING_FAILED)
    audit_root = Path(request["sec_audit_path"]).parent.resolve()
    seen_ciks: set[str] = set()
    seen_accessions: set[str] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("status") != "PASS"
            or not isinstance(record.get("matched_field_count"), int)
            or int(record["matched_field_count"]) < 3
        ):
            raise ValueError(SEC_AUDIT_BINDING_FAILED)
        cik = str(record.get("cik", ""))
        accession = str(record.get("accession", ""))
        if cik in seen_ciks or accession in seen_accessions:
            raise ValueError(SEC_AUDIT_BINDING_FAILED)
        seen_ciks.add(cik)
        seen_accessions.add(accession)
        relative = Path(str(record.get("raw_response_path", "")))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(SEC_AUDIT_BINDING_FAILED)
        raw_path = audit_root / relative
        try:
            resolved = raw_path.resolve()
            resolved.relative_to(audit_root)
        except (OSError, ValueError) as exc:
            raise ValueError(SEC_AUDIT_BINDING_FAILED) from exc
        if raw_path.is_symlink() or not resolved.is_file():
            raise ValueError(SEC_AUDIT_BINDING_FAILED)
        try:
            expected_raw_sha = _sha_field(record.get("raw_response_sha256"), "raw_response_sha256")
        except ValueError as exc:
            raise ValueError(SEC_AUDIT_BINDING_FAILED) from exc
        if _file_sha(resolved) != expected_raw_sha:
            raise ValueError(SEC_AUDIT_BINDING_FAILED)
    return {
        "version": str(raw["version"]),
        "status": str(raw["status"]),
        "audit_id": str(raw.get("audit_id", "")),
        "sample_size": int(raw["sample_size"]),
        "passed_record_count": int(raw["passed_record_count"]),
        "fundamental_manifest_sha256": str(raw["fundamental_manifest_sha256"]),
        "fundamental_canonical_manifest_sha256": str(raw["fundamental_canonical_manifest_sha256"]),
        "audit_file_sha256": request["expected_sec_audit_sha256"],
        "canonical_audit_sha256": str(declared),
        "sealed_oos_opened": False,
    }


def _read_and_preflight_ledger(request: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(Path(request["previous_ledger_path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(LEDGER_BINDING_FAILED) from exc
    if not isinstance(raw, dict):
        raise ValueError(LEDGER_BINDING_FAILED)
    expected = request["expected_previous_ledger_sha256"]
    if raw.get("canonical_ledger_sha256") != expected or _canonical_sha(
        {key: value for key, value in raw.items() if key != "canonical_ledger_sha256"}
    ) != expected:
        raise ValueError(LEDGER_BINDING_FAILED)
    try:
        ledger = validate_global_experiment_ledger_v1(raw)
    except ValueError as exc:
        raise ValueError(LEDGER_BINDING_FAILED) from exc
    policy = ledger.get("policy", {})
    trials = ledger.get("trials", [])
    hypotheses = ledger.get("hypotheses", [])
    family_count = sum(trial.get("strategy_family_id") == STRATEGY_FAMILY_ID for trial in trials)
    if (
        policy.get("max_global_trials", 0) - len(trials) < 10
        or policy.get("max_trials_per_family", 0) - family_count < 10
        or policy.get("max_total_hypotheses", 0) - len(hypotheses) < 10
        or ledger.get("sealed_oos_consumptions", 0) != 0
    ):
        raise ValueError(LEDGER_BINDING_FAILED)
    return copy.deepcopy(ledger)


def _load_spy_prices(root: Path, expected_sha256: str, *, development_end: str) -> pd.DataFrame:
    path = root / "raw" / "session-proxy" / "spy.json.gz"
    if path.is_symlink() or not path.is_file() or _file_sha(path) != expected_sha256:
        raise ValueError("SPY source hash mismatch.")
    try:
        rows = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("SPY source is invalid.") from exc
    if not isinstance(rows, list):
        raise ValueError("SPY source rows are invalid.")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("SPY row is invalid.")
        timestamp = pd.Timestamp(row.get("date"))
        if timestamp.tz is not None or timestamp.date().isoformat() != row.get("date") or timestamp > pd.Timestamp(development_end):
            if timestamp > pd.Timestamp(development_end):
                raise ValueError("sealed or post-development SPY row exposed.")
            raise ValueError("SPY timestamp is invalid.")
        raw_close = float(row.get("close"))
        adjusted_close = float(row.get("adjusted_close"))
        opening = float(row.get("open"))
        if min(raw_close, adjusted_close, opening) <= 0.0:
            raise ValueError("SPY price is invalid.")
        normalized.append(
            {"datetime": timestamp, "open": opening * adjusted_close / raw_close, "close": adjusted_close}
        )
    frame = pd.DataFrame(normalized).set_index("datetime").sort_index()
    if frame.empty or frame.index.has_duplicates:
        raise ValueError("SPY history is invalid.")
    return frame


def _build_hypothesis(
    *,
    hypothesis_id: str,
    factor_id: str,
    semantic_fingerprint: str,
    request: dict[str, Any],
    catalog_sha256: str,
    screen_sha256: str,
) -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "strategy_family_id": STRATEGY_FAMILY_ID,
        "parent_hypothesis_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": [
            request["expected_eodhd_manifest_sha256"],
            request["expected_fundamental_canonical_manifest_sha256"],
            request["expected_sec_audit_canonical_sha256"],
            catalog_sha256,
            screen_sha256,
        ],
        "economic_mechanism_fingerprint": f"point in time fundamental factor {factor_id}",
        "semantic_strategy_fingerprint": semantic_fingerprint,
        "market_scope": "US_COMMON_STOCKS",
        "instrument_classes": ["COMMON_STOCK"],
        "timeframe": "1D_WEEKLY_REBALANCE",
        "novelty_basis": f"predeclared point-in-time fundamental mechanism {factor_id}",
        "expected_failure_modes": [
            "FUNDAMENTAL_COVERAGE_INSUFFICIENT",
            "LOOKAHEAD",
            "COST_FRAGILITY",
            "YEAR_CONCENTRATION",
            "INSTRUMENT_CONCENTRATION",
        ],
        "provenance": {"source": PILOT_VERSION, "pilot_id": request["pilot_id"], "factor_id": factor_id},
    }


def _build_trial(
    *,
    experiment_id: str,
    hypothesis_id: str,
    factor_id: str,
    semantic_fingerprint: str,
    factor_result: dict[str, object],
    request: dict[str, Any],
    catalog_sha256: str,
    screen_sha256: str,
) -> dict[str, object]:
    configuration = {
        "factor_id": factor_id,
        "definition": copy.deepcopy(FACTOR_DEFINITIONS_V1[factor_id]),
        "portfolio_size": 15,
        "weighting": "EQUAL_WEIGHT_ONE_FIFTEENTH_ALLOW_CASH",
        "rebalance": "WEEKLY_NEXT_VERIFIED_SESSION_OPEN",
        "costs": copy.deepcopy(request["costs"]),
    }
    status = "WALK_FORWARD_COMPLETE" if factor_result["decision"] == "FACTOR_CONTINUE" else "STRATEGY_GATE_FAIL"
    material = _canonical_sha(
        {
            "semantic_strategy_fingerprint": semantic_fingerprint,
            "parameter_configuration": configuration,
            "data_snapshot_hashes": [
                request["expected_eodhd_manifest_sha256"],
                request["expected_fundamental_canonical_manifest_sha256"],
                request["expected_sec_audit_canonical_sha256"],
            ],
            "intervals": {
                "discovery": request["discovery_interval"],
                "development": request["development_interval"],
                "sealed": request["sealed_oos_interval"],
            },
        }
    )
    return {
        "experiment_id": experiment_id,
        "strategy_family_id": STRATEGY_FAMILY_ID,
        "strategy_fingerprint": semantic_fingerprint,
        "parent_hypothesis_id": hypothesis_id,
        "parent_trial_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": [
            request["expected_eodhd_manifest_sha256"],
            request["expected_fundamental_canonical_manifest_sha256"],
            request["expected_sec_audit_canonical_sha256"],
            catalog_sha256,
            screen_sha256,
        ],
        "economic_mechanism_fingerprint": f"point in time fundamental factor {factor_id}",
        "universe_variant": "US_COMMON_STOCK_PIT_LIQUID_1500",
        "screener_variant": "FROZEN_TEN_FUNDAMENTAL_FACTORS_V1",
        "ranking_variant": factor_id,
        "entry_variant": "WEEKLY_SIGNAL_NEXT_VERIFIED_SESSION_OPEN",
        "exit_variant": "NEXT_WEEK_REBALANCE_OPEN",
        "sizing_variant": "TOP_15_EQUAL_WEIGHT_ALLOW_CASH",
        "regime_filter_variant": "NONE",
        "parameter_configuration": configuration,
        "parameter_space_sha256": _canonical_sha(configuration),
        "data_snapshot_hashes": [
            request["expected_eodhd_manifest_sha256"],
            request["expected_fundamental_canonical_manifest_sha256"],
            request["expected_sec_audit_canonical_sha256"],
        ],
        "train_interval": copy.deepcopy(request["discovery_interval"]),
        "validation_interval": copy.deepcopy(request["development_interval"]),
        "walk_forward_intervals": [copy.deepcopy(request["development_interval"])],
        "sealed_oos_interval": copy.deepcopy(request["sealed_oos_interval"]),
        "sealed_oos_consumption_state": "UNCONSUMED",
        "transaction_cost_model": "ONE_WAY_BPS_15_30_50",
        "slippage_model": "INCLUDED_IN_COST_SCENARIOS",
        "trial_status": status,
        "metrics": copy.deepcopy(factor_result),
        "failure_taxonomy": list(factor_result["failure_taxonomy"]),
        "novelty_justification": {"kind": "FROZEN_FUNDAMENTAL_MECHANISM", "failure_mechanism_evidence": []},
        "canonical_trial_fingerprint": material,
        "provenance": {"source": PILOT_VERSION, "pilot_id": request["pilot_id"], "factor_id": factor_id},
    }


def _apply_operation(previous: dict[str, object], operation: dict[str, object]) -> dict[str, object]:
    return apply_global_experiment_ledger_operation_v1(
        {
            "version": "global_experiment_ledger_operation_request_v1",
            "previous_ledger": previous,
            "previous_ledger_sha256": previous["canonical_ledger_sha256"],
            "operation": operation,
            "provenance": {"source": PILOT_VERSION},
        }
    )


def _build_scorecard(
    *,
    status: str,
    request: dict[str, Any],
    screen: dict[str, object],
    updated_ledger: dict[str, object],
    experiment_ids: list[str],
    hypothesis_ids: list[str],
    sec_audit: dict[str, object],
) -> dict[str, object]:
    trial_lookup = {trial["experiment_id"]: trial for trial in updated_ledger["trials"]}
    factors = copy.deepcopy(screen["factors"])
    for factor_id in FACTOR_DEFINITIONS_V1:
        factors[factor_id]["ledger_trial_status"] = trial_lookup[f"{request['pilot_id']}-{factor_id}"]["trial_status"]
    result: dict[str, object] = {
        "version": "fundamental_edge_discovery_scorecard_v1",
        "status": status,
        "next_authorized_milestone": (
            "PORTFOLIO_CONSTRUCTION_DESIGN_V1"
            if status == FUNDAMENTAL_EDGE_CANDIDATE_FOUND
            else "DIVERSIFIED_ETF_TREND_FALLBACK_V1"
        ),
        "eodhd_manifest_sha256": request["expected_eodhd_manifest_sha256"],
        "fundamental_manifest_sha256": request["expected_fundamental_canonical_manifest_sha256"],
        "sec_audit_sha256": sec_audit["canonical_audit_sha256"],
        "fundamental_screen_sha256": screen["canonical_screen_sha256"],
        "updated_ledger_sha256": updated_ledger["canonical_ledger_sha256"],
        "factor_metrics": factors,
        "continuing_factor_ids": list(screen["continuing_factor_ids"]),
        "stopped_factor_ids": list(screen["stopped_factor_ids"]),
        "new_experiment_ids": experiment_ids,
        "new_hypothesis_ids": hypothesis_ids,
        "new_trial_count": 10,
        "new_hypothesis_count": 10,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
        "broker_action_authorized": False,
        "registry_write_authorized": False,
        "deployment_authorized": False,
    }
    result["canonical_scorecard_sha256"] = _canonical_sha(result)
    return result


def _coverage_failure_result(
    *,
    validated: dict[str, Any],
    previous_ledger: dict[str, object],
    price_metadata: dict[str, object],
    history_metadata: dict[str, object],
    panel_metadata: dict[str, object],
    sec_audit: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": validated["pilot_id"],
        "status": FUNDAMENTAL_COVERAGE_INSUFFICIENT,
        "price_metadata": copy.deepcopy(price_metadata),
        "fundamental_history_metadata": copy.deepcopy(history_metadata),
        "fundamental_panel_metadata": copy.deepcopy(panel_metadata),
        "sec_filing_audit": copy.deepcopy(sec_audit),
        "updated_ledger": copy.deepcopy(previous_ledger),
        "new_hypotheses": [],
        "new_trials": [],
        "new_hypothesis_count": 0,
        "new_trial_count": 0,
        "attempted_factor_ids": [],
        "accounted_factor_ids": [],
        "accounting_complete": True,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha(result)
    return result


def _accounting_failure_result(
    *,
    validated: dict[str, Any],
    previous_ledger: dict[str, object],
    updated_ledger: dict[str, object],
    price_metadata: dict[str, object],
    history_metadata: dict[str, object],
    panel_metadata: dict[str, object],
    screen: dict[str, object],
    hypothesis_ids: list[str],
    experiment_ids: list[str],
    accounted_factor_ids: list[str],
    sec_audit: dict[str, object],
) -> dict[str, object]:
    hypothesis_lookup = {item["hypothesis_id"]: item for item in updated_ledger.get("hypotheses", [])}
    trial_lookup = {item["experiment_id"]: item for item in updated_ledger.get("trials", [])}
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": validated["pilot_id"],
        "status": LEDGER_BINDING_FAILED,
        "price_metadata": copy.deepcopy(price_metadata),
        "fundamental_history_metadata": copy.deepcopy(history_metadata),
        "fundamental_panel_metadata": copy.deepcopy(panel_metadata),
        "sec_filing_audit": copy.deepcopy(sec_audit),
        "fundamental_screen": copy.deepcopy(screen),
        "previous_ledger_sha256": previous_ledger["canonical_ledger_sha256"],
        "updated_ledger": copy.deepcopy(updated_ledger),
        "new_hypotheses": [copy.deepcopy(hypothesis_lookup[item]) for item in hypothesis_ids if item in hypothesis_lookup],
        "new_trials": [copy.deepcopy(trial_lookup[item]) for item in experiment_ids if item in trial_lookup],
        "new_hypothesis_count": len(hypothesis_ids),
        "new_trial_count": len(experiment_ids),
        "attempted_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "accounted_factor_ids": accounted_factor_ids,
        "accounting_complete": False,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha(result)
    return result


def _interval(raw: Any, name: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"start", "end"}:
        raise ValueError(f"{name} is invalid.")
    start = _date(raw.get("start"), f"{name}.start")
    end = _date(raw.get("end"), f"{name}.end")
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError(f"{name} is out of order.")
    return {"start": start, "end": end}


def _sealed_interval(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"dataset_version", "start", "end"}:
        raise ValueError("sealed_oos_interval is invalid.")
    result = _interval({"start": raw["start"], "end": raw["end"]}, "sealed_oos_interval")
    return {"dataset_version": _text(raw.get("dataset_version"), "dataset_version"), **result}


def _date(raw: Any, name: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{name} is invalid.")
    value = pd.Timestamp(raw)
    if value.tz is not None or value.date().isoformat() != raw:
        raise ValueError(f"{name} is invalid.")
    return raw


def _text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be nonempty text.")
    return raw.strip()


def _sha_field(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"{name} must be lowercase SHA-256.")
    return raw


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
