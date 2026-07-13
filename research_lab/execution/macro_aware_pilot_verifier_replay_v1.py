from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import _canonical_sha256
from research_lab.execution.macro_aware_pilot_runner_v1 import (
    CHECKSUMS_VERSION,
    COMPLETE_VERSION,
    OUTPUT_FILES,
    PILOT_RUN_LABEL,
    RUN_REPORT_VERSION,
    _SAFE_FLAGS,
    _validate_acceptance_result,
    _validate_request,
    run_macro_aware_pilot,
)


VERIFICATION_RESULT_VERSION = "macro_aware_pilot_verification_result_v1"
REPLAY_RESULT_VERSION = "macro_aware_pilot_replay_result_v1"
_CHECKSUMMED_FILES = (
    "request.json",
    "macro_aware_result.json",
    "run_report.json",
)
_RESULT_FIELDS = {
    "version",
    "acceptance_version",
    "acceptance_id",
    "status",
    "lineage",
    "macro_snapshot_result",
    "alignment_result",
    "feature_set_result",
    "macro_regime_candidate_result",
    "baseline_strategy_result",
    "macro_filter_evaluator_result",
    "review_artifact",
    "evaluator_classification",
    "baseline_preservation_proof",
    "protective_exit_preservation_proof",
    "no_look_ahead_proof",
    "validation_errors",
    "failure_reason",
    "safety_flags",
    "provenance",
    "input_sha256",
    "output_payload_sha256",
}
_REPORT_FIELDS = {
    "version",
    "runner_version",
    "run_id",
    "run_label",
    "created_at",
    "execution_status",
    "acceptance_status",
    "evaluator_classification",
    "acceptance_request_sha256",
    "acceptance_result_sha256",
    "market_dataset_identity",
    "market_symbol",
    "market_bars_sha256",
    "market_source_artifact_sha256",
    "synthetic_macro_label",
    "lineage",
    "no_look_ahead_proof",
    "baseline_preservation_proof",
    "protective_exit_preservation_proof",
    "written_files",
    "provenance",
    "output_payload_sha256",
    *_SAFE_FLAGS.keys(),
}


def verify_macro_aware_pilot_run(run_directory: str | Path) -> dict[str, Any]:
    run_dir = Path(run_directory).expanduser()
    if _is_incomplete(run_dir):
        return _verification_result("INCOMPLETE", failure_reason="incomplete_run_directory")
    if not run_dir.exists() or not run_dir.is_dir():
        return _verification_result("FAILED_VALIDATION", failure_reason="run_directory_missing")
    observed_entries = {item.name for item in run_dir.iterdir()}
    if "COMPLETE" not in observed_entries:
        return _verification_result("INCOMPLETE", failure_reason="complete_marker_missing")
    if observed_entries != set(OUTPUT_FILES):
        return _verification_result(
            "FAILED_VALIDATION",
            failure_reason="artifact_set_mismatch",
            exact_artifact_set=False,
        )
    try:
        request = _load_json(run_dir / "request.json")
        result = _load_json(run_dir / "macro_aware_result.json")
        report = _load_json(run_dir / "run_report.json")
        checksums = _load_json(run_dir / "checksums.json")
        complete = _load_json(run_dir / "COMPLETE")
        if set(result) != _RESULT_FIELDS:
            raise ValueError("result schema mismatch.")
        if set(report) != _REPORT_FIELDS:
            raise ValueError("run report schema mismatch.")
        validated = _validate_request(request)
        _validate_acceptance_result(validated, result)
        _validate_child_integrity(result)
        _validate_report(validated, result=result, report=report)
        _validate_complete(validated, complete)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return _verification_result(
            "FAILED_VALIDATION",
            failure_reason=str(exc),
            exact_artifact_set=True,
        )
    checksums_match = _checksums_match(
        checksums,
        run_id=validated["run_id"],
        payloads={
            "request.json": request,
            "macro_aware_result.json": result,
            "run_report.json": report,
        },
    )
    if not checksums_match:
        return _verification_result(
            "FAILED_VALIDATION",
            failure_reason="checksum_manifest_mismatch",
            run_id=validated["run_id"],
            run_label=validated["run_label"],
            exact_artifact_set=True,
            checksums_match=False,
        )
    return _verification_result(
        "VERIFIED",
        run_id=validated["run_id"],
        run_label=validated["run_label"],
        acceptance_status=result["status"],
        evaluator_classification=result["evaluator_classification"],
        acceptance_request_sha256=validated["expected_acceptance_request_sha256"],
        acceptance_result_sha256=result["output_payload_sha256"],
        exact_artifact_set=True,
        checksums_match=True,
    )


def replay_macro_aware_pilot(
    source_run_directory: str | Path,
    *,
    replay_output_dir: str | Path,
) -> dict[str, Any]:
    source_dir = Path(source_run_directory).expanduser()
    source_before = _directory_payload_hash(source_dir)
    source_verification = verify_macro_aware_pilot_run(source_dir)
    if source_verification["verification_status"] != "VERIFIED":
        return _replay_result(
            "REPLAY_MISMATCH",
            failure_reason="source_run_not_verified",
            source_verification_status=source_verification["verification_status"],
        )
    source_request = _load_json(source_dir / "request.json")
    source_result = _load_json(source_dir / "macro_aware_result.json")
    run_macro_aware_pilot(source_request, output_dir=replay_output_dir)
    replay_dir = Path(replay_output_dir).expanduser()
    replay_verification = verify_macro_aware_pilot_run(replay_dir)
    replay_result = _load_json(replay_dir / "macro_aware_result.json")
    comparison = _deterministic_comparison(
        source_request=source_request,
        source_result=source_result,
        replay_request=_load_json(replay_dir / "request.json"),
        replay_result=replay_result,
    )
    source_unchanged = source_before == _directory_payload_hash(source_dir)
    all_match = (
        comparison["all_match"]
        and replay_verification["verification_status"] == "VERIFIED"
        and source_unchanged
    )
    return _replay_result(
        "REPLAY_MATCH" if all_match else "REPLAY_MISMATCH",
        failure_reason=None if all_match else "deterministic_replay_mismatch",
        run_id=source_verification["run_id"],
        run_label=source_verification["run_label"],
        source_verification_status=source_verification["verification_status"],
        replay_verification_status=replay_verification["verification_status"],
        deterministic_comparison=comparison,
        source_run_unchanged=source_unchanged,
    )


def _validate_report(validated: dict[str, Any], *, result: dict[str, Any], report: dict[str, Any]) -> None:
    if report.get("version") != RUN_REPORT_VERSION:
        raise ValueError("run report version mismatch.")
    if report.get("run_id") != validated["run_id"]:
        raise ValueError("run report run_id mismatch.")
    if report.get("run_label") != PILOT_RUN_LABEL:
        raise ValueError("run report label mismatch.")
    if report.get("created_at") != validated["created_at"]:
        raise ValueError("run report created_at mismatch.")
    if report.get("execution_status") != "COMPLETED":
        raise ValueError("run report execution status mismatch.")
    bindings = {
        "acceptance_status": result["status"],
        "evaluator_classification": result["evaluator_classification"],
        "acceptance_request_sha256": validated["expected_acceptance_request_sha256"],
        "acceptance_result_sha256": result["output_payload_sha256"],
        "market_dataset_identity": validated["expected_market_dataset_identity"],
        "market_symbol": validated["expected_market_symbol"],
        "market_bars_sha256": validated["expected_market_bars_sha256"],
        "market_source_artifact_sha256": validated["expected_market_source_artifact_sha256"],
        "synthetic_macro_label": validated["expected_synthetic_macro_label"],
        "lineage": result["lineage"],
        "no_look_ahead_proof": result["no_look_ahead_proof"],
        "baseline_preservation_proof": result["baseline_preservation_proof"],
        "protective_exit_preservation_proof": result["protective_exit_preservation_proof"],
        "written_files": list(OUTPUT_FILES),
    }
    for field, expected in bindings.items():
        if report.get(field) != expected:
            raise ValueError(f"run report {field} mismatch.")
    for field, expected in _SAFE_FLAGS.items():
        if report.get(field) != expected:
            raise ValueError(f"run report safety violation: {field}")
    if report.get("output_payload_sha256") != _self_hash(report):
        raise ValueError("run report output hash mismatch.")


def _validate_complete(validated: dict[str, Any], complete: dict[str, Any]) -> None:
    if complete != {
        "version": COMPLETE_VERSION,
        "run_id": validated["run_id"],
        "status": "COMPLETE",
    }:
        raise ValueError("COMPLETE marker mismatch.")


def _validate_child_integrity(result: dict[str, Any]) -> None:
    child_names = (
        "macro_snapshot_result",
        "alignment_result",
        "feature_set_result",
        "macro_regime_candidate_result",
        "baseline_strategy_result",
        "macro_filter_evaluator_result",
        "review_artifact",
    )
    for child_name in child_names:
        child = result[child_name]
        if child.get("output_payload_sha256") != _self_hash(child):
            raise ValueError(f"child integrity violation: {child_name} output hash mismatch.")
    review = result["review_artifact"]
    for child_name in ("adapter_result", "strategy_contract_result", "bridge_result"):
        child = review[child_name]
        if child.get("output_payload_sha256") != _self_hash(child):
            raise ValueError(f"child integrity violation: review_artifact.{child_name} output hash mismatch.")
    lineage = result["lineage"]
    bindings = {
        "macro_snapshot_sha256": result["macro_snapshot_result"]["output_payload_sha256"],
        "alignment_output_sha256": result["alignment_result"]["output_payload_sha256"],
        "feature_set_output_sha256": result["feature_set_result"]["output_payload_sha256"],
        "macro_regime_candidate_output_sha256": result["macro_regime_candidate_result"][
            "output_payload_sha256"
        ],
        "evaluator_output_sha256": result["macro_filter_evaluator_result"]["output_payload_sha256"],
        "market_data_sha256": _canonical_sha256(
            result["baseline_strategy_result"]["synthetic_bars"]
        ),
        "market_source_artifact_sha256": review["adapter_result"]["output_payload_sha256"],
    }
    for field, expected in bindings.items():
        if lineage.get(field) != expected:
            raise ValueError(f"child integrity violation: lineage.{field} mismatch.")
    if result["evaluator_classification"] != result["macro_filter_evaluator_result"]["classification"]:
        raise ValueError("child integrity violation: evaluator classification mismatch.")
    if result["baseline_strategy_result"] != review["strategy_contract_result"]:
        raise ValueError("child integrity violation: baseline strategy review copy mismatch.")
    if review["adapter_result"].get("symbol") != lineage.get("market_symbol"):
        raise ValueError("child integrity violation: adapter market symbol mismatch.")
    if any(provider != "SYNTHETIC" for provider in lineage.get("macro_provider_identities", [])):
        raise ValueError("child integrity violation: macro provider identity is not SYNTHETIC.")


def _checksums_match(
    checksums: dict[str, Any],
    *,
    run_id: str,
    payloads: dict[str, dict[str, Any]],
) -> bool:
    return checksums == {
        "version": CHECKSUMS_VERSION,
        "run_id": run_id,
        "files": {
            file_name: _canonical_sha256(payloads[file_name])
            for file_name in sorted(_CHECKSUMMED_FILES)
        },
    }


def _deterministic_comparison(
    *,
    source_request: dict[str, Any],
    source_result: dict[str, Any],
    replay_request: dict[str, Any],
    replay_result: dict[str, Any],
) -> dict[str, bool]:
    source_lineage = source_result["lineage"]
    replay_lineage = replay_result["lineage"]
    comparisons = {
        "request_hash": _canonical_sha256(source_request) == _canonical_sha256(replay_request),
        "acceptance_result_hash": source_result["output_payload_sha256"] == replay_result["output_payload_sha256"],
        "market_bars_hash": source_lineage["market_data_sha256"] == replay_lineage["market_data_sha256"],
        "market_source_artifact_hash": source_lineage["market_source_artifact_sha256"] == replay_lineage["market_source_artifact_sha256"],
        "macro_snapshot_hash": source_lineage["macro_snapshot_sha256"] == replay_lineage["macro_snapshot_sha256"],
        "alignment_hash": source_lineage["alignment_output_sha256"] == replay_lineage["alignment_output_sha256"],
        "feature_set_hash": source_lineage["feature_set_output_sha256"] == replay_lineage["feature_set_output_sha256"],
        "regime_candidate_hash": source_lineage["macro_regime_candidate_output_sha256"] == replay_lineage["macro_regime_candidate_output_sha256"],
        "evaluator_hash": source_lineage["evaluator_output_sha256"] == replay_lineage["evaluator_output_sha256"],
        "classification": source_result["evaluator_classification"] == replay_result["evaluator_classification"],
        "lineage": source_lineage == replay_lineage,
        "no_look_ahead_proof": source_result["no_look_ahead_proof"] == replay_result["no_look_ahead_proof"],
        "baseline_preservation_proof": source_result["baseline_preservation_proof"] == replay_result["baseline_preservation_proof"],
        "protective_exit_preservation_proof": source_result["protective_exit_preservation_proof"] == replay_result["protective_exit_preservation_proof"],
        "safety_flags": source_result["safety_flags"] == replay_result["safety_flags"],
        "acceptance_result": source_result == replay_result,
    }
    comparisons["all_match"] = all(comparisons.values())
    return comparisons


def _self_hash(payload: dict[str, Any]) -> str:
    return _canonical_sha256({key: value for key, value in payload.items() if key != "output_payload_sha256"})


def _directory_payload_hash(path: Path) -> str:
    payload = {
        item.name: item.read_bytes().hex()
        for item in sorted(path.iterdir(), key=lambda candidate: candidate.name)
        if item.is_file()
    }
    return _canonical_sha256(payload)


def _is_incomplete(run_dir: Path) -> bool:
    staging = run_dir.with_name(f".{run_dir.name}.staging")
    return (staging / "INCOMPLETE").exists() or (run_dir / "INCOMPLETE").exists()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _verification_result(
    status: str,
    *,
    failure_reason: str | None = None,
    run_id: str | None = None,
    run_label: str | None = None,
    acceptance_status: str | None = None,
    evaluator_classification: str | None = None,
    acceptance_request_sha256: str | None = None,
    acceptance_result_sha256: str | None = None,
    exact_artifact_set: bool | None = None,
    checksums_match: bool | None = None,
) -> dict[str, Any]:
    return {
        "version": VERIFICATION_RESULT_VERSION,
        "verification_status": status,
        "failure_reason": failure_reason,
        "verification_read_only": True,
        "run_id": run_id,
        "run_label": run_label,
        "acceptance_status": acceptance_status,
        "evaluator_classification": evaluator_classification,
        "acceptance_request_sha256": acceptance_request_sha256,
        "acceptance_result_sha256": acceptance_result_sha256,
        "exact_artifact_set": exact_artifact_set,
        "checksums_match": checksums_match,
    }


def _replay_result(
    status: str,
    *,
    failure_reason: str | None = None,
    run_id: str | None = None,
    run_label: str | None = None,
    source_verification_status: str | None = None,
    replay_verification_status: str | None = None,
    deterministic_comparison: dict[str, bool] | None = None,
    source_run_unchanged: bool | None = None,
) -> dict[str, Any]:
    return {
        "version": REPLAY_RESULT_VERSION,
        "replay_status": status,
        "failure_reason": failure_reason,
        "run_id": run_id,
        "run_label": run_label,
        "source_verification_status": source_verification_status,
        "replay_verification_status": replay_verification_status,
        "deterministic_comparison": deterministic_comparison,
        "source_run_unchanged": source_run_unchanged,
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "paper_trading_performed": False,
        "deployment_performed": False,
        "promotion_performed": False,
        "generated_code_executed": False,
        "automatic_strategy_application_performed": False,
        "production_runtime_supported": False,
    }
