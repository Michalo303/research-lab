from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research_lab.execution.isolated_orchestrator_runner_v1 import (
    run_isolated_orchestrator_runner,
)
from research_lab.execution.orchestrator_run_verifier_replay_v1 import (
    verify_orchestrator_run_directory,
)
from tests.test_orchestrator_run_bundle_contract_v1 import _request as _bundle_request


def _request() -> dict[str, object]:
    return copy.deepcopy(_bundle_request())


def _completed_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "completed-run"
    run_isolated_orchestrator_runner(_request(), output_dir=run_dir)
    return run_dir


def test_verifies_completed_run_and_replay_matches(tmp_path):
    run_dir = _completed_run_dir(tmp_path)
    replay_dir = tmp_path / "replay-run"

    result = verify_orchestrator_run_directory(run_dir, replay_output_dir=replay_dir)

    assert result["version"] == "orchestrator_run_verifier_replay_result_v1"
    assert result["verification_status"] == "REPLAY_MATCH"
    assert result["verification_read_only"] is True
    assert result["failure_reason"] is None
    assert result["run_id"] == "RUN-20260712-001"
    assert result["final_status"] == "ACCEPTED_REVIEW_ONLY"
    assert result["selected_variant_id"] == "SIMPLER_SAFE"
    assert result["request_sha256_matches"] is True
    assert result["bundle_manifest_sha256_matches"] is True
    assert result["orchestrator_result_sha256_matches"] is True
    assert result["checksums_match"] is True
    assert result["strict_file_set_ok"] is True
    assert result["replay_semantic_match"] is True
    assert result["replay_hash_match"] is True
    assert (replay_dir / "run_report.json").exists()


def test_verifies_without_replay_when_no_replay_dir_is_supplied(tmp_path):
    run_dir = _completed_run_dir(tmp_path)

    result = verify_orchestrator_run_directory(run_dir)

    assert result["verification_status"] == "VERIFIED"
    assert result["replay_semantic_match"] is None
    assert result["replay_hash_match"] is None


def test_strict_mode_rejects_unexpected_files(tmp_path):
    run_dir = _completed_run_dir(tmp_path)
    (run_dir / "unexpected.txt").write_text("extra\n", encoding="utf-8")

    result = verify_orchestrator_run_directory(run_dir)

    assert result["verification_status"] == "FAILED_VALIDATION"
    assert result["failure_reason"] == "unexpected_files_present"
    assert result["strict_file_set_ok"] is False


def test_incomplete_run_directory_reports_incomplete(tmp_path, monkeypatch):
    output_dir = tmp_path / "failed-run"
    module = __import__("research_lab.execution.isolated_orchestrator_runner_v1", fromlist=["_write_verified_json"])
    original = module._write_verified_json

    def failing_writer(path, payload):
        if path.name == "orchestrator_result.json":
            raise OSError("forced write failure")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_verified_json", failing_writer)
    with pytest.raises(OSError):
        run_isolated_orchestrator_runner(_request(), output_dir=output_dir)

    result = verify_orchestrator_run_directory(output_dir)

    assert result["verification_status"] == "INCOMPLETE"
    assert result["failure_reason"] == "incomplete_run_directory"


def test_replay_mismatch_fails_closed_on_modified_orchestrator_result(tmp_path):
    run_dir = _completed_run_dir(tmp_path)
    replay_dir = tmp_path / "replay-run"
    module = __import__("research_lab.execution.orchestrator_run_verifier_replay_v1", fromlist=["run_isolated_orchestrator_runner"])
    original = module.run_isolated_orchestrator_runner

    def mismatching_runner(bundle_request, *, output_dir):
        report = original(bundle_request, output_dir=output_dir)
        orchestrator_result_path = Path(output_dir) / "orchestrator_result.json"
        payload = json.loads(orchestrator_result_path.read_text(encoding="utf-8"))
        payload["final_status"] = "REJECTED"
        orchestrator_result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    module.run_isolated_orchestrator_runner = mismatching_runner
    try:
        result = verify_orchestrator_run_directory(run_dir, replay_output_dir=replay_dir)
    finally:
        module.run_isolated_orchestrator_runner = original

    assert result["verification_status"] == "REPLAY_MISMATCH"
    assert result["failure_reason"] == "replay_mismatch"
    assert result["replay_semantic_match"] is False
