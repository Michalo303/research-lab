from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import _canonical_sha256
from research_lab.execution.macro_aware_pilot_runner_v1 import run_macro_aware_pilot
from research_lab.execution.macro_aware_pilot_verifier_replay_v1 import (
    replay_macro_aware_pilot,
    verify_macro_aware_pilot_run,
)
from test_macro_aware_pilot_runner_v1 import _pilot_request


def _directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in path.iterdir()
        if item.is_file()
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _completed_run(tmp_path, monkeypatch) -> Path:
    import research_lab.execution.macro_aware_pilot_runner_v1 as runner_module

    monkeypatch.setattr(runner_module, "PRIVATE_RUN_ROOT", tmp_path)
    run_dir = tmp_path / "run"
    run_macro_aware_pilot(_pilot_request(), output_dir=run_dir)
    return run_dir


def _rehash_result_bundle(run_dir: Path, result: dict[str, object]) -> None:
    result["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in result.items() if key != "output_payload_sha256"}
    )
    _write_json(run_dir / "macro_aware_result.json", result)
    report_path = run_dir / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["acceptance_result_sha256"] = result["output_payload_sha256"]
    report["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in report.items() if key != "output_payload_sha256"}
    )
    _write_json(report_path, report)
    checksums_path = run_dir / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["files"]["macro_aware_result.json"] = _canonical_sha256(result)
    checksums["files"]["run_report.json"] = _canonical_sha256(report)
    _write_json(checksums_path, checksums)


def test_successful_verification_is_read_only(tmp_path, monkeypatch):
    run_dir = _completed_run(tmp_path, monkeypatch)
    before = _directory_hashes(run_dir)

    verification = verify_macro_aware_pilot_run(run_dir)

    assert verification["verification_status"] == "VERIFIED"
    assert verification["verification_read_only"] is True
    assert verification["run_label"] == "SYNTHETIC_MACRO_INTEGRATION_PILOT"
    assert _directory_hashes(run_dir) == before


def test_successful_deterministic_replay_matches_and_verifies(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as runner_module

    monkeypatch.setattr(runner_module, "PRIVATE_RUN_ROOT", tmp_path)
    source_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay"
    run_macro_aware_pilot(_pilot_request(), output_dir=source_dir)
    source_before = _directory_hashes(source_dir)

    replay = replay_macro_aware_pilot(source_dir, replay_output_dir=replay_dir)

    assert replay["replay_status"] == "REPLAY_MATCH"
    assert replay["source_verification_status"] == "VERIFIED"
    assert replay["replay_verification_status"] == "VERIFIED"
    assert replay["run_label"] == "SYNTHETIC_MACRO_INTEGRATION_PILOT"
    assert replay["deterministic_comparison"]["all_match"] is True
    assert verify_macro_aware_pilot_run(replay_dir)["verification_status"] == "VERIFIED"
    assert _directory_hashes(source_dir) == source_before


@pytest.mark.parametrize(
    ("file_name", "replacement"),
    [
        ("request.json", "not-json\n"),
        ("macro_aware_result.json", "[]\n"),
        ("run_report.json", "null\n"),
        ("checksums.json", "{\"files\": {}}\n"),
        ("COMPLETE", "{\"status\": \"BROKEN\"}\n"),
    ],
)
def test_corrupted_artifacts_fail_validation(tmp_path, monkeypatch, file_name, replacement):
    run_dir = _completed_run(tmp_path, monkeypatch)
    (run_dir / file_name).write_text(replacement, encoding="utf-8")
    result = verify_macro_aware_pilot_run(run_dir)
    assert result["verification_status"] == "FAILED_VALIDATION"


def test_missing_complete_is_incomplete_and_artifact_set_is_exact(tmp_path, monkeypatch):
    run_dir = _completed_run(tmp_path, monkeypatch)
    (run_dir / "COMPLETE").unlink()
    assert verify_macro_aware_pilot_run(run_dir)["verification_status"] == "INCOMPLETE"

    second = tmp_path / "second"
    run_macro_aware_pilot(_pilot_request(), output_dir=second)
    (second / "request.json").unlink()
    assert verify_macro_aware_pilot_run(second)["verification_status"] == "FAILED_VALIDATION"

    third = tmp_path / "third"
    run_macro_aware_pilot(_pilot_request(), output_dir=third)
    (third / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    verification = verify_macro_aware_pilot_run(third)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert verification["exact_artifact_set"] is False

    fourth = tmp_path / "fourth"
    run_macro_aware_pilot(_pilot_request(), output_dir=fourth)
    (fourth / "unexpected-directory").mkdir()
    directory_verification = verify_macro_aware_pilot_run(fourth)
    assert directory_verification["verification_status"] == "FAILED_VALIDATION"
    assert directory_verification["exact_artifact_set"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (lambda result: result.update(version="wrong"), "version mismatch"),
        (
            lambda result: result["lineage"].update(market_data_identity="wrong"),
            "dataset identity mismatch",
        ),
        (lambda result: result["lineage"].update(market_symbol="SYNTH_QQQ"), "symbol mismatch"),
        (
            lambda result: result["lineage"].update(market_data_sha256="0" * 64),
            "market bars sha256 mismatch",
        ),
        (
            lambda result: result["lineage"].update(market_source_artifact_sha256="1" * 64),
            "source artifact sha256 mismatch",
        ),
        (
            lambda result: result["provenance"].update(synthetic_macro_label="wrong"),
            "synthetic macro label mismatch",
        ),
        (
            lambda result: result["no_look_ahead_proof"].update(no_future_release_used=False),
            "no-look-ahead",
        ),
        (
            lambda result: result["baseline_preservation_proof"].update(baseline_unchanged=False),
            "baseline-preservation",
        ),
        (
            lambda result: result["protective_exit_preservation_proof"].update(
                protective_exits_preserved=False
            ),
            "protective-exit-preservation",
        ),
    ],
)
def test_semantically_rehashed_result_tampering_fails_closed(
    tmp_path, monkeypatch, mutation, expected_reason
):
    run_dir = _completed_run(tmp_path, monkeypatch)
    result_path = run_dir / "macro_aware_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    mutation(result)
    result["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in result.items() if key != "output_payload_sha256"}
    )
    _write_json(result_path, result)
    verification = verify_macro_aware_pilot_run(run_dir)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert expected_reason in verification["failure_reason"]


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("provider_calls_used", 1),
        ("network_used", True),
        ("registry_write_performed", True),
        ("broker_actions_used", 1),
        ("paper_trading_performed", True),
        ("deployment_performed", True),
        ("promotion_performed", True),
        ("generated_code_executed", True),
        ("automatic_strategy_application_performed", True),
        ("production_runtime_supported", True),
    ],
)
def test_semantically_rehashed_unsafe_result_flags_fail_closed(
    tmp_path, monkeypatch, field, unsafe_value
):
    run_dir = _completed_run(tmp_path, monkeypatch)
    result_path = run_dir / "macro_aware_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["safety_flags"][field] = unsafe_value
    result["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in result.items() if key != "output_payload_sha256"}
    )
    _write_json(result_path, result)
    verification = verify_macro_aware_pilot_run(run_dir)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert "safety flags" in verification["failure_reason"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: result["macro_snapshot_result"]["safe_flags"].update(network_used=True),
        lambda result: result["alignment_result"]["safety_flags"].update(provider_calls_used=1),
        lambda result: result["feature_set_result"].update(production_runtime_supported=True),
        lambda result: result["macro_regime_candidate_result"].update(
            automatic_strategy_application_performed=True
        ),
        lambda result: result["baseline_strategy_result"]["safe_flags"].update(
            provider_calls_used=1
        ),
        lambda result: result["macro_filter_evaluator_result"].update(generated_code_executed=True),
        lambda result: result["review_artifact"].update(registry_write_performed=True),
        lambda result: result["review_artifact"]["adapter_safety_flags"].update(
            broker_actions_used=1
        ),
    ],
)
def test_rehashed_unsafe_child_artifacts_fail_closed(tmp_path, monkeypatch, mutation):
    run_dir = _completed_run(tmp_path, monkeypatch)
    result = json.loads((run_dir / "macro_aware_result.json").read_text(encoding="utf-8"))
    mutation(result)
    _rehash_result_bundle(run_dir, result)
    verification = verify_macro_aware_pilot_run(run_dir)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert "child safety" in verification["failure_reason"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: result["macro_snapshot_result"].update(snapshot_id="tampered"),
        lambda result: result["alignment_result"]["aligned_bars"][0].update(
            decision_timestamp_utc="2026-01-01T00:00:00Z"
        ),
        lambda result: result["feature_set_result"].update(deterministic_feature_hash="0" * 64),
        lambda result: result["macro_regime_candidate_result"].update(regime_label="tampered"),
        lambda result: result["macro_filter_evaluator_result"].update(classification="tampered"),
        lambda result: result["baseline_strategy_result"]["synthetic_bars"][0].update(close=999.0),
        lambda result: result["review_artifact"]["adapter_result"].update(source_symbol="QQQ"),
    ],
)
def test_rehashed_outer_bundle_cannot_mask_child_integrity_tampering(
    tmp_path, monkeypatch, mutation
):
    run_dir = _completed_run(tmp_path, monkeypatch)
    result = json.loads((run_dir / "macro_aware_result.json").read_text(encoding="utf-8"))
    mutation(result)
    _rehash_result_bundle(run_dir, result)
    verification = verify_macro_aware_pilot_run(run_dir)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert "child integrity" in verification["failure_reason"]


def test_corrupted_run_report_self_hash_and_checksum_manifest_fail(tmp_path, monkeypatch):
    run_dir = _completed_run(tmp_path, monkeypatch)
    report_path = run_dir / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["evaluator_classification"] = "CORRUPTED"
    _write_json(report_path, report)
    assert "run report evaluator_classification mismatch" in verify_macro_aware_pilot_run(run_dir)[
        "failure_reason"
    ]

    second = tmp_path / "second"
    run_macro_aware_pilot(_pilot_request(), output_dir=second)
    checksums_path = second / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["files"]["request.json"] = "0" * 64
    _write_json(checksums_path, checksums)
    verification = verify_macro_aware_pilot_run(second)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert verification["checksums_match"] is False


def test_rehashed_unsafe_run_report_and_unknown_result_fields_fail_closed(tmp_path, monkeypatch):
    run_dir = _completed_run(tmp_path, monkeypatch)
    report_path = run_dir / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["network_used"] = True
    report["output_payload_sha256"] = _canonical_sha256(
        {key: value for key, value in report.items() if key != "output_payload_sha256"}
    )
    _write_json(report_path, report)
    checksums_path = run_dir / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["files"]["run_report.json"] = _canonical_sha256(report)
    _write_json(checksums_path, checksums)
    verification = verify_macro_aware_pilot_run(run_dir)
    assert verification["verification_status"] == "FAILED_VALIDATION"
    assert "run report safety" in verification["failure_reason"]

    second = tmp_path / "second"
    run_macro_aware_pilot(_pilot_request(), output_dir=second)
    result = json.loads((second / "macro_aware_result.json").read_text(encoding="utf-8"))
    result["unexpected"] = "field"
    _rehash_result_bundle(second, result)
    schema_verification = verify_macro_aware_pilot_run(second)
    assert schema_verification["verification_status"] == "FAILED_VALIDATION"
    assert "result schema" in schema_verification["failure_reason"]


def test_replay_returns_mismatch_for_a_verified_deterministic_difference(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as runner_module
    import research_lab.execution.macro_aware_pilot_verifier_replay_v1 as verifier_module

    monkeypatch.setattr(runner_module, "PRIVATE_RUN_ROOT", tmp_path)
    source_dir = tmp_path / "run"
    run_macro_aware_pilot(_pilot_request(), output_dir=source_dir)
    original_run = verifier_module.run_macro_aware_pilot

    def altered_replay(request, *, output_dir):
        report = original_run(request, output_dir=output_dir)
        replay_dir = Path(output_dir)
        request_path = replay_dir / "request.json"
        replay_request = json.loads(request_path.read_text(encoding="utf-8"))
        replay_request["created_at"] = "2026-01-12T00:00:00Z"
        _write_json(request_path, replay_request)
        report_path = replay_dir / "run_report.json"
        replay_report = json.loads(report_path.read_text(encoding="utf-8"))
        replay_report["created_at"] = replay_request["created_at"]
        replay_report["output_payload_sha256"] = _canonical_sha256(
            {key: value for key, value in replay_report.items() if key != "output_payload_sha256"}
        )
        _write_json(report_path, replay_report)
        checksums_path = replay_dir / "checksums.json"
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        checksums["files"]["request.json"] = _canonical_sha256(replay_request)
        checksums["files"]["run_report.json"] = _canonical_sha256(replay_report)
        _write_json(checksums_path, checksums)
        return report

    monkeypatch.setattr(verifier_module, "run_macro_aware_pilot", altered_replay)
    replay = replay_macro_aware_pilot(source_dir, replay_output_dir=tmp_path / "replay")
    assert replay["replay_status"] == "REPLAY_MISMATCH"
    assert replay["replay_verification_status"] == "VERIFIED"
    assert replay["deterministic_comparison"]["request_hash"] is False
