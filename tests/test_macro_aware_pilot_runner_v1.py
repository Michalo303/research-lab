from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import _canonical_sha256
from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.macro_aware_pilot_runner_v1 import (
    PILOT_RUN_LABEL,
    run_macro_aware_pilot,
)
from test_e2e_macro_aware_research_acceptance_v1 import _expected_hashes, _request


def _acceptance_request() -> dict[str, object]:
    request = _request()
    request["provenance"] = {
        "source": "unit_test",
        "run_label": PILOT_RUN_LABEL,
        "synthetic_macro_label": "SYNTHETIC_MACRO_TEST_V1",
    }
    for series in request["macro_series_requests"]:
        series["provider"] = "SYNTHETIC"
        series["provenance"] = {
            "source": "unit_test",
            "run_label": PILOT_RUN_LABEL,
            "synthetic_macro_label": "SYNTHETIC_MACRO_TEST_V1",
        }
    for definition in request["macro_feature_request"]["feature_definitions"]:
        if "source_series_id" in definition:
            definition["source_series_id"] = definition["source_series_id"].replace(
                "FRED:", "SYNTHETIC:"
            )
        if "left_source_series_id" in definition:
            definition["left_source_series_id"] = definition["left_source_series_id"].replace(
                "FRED:", "SYNTHETIC:"
            )
        if "right_source_series_id" in definition:
            definition["right_source_series_id"] = definition["right_source_series_id"].replace(
                "FRED:", "SYNTHETIC:"
            )
    request["expected_hashes"] = _expected_hashes(request)
    return request


def _pilot_request() -> dict[str, object]:
    acceptance_request = _acceptance_request()
    market_adapter = build_isolated_real_data_adapter_contract(
        copy.deepcopy(acceptance_request["market_data_request"])
    )
    return {
        "version": "macro_aware_pilot_run_request_v1",
        "run_id": "synthetic-macro-pilot-test-1",
        "run_label": PILOT_RUN_LABEL,
        "acceptance_request": acceptance_request,
        "expected_acceptance_request_sha256": _canonical_sha256(acceptance_request),
        "expected_market_dataset_identity": acceptance_request["expected_identities"]["market_data_identity"],
        "expected_market_symbol": acceptance_request["market_data_request"]["symbol"].upper(),
        "expected_market_bars_sha256": acceptance_request["expected_hashes"]["market_data_sha256"],
        "expected_market_source_artifact_sha256": market_adapter["output_payload_sha256"],
        "expected_synthetic_macro_label": "SYNTHETIC_MACRO_TEST_V1",
        "created_at": "2026-01-11T00:00:00Z",
        "provenance": {"source": "unit_test"},
    }


def test_successful_isolated_run_writes_exact_artifacts_without_mutating_inputs(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    output_dir = tmp_path / "run"
    request = _pilot_request()
    original_request = copy.deepcopy(request)
    original_market_input = copy.deepcopy(request["acceptance_request"]["market_data_request"])

    report = run_macro_aware_pilot(request, output_dir=output_dir)

    assert report["execution_status"] == "COMPLETED"
    assert report["run_label"] == PILOT_RUN_LABEL
    assert report["provider_calls_used"] == 0
    assert report["network_used"] is False
    assert report["production_runtime_supported"] is False
    assert request == original_request
    assert request["acceptance_request"]["market_data_request"] == original_market_input
    assert {path.name for path in output_dir.iterdir()} == {
        "request.json",
        "macro_aware_result.json",
        "run_report.json",
        "checksums.json",
        "COMPLETE",
    }
    assert json.loads((output_dir / "COMPLETE").read_text(encoding="utf-8")) == {
        "run_id": request["run_id"],
        "status": "COMPLETE",
        "version": "macro_aware_pilot_complete_v1",
    }


def test_rejects_existing_destinations_overwrite_symlinks_and_path_escape(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        run_macro_aware_pilot(_pilot_request(), output_dir=empty)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh"):
        run_macro_aware_pilot(_pilot_request(), output_dir=nonempty)

    completed = tmp_path / "completed"
    run_macro_aware_pilot(_pilot_request(), output_dir=completed)
    with pytest.raises(ValueError, match="fresh"):
        run_macro_aware_pilot(_pilot_request(), output_dir=completed)

    symlink = tmp_path / "linked"
    target = tmp_path / "target"
    target.mkdir()
    try:
        symlink.symlink_to(target, target_is_directory=True)
    except OSError:
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == symlink)
        symlink.mkdir()
    with pytest.raises(ValueError, match="symlink"):
        run_macro_aware_pilot(_pilot_request(), output_dir=symlink)

    with pytest.raises(ValueError, match="traversal"):
        run_macro_aware_pilot(_pilot_request(), output_dir=tmp_path / "child" / ".." / "escape")


def test_rejects_repository_hermes_registry_and_outside_private_root(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    rejected = (
        Path.cwd() / "pilot-output",
        Path.cwd() / "registry" / "pilot-output",
        Path("/opt/trading/research-lab/pilot-output"),
        Path("/opt/trading/private/hermes_books/pilot-output"),
        Path.home() / "AppData" / "Local" / "hermes" / "pilot-output",
        tmp_path.parent / "outside-private-root",
    )
    for output_dir in rejected:
        with pytest.raises(ValueError, match="outside permitted"):
            run_macro_aware_pilot(_pilot_request(), output_dir=output_dir)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(run_label="WRONG"), "run_label"),
        (
            lambda request: request.update(expected_acceptance_request_sha256="0" * 64),
            "request_sha256 mismatch",
        ),
        (
            lambda request: request.update(expected_market_dataset_identity="wrong-dataset"),
            "dataset identity mismatch",
        ),
        (lambda request: request.update(expected_market_symbol="QQQ"), "market symbol mismatch"),
        (
            lambda request: request.update(expected_market_bars_sha256="1" * 64),
            "market bars sha256 mismatch",
        ),
        (
            lambda request: request.update(expected_market_source_artifact_sha256="2" * 64),
            "source artifact sha256 mismatch",
        ),
        (lambda request: request.update(expected_synthetic_macro_label=""), "non-empty text"),
        (lambda request: request.update(unexpected=True), "unknown field"),
    ],
)
def test_rejects_invalid_run_request_bindings(tmp_path, monkeypatch, mutate, message):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    request = _pilot_request()
    mutate(request)
    with pytest.raises(ValueError, match=message):
        run_macro_aware_pilot(request, output_dir=tmp_path / "run")


def test_rejects_missing_synthetic_label_and_non_synthetic_provider(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    missing_label = _pilot_request()
    del missing_label["acceptance_request"]["provenance"]["synthetic_macro_label"]
    missing_label["expected_acceptance_request_sha256"] = _canonical_sha256(
        missing_label["acceptance_request"]
    )
    with pytest.raises(ValueError, match="synthetic macro label"):
        run_macro_aware_pilot(missing_label, output_dir=tmp_path / "missing-label")

    provider = _pilot_request()
    provider["acceptance_request"]["macro_series_requests"][0]["provider"] = "FRED"
    provider["expected_acceptance_request_sha256"] = _canonical_sha256(provider["acceptance_request"])
    with pytest.raises(ValueError, match="provider must be SYNTHETIC"):
        run_macro_aware_pilot(provider, output_dir=tmp_path / "provider")


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
def test_rejects_every_unsafe_acceptance_flag(tmp_path, monkeypatch, field, unsafe_value):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    original = module.run_e2e_macro_aware_research_acceptance

    def unsafe(request):
        result = original(request)
        result["safety_flags"][field] = unsafe_value
        result["output_payload_sha256"] = _canonical_sha256(
            {key: value for key, value in result.items() if key != "output_payload_sha256"}
        )
        return result

    monkeypatch.setattr(module, "run_e2e_macro_aware_research_acceptance", unsafe)
    with pytest.raises(ValueError, match="safety flags"):
        run_macro_aware_pilot(_pilot_request(), output_dir=tmp_path / "run")


@pytest.mark.parametrize(
    ("proof_name", "proof_field", "message"),
    [
        ("no_look_ahead_proof", "no_future_release_used", "no-look-ahead"),
        ("baseline_preservation_proof", "baseline_unchanged", "baseline-preservation"),
        (
            "protective_exit_preservation_proof",
            "protective_exits_preserved",
            "protective-exit-preservation",
        ),
    ],
)
def test_rejects_false_required_proofs(tmp_path, monkeypatch, proof_name, proof_field, message):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    original = module.run_e2e_macro_aware_research_acceptance

    def false_proof(request):
        result = original(request)
        result[proof_name][proof_field] = False
        result["output_payload_sha256"] = _canonical_sha256(
            {key: value for key, value in result.items() if key != "output_payload_sha256"}
        )
        return result

    monkeypatch.setattr(module, "run_e2e_macro_aware_research_acceptance", false_proof)
    with pytest.raises(ValueError, match=message):
        run_macro_aware_pilot(_pilot_request(), output_dir=tmp_path / "run")


def test_failure_after_staging_leaves_incomplete_marker_and_no_final_directory(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "run_e2e_macro_aware_research_acceptance",
        lambda request: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )
    output_dir = tmp_path / "run"
    with pytest.raises(RuntimeError, match="forced failure"):
        run_macro_aware_pilot(_pilot_request(), output_dir=output_dir)
    assert not output_dir.exists()
    incomplete = tmp_path / ".run.staging" / "INCOMPLETE"
    assert json.loads(incomplete.read_text(encoding="utf-8"))["execution_status"] == "INCOMPLETE"


def test_complete_is_written_last_and_all_writes_stay_inside_requested_root(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    writes: list[Path] = []
    original_write = module._write_verified_json

    def recording_write(path, payload):
        writes.append(path)
        return original_write(path, payload)

    monkeypatch.setattr(module, "_write_verified_json", recording_write)
    run_macro_aware_pilot(_pilot_request(), output_dir=tmp_path / "run")
    assert writes[-1].name == "COMPLETE"
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in writes)


def test_failed_validation_result_is_rejected(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_runner_v1 as module

    monkeypatch.setattr(module, "PRIVATE_RUN_ROOT", tmp_path)
    original = module.run_e2e_macro_aware_research_acceptance

    def failed(request):
        result = original(request)
        result["status"] = "FAILED_VALIDATION"
        result["output_payload_sha256"] = _canonical_sha256(
            {key: value for key, value in result.items() if key != "output_payload_sha256"}
        )
        return result

    monkeypatch.setattr(module, "run_e2e_macro_aware_research_acceptance", failed)
    with pytest.raises(ValueError, match="FAILED_VALIDATION"):
        run_macro_aware_pilot(_pilot_request(), output_dir=tmp_path / "run")


def test_runner_has_no_arbitrary_dispatch_network_clock_or_random_capability():
    module_path = Path("research_lab/execution/macro_aware_pilot_runner_v1.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {
        "datetime",
        "importlib",
        "random",
        "requests",
        "socket",
        "subprocess",
        "time",
        "urllib",
        "uuid",
    }
    assert imports.isdisjoint(forbidden)
    called_names = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert called_names.count("run_e2e_macro_aware_research_acceptance") == 1
