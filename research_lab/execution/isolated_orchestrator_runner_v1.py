from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from research_lab.execution.e2e_research_orchestrator_acceptance_v1 import (
    run_e2e_research_orchestrator_acceptance,
)
from research_lab.execution.experiment_manifest_contract_v1 import _canonical_sha256
from research_lab.execution.orchestrator_run_bundle_contract_v1 import (
    build_orchestrator_run_bundle_contract,
)


RUNNER_VERSION = "isolated_orchestrator_runner_v1"
RUN_REPORT_VERSION = "isolated_orchestrator_run_report_v1"
CHECKSUMS_VERSION = "isolated_orchestrator_run_checksums_v1"
STAGING_DIR_NAME = ".orchestrator-run-staging"
LOCK_FILE_NAME = ".run.lock"
INCOMPLETE_MARKER_NAME = "incomplete.json"
_OUTPUT_FILES = (
    "request.json",
    "bundle_manifest.json",
    "orchestrator_result.json",
    "run_report.json",
    "checksums.json",
)


def run_isolated_orchestrator_runner(bundle_request: dict[str, object], *, output_dir: str | Path) -> dict[str, Any]:
    output_path = Path(output_dir).expanduser()
    repo_root = Path(__file__).resolve().parents[2]
    _validate_output_dir_path(output_path, repo_root=repo_root)

    bundle_contract = build_orchestrator_run_bundle_contract(bundle_request)
    run_id = bundle_contract["run_id"]
    _prepare_output_dir(output_path)
    lock_path = output_path / LOCK_FILE_NAME
    staging_dir = output_path / STAGING_DIR_NAME
    _acquire_lock(lock_path)
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        orchestrator_result = run_e2e_research_orchestrator_acceptance(bundle_contract["normalized_request"])
        request_payload = bundle_contract["normalized_request"]
        bundle_manifest = bundle_contract["bundle_manifest"]
        run_report = _build_run_report(
            bundle_contract=bundle_contract,
            orchestrator_result=orchestrator_result,
        )

        staged_hashes = {
            "request.json": _write_verified_json(staging_dir / "request.json", request_payload),
            "bundle_manifest.json": _write_verified_json(staging_dir / "bundle_manifest.json", bundle_manifest),
            "orchestrator_result.json": _write_verified_json(staging_dir / "orchestrator_result.json", orchestrator_result),
            "run_report.json": _write_verified_json(staging_dir / "run_report.json", run_report),
        }
        checksums = {
            "version": CHECKSUMS_VERSION,
            "run_id": run_id,
            "files": dict(sorted(staged_hashes.items())),
        }
        _write_verified_json(staging_dir / "checksums.json", checksums)
        _finalize_staged_run(staging_dir=staging_dir, output_dir=output_path)
        return run_report
    except Exception as exc:
        _write_incomplete_marker(staging_dir=staging_dir, run_id=run_id, failure_reason=str(exc))
        raise
    finally:
        _release_lock(lock_path)


def _build_run_report(*, bundle_contract: dict[str, Any], orchestrator_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": RUN_REPORT_VERSION,
        "runner_version": RUNNER_VERSION,
        "run_id": bundle_contract["run_id"],
        "execution_status": "completed",
        "failure_reason": None,
        "request_sha256": bundle_contract["canonical_request_sha256"],
        "bundle_manifest_sha256": bundle_contract["bundle_manifest_sha256"],
        "orchestrator_result_sha256": orchestrator_result["output_payload_sha256"],
        "final_status": orchestrator_result["final_status"],
        "selected_variant_id": orchestrator_result["selected_variant_id"],
        "lineage": orchestrator_result["lineage"],
        "written_files": list(_OUTPUT_FILES),
        "run_directory_complete": True,
        "execution_authority_granted": False,
        "persistence_authority_granted": False,
        "provider_calls_used": orchestrator_result["provider_calls_used"],
        "registry_write_performed": orchestrator_result["registry_write_performed"],
        "broker_actions_used": orchestrator_result["broker_actions_used"],
        "promotion_performed": orchestrator_result["promotion_performed"],
        "deployment_gate_run": orchestrator_result["deployment_gate_run"],
        "external_data_used": orchestrator_result["external_data_used"],
        "production_runtime_supported": False,
        "input_sha256": _canonical_sha256(
            {
                "bundle_contract": bundle_contract["output_payload_sha256"],
                "orchestrator_result": orchestrator_result["output_payload_sha256"],
            }
        ),
        "output_payload_sha256": _canonical_sha256(
            {
                "run_id": bundle_contract["run_id"],
                "request_sha256": bundle_contract["canonical_request_sha256"],
                "bundle_manifest_sha256": bundle_contract["bundle_manifest_sha256"],
                "orchestrator_result_sha256": orchestrator_result["output_payload_sha256"],
                "final_status": orchestrator_result["final_status"],
                "selected_variant_id": orchestrator_result["selected_variant_id"],
                "lineage": orchestrator_result["lineage"],
                "written_files": list(_OUTPUT_FILES),
            }
        ),
    }


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError("output_dir must be a directory path.")
        contents = {item.name for item in output_dir.iterdir()}
        if LOCK_FILE_NAME in contents:
            raise ValueError("output_dir lock already exists.")
        if "run_report.json" in contents:
            raise ValueError("output_dir already contains a completed run.")
        if contents:
            raise ValueError("output_dir must be empty or absent; existing non-empty directory is forbidden.")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)


def _acquire_lock(lock_path: Path) -> None:
    if lock_path.exists():
        raise ValueError("output_dir lock already exists.")
    with lock_path.open("x", encoding="utf-8") as handle:
        handle.write(RUNNER_VERSION + "\n")


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return


def _write_verified_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    expected_sha256 = _canonical_sha256(payload)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(encoded, encoding="utf-8")
    os.replace(temp_path, path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    observed_sha256 = _canonical_sha256(loaded)
    if observed_sha256 != expected_sha256:
        raise OSError(f"post-write verification failed for {path.name}")
    return observed_sha256


def _write_incomplete_marker(*, staging_dir: Path, run_id: str, failure_reason: str) -> None:
    if not staging_dir.exists():
        return
    marker = {
        "version": RUN_REPORT_VERSION,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "execution_status": "incomplete",
        "failure_reason": failure_reason,
        "run_directory_complete": False,
        "written_files": [],
        "execution_authority_granted": False,
        "persistence_authority_granted": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "promotion_performed": False,
        "deployment_gate_run": False,
        "external_data_used": False,
        "production_runtime_supported": False,
    }
    try:
        _write_verified_json(staging_dir / INCOMPLETE_MARKER_NAME, marker)
    except OSError:
        return


def _finalize_staged_run(*, staging_dir: Path, output_dir: Path) -> None:
    for file_name in _OUTPUT_FILES:
        source = staging_dir / file_name
        target = output_dir / file_name
        if target.exists():
            raise OSError(f"refusing to overwrite finalized artifact {file_name}")
        os.replace(source, target)
    staging_dir.rmdir()


def _validate_output_dir_path(output_dir: Path, *, repo_root: Path) -> None:
    if any(part == ".." for part in output_dir.parts):
        raise ValueError("unsafe_output_dir: parent-directory traversal is forbidden.")
    resolved_output = _resolved_destination_path(output_dir)
    if _is_under_protected_root(resolved_output, repo_root=repo_root):
        raise ValueError("unsafe_output_dir: output directory is inside a protected path.")


def _protected_output_roots(repo_root: Path) -> list[Path]:
    resolved_repo_root = repo_root.resolve()
    roots = [
        resolved_repo_root,
        resolved_repo_root / "registry",
        resolved_repo_root / "deploy",
        resolved_repo_root / "deployment",
        Path("/opt/trading/private"),
        Path("/opt/trading/private/hermes_books"),
        Path("/opt/trading/research-lab"),
        Path.home() / "AppData" / "Local" / "hermes",
    ]
    return [_normalize_path(root) for root in roots]


def _permitted_output_roots(repo_root: Path) -> list[Path]:
    return [_normalize_path(Path("/opt/trading/private/research_orchestrator_runs"))]


def _resolved_destination_path(path: Path) -> Path:
    resolved_parent = _resolved_existing_parent(path)
    if path.exists():
        return path.resolve()
    missing_parts: list[str] = []
    current = path
    while not current.exists():
        missing_parts.append(current.name)
        current = current.parent
    return resolved_parent.joinpath(*reversed(missing_parts))


def _resolved_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        current = current.parent
    return current.resolve()


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_under_protected_root(path: Path, *, repo_root: Path) -> bool:
    for permitted_root in _permitted_output_roots(repo_root):
        try:
            path.relative_to(permitted_root)
            return False
        except ValueError:
            continue
    for protected_root in _protected_output_roots(repo_root):
        try:
            path.relative_to(protected_root)
            return True
        except ValueError:
            continue
    return False
