from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_lab.execution.experiment_manifest_contract_v1 import _canonical_sha256
from research_lab.execution.isolated_orchestrator_runner_v1 import (
    CHECKSUMS_VERSION,
    INCOMPLETE_MARKER_NAME,
    RUN_REPORT_VERSION,
    STAGING_DIR_NAME,
    run_isolated_orchestrator_runner,
)


RESULT_VERSION = "orchestrator_run_verifier_replay_result_v1"
_REQUIRED_FILES = (
    "request.json",
    "bundle_manifest.json",
    "orchestrator_result.json",
    "run_report.json",
    "checksums.json",
)


def verify_orchestrator_run_directory(
    run_directory: str | Path,
    *,
    strict: bool = True,
    replay_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_directory).expanduser()
    if _is_incomplete(run_dir):
        return _result(status="INCOMPLETE", failure_reason="incomplete_run_directory")

    if not run_dir.exists() or not run_dir.is_dir():
        return _result(status="FAILED_VALIDATION", failure_reason="run_directory_missing")

    file_names = sorted(path.name for path in run_dir.iterdir() if path.is_file())
    strict_file_set_ok = set(file_names) == set(_REQUIRED_FILES)
    if strict and not strict_file_set_ok:
        return _result(status="FAILED_VALIDATION", failure_reason="unexpected_files_present", strict_file_set_ok=False)

    try:
        request = _load_json(run_dir / "request.json")
        bundle_manifest = _load_json(run_dir / "bundle_manifest.json")
        orchestrator_result = _load_json(run_dir / "orchestrator_result.json")
        run_report = _load_json(run_dir / "run_report.json")
        checksums = _load_json(run_dir / "checksums.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _result(status="FAILED_VALIDATION", failure_reason=str(exc), strict_file_set_ok=strict_file_set_ok)

    request_sha256 = _canonical_sha256(request)
    bundle_manifest_sha256 = _canonical_sha256(bundle_manifest)
    orchestrator_result_file_sha256 = _canonical_sha256(orchestrator_result)
    orchestrator_result_payload_sha256 = str(orchestrator_result.get("output_payload_sha256") or "")
    run_report_sha256 = _canonical_sha256(run_report)

    request_sha256_matches = request_sha256 == bundle_manifest.get("canonical_request_sha256") == run_report.get("request_sha256")
    bundle_manifest_sha256_matches = bundle_manifest_sha256 == run_report.get("bundle_manifest_sha256")
    orchestrator_result_sha256_matches = orchestrator_result_payload_sha256 == run_report.get("orchestrator_result_sha256")
    checksums_match = _checksums_match(
        checksums=checksums,
        request_sha256=request_sha256,
        bundle_manifest_sha256=bundle_manifest_sha256,
        orchestrator_result_file_sha256=orchestrator_result_file_sha256,
        run_report_sha256=run_report_sha256,
    )
    identity_ok = _identity_consistency_ok(
        request=request,
        bundle_manifest=bundle_manifest,
        orchestrator_result=orchestrator_result,
        run_report=run_report,
    )
    safety_flags_ok = _safety_flags_ok(orchestrator_result=orchestrator_result, run_report=run_report)

    verified = all(
        (
            request_sha256_matches,
            bundle_manifest_sha256_matches,
            orchestrator_result_sha256_matches,
            checksums_match,
            identity_ok,
            safety_flags_ok,
            strict_file_set_ok or not strict,
        )
    )
    if not verified:
        return _result(
            status="FAILED_VALIDATION",
            failure_reason="verification_failed",
            run_id=run_report.get("run_id"),
            final_status=run_report.get("final_status"),
            selected_variant_id=run_report.get("selected_variant_id"),
            request_sha256_matches=request_sha256_matches,
            bundle_manifest_sha256_matches=bundle_manifest_sha256_matches,
            orchestrator_result_sha256_matches=orchestrator_result_sha256_matches,
            checksums_match=checksums_match,
            strict_file_set_ok=strict_file_set_ok,
        )

    if replay_output_dir is None:
        return _result(
            status="VERIFIED",
            run_id=run_report["run_id"],
            final_status=run_report["final_status"],
            selected_variant_id=run_report["selected_variant_id"],
            request_sha256_matches=True,
            bundle_manifest_sha256_matches=True,
            orchestrator_result_sha256_matches=True,
            checksums_match=True,
            strict_file_set_ok=strict_file_set_ok,
        )

    replay_dir = Path(replay_output_dir).expanduser()
    replay_report = run_isolated_orchestrator_runner(
        {
            "version": "orchestrator_run_bundle_contract_request_v1",
            "run_id": run_report["run_id"],
            "orchestrator_request": request,
            "request_source_metadata": bundle_manifest["request_source_metadata"],
            "supplied_input_artifact_hashes": bundle_manifest["source_artifact_hashes"],
            "expected_experiment_id": bundle_manifest["expected_identities"]["experiment_id"],
            "expected_strategy_identity": bundle_manifest["expected_identities"]["strategy_identity"],
            "expected_dataset_identity": bundle_manifest["expected_identities"]["dataset_identity"],
            "expected_knihomol_evidence_ids": bundle_manifest["expected_identities"]["knihomol_evidence_ids"],
            "provenance": bundle_manifest["provenance"],
        },
        output_dir=replay_dir,
    )
    replay_orchestrator_result = _load_json(replay_dir / "orchestrator_result.json")

    replay_semantic_match = _semantic_replay_match(
        stored_orchestrator_result=orchestrator_result,
        replay_orchestrator_result=replay_orchestrator_result,
        stored_run_report=run_report,
        replay_run_report=replay_report,
    )
    replay_hash_match = (
        replay_report["request_sha256"] == run_report["request_sha256"]
        and replay_report["bundle_manifest_sha256"] == run_report["bundle_manifest_sha256"]
        and replay_report["orchestrator_result_sha256"] == run_report["orchestrator_result_sha256"]
    )
    if replay_semantic_match and replay_hash_match:
        return _result(
            status="REPLAY_MATCH",
            run_id=run_report["run_id"],
            final_status=run_report["final_status"],
            selected_variant_id=run_report["selected_variant_id"],
            request_sha256_matches=True,
            bundle_manifest_sha256_matches=True,
            orchestrator_result_sha256_matches=True,
            checksums_match=True,
            strict_file_set_ok=strict_file_set_ok,
            replay_semantic_match=True,
            replay_hash_match=True,
        )

    return _result(
        status="REPLAY_MISMATCH",
        failure_reason="replay_mismatch",
        run_id=run_report["run_id"],
        final_status=run_report["final_status"],
        selected_variant_id=run_report["selected_variant_id"],
        request_sha256_matches=True,
        bundle_manifest_sha256_matches=True,
        orchestrator_result_sha256_matches=True,
        checksums_match=True,
        strict_file_set_ok=strict_file_set_ok,
        replay_semantic_match=replay_semantic_match,
        replay_hash_match=replay_hash_match,
    )


def _semantic_replay_match(
    *,
    stored_orchestrator_result: dict[str, Any],
    replay_orchestrator_result: dict[str, Any],
    stored_run_report: dict[str, Any],
    replay_run_report: dict[str, Any],
) -> bool:
    return (
        stored_orchestrator_result == replay_orchestrator_result
        and stored_run_report["final_status"] == replay_run_report["final_status"]
        and stored_run_report["selected_variant_id"] == replay_run_report["selected_variant_id"]
        and stored_run_report["lineage"] == replay_run_report["lineage"]
    )


def _checksums_match(
    *,
    checksums: dict[str, Any],
    request_sha256: str,
    bundle_manifest_sha256: str,
    orchestrator_result_file_sha256: str,
    run_report_sha256: str,
) -> bool:
    if checksums.get("version") != CHECKSUMS_VERSION:
        return False
    files = checksums.get("files")
    if not isinstance(files, dict):
        return False
    return files == {
        "bundle_manifest.json": bundle_manifest_sha256,
        "orchestrator_result.json": orchestrator_result_file_sha256,
        "request.json": request_sha256,
        "run_report.json": run_report_sha256,
    }


def _identity_consistency_ok(
    *,
    request: dict[str, Any],
    bundle_manifest: dict[str, Any],
    orchestrator_result: dict[str, Any],
    run_report: dict[str, Any],
) -> bool:
    manifest_request = request["experiment_manifest_request"]
    return (
        bundle_manifest["expected_identities"]["experiment_id"] == manifest_request["experiment_id"] == orchestrator_result["lineage"]["experiment_id"]
        and bundle_manifest["expected_identities"]["strategy_identity"] == manifest_request["strategy_identity"]
        and orchestrator_result["lineage"]["strategy_id"] == manifest_request["strategy_identity"]["strategy_id"]
        and orchestrator_result["lineage"]["strategy_version"] == manifest_request["strategy_identity"]["strategy_version"]
        and run_report["selected_variant_id"] == orchestrator_result["selected_variant_id"] == orchestrator_result["lineage"]["selected_variant_id"]
        and run_report["final_status"] == orchestrator_result["final_status"]
    )


def _safety_flags_ok(*, orchestrator_result: dict[str, Any], run_report: dict[str, Any]) -> bool:
    return (
        orchestrator_result["provider_calls_used"] == 0
        and orchestrator_result["registry_write_performed"] is False
        and orchestrator_result["broker_actions_used"] == 0
        and orchestrator_result["promotion_performed"] is False
        and orchestrator_result["deployment_gate_run"] is False
        and orchestrator_result["external_data_used"] is False
        and run_report["execution_authority_granted"] is False
        and run_report["persistence_authority_granted"] is False
        and run_report["production_runtime_supported"] is False
    )


def _is_incomplete(run_dir: Path) -> bool:
    return (run_dir / STAGING_DIR_NAME / INCOMPLETE_MARKER_NAME).exists()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _result(
    *,
    status: str,
    failure_reason: str | None = None,
    run_id: str | None = None,
    final_status: str | None = None,
    selected_variant_id: str | None = None,
    request_sha256_matches: bool | None = None,
    bundle_manifest_sha256_matches: bool | None = None,
    orchestrator_result_sha256_matches: bool | None = None,
    checksums_match: bool | None = None,
    strict_file_set_ok: bool | None = None,
    replay_semantic_match: bool | None = None,
    replay_hash_match: bool | None = None,
) -> dict[str, Any]:
    return {
        "version": RESULT_VERSION,
        "verification_status": status,
        "failure_reason": failure_reason,
        "verification_read_only": True,
        "run_id": run_id,
        "final_status": final_status,
        "selected_variant_id": selected_variant_id,
        "request_sha256_matches": request_sha256_matches,
        "bundle_manifest_sha256_matches": bundle_manifest_sha256_matches,
        "orchestrator_result_sha256_matches": orchestrator_result_sha256_matches,
        "checksums_match": checksums_match,
        "strict_file_set_ok": strict_file_set_ok,
        "replay_semantic_match": replay_semantic_match,
        "replay_hash_match": replay_hash_match,
    }
