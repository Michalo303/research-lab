from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from research_lab.execution.isolated_orchestrator_runner_v1 import (
    CHECKSUMS_VERSION,
    RUN_REPORT_VERSION,
    run_isolated_orchestrator_runner,
)
from tests.test_orchestrator_run_bundle_contract_v1 import _request as _bundle_request


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "isolated_orchestrator_runner_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "run_isolated_orchestrator_runner.py"


def _request() -> dict[str, object]:
    return copy.deepcopy(_bundle_request())


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("isolated_orchestrator_runner_cli", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_successful_run_writes_expected_files_and_returns_deterministic_report(tmp_path):
    first_dir = tmp_path / "first-run"
    second_dir = tmp_path / "second-run"

    first = run_isolated_orchestrator_runner(_request(), output_dir=first_dir)
    second = run_isolated_orchestrator_runner(_request(), output_dir=second_dir)

    assert first == second
    assert first["version"] == RUN_REPORT_VERSION
    assert first["runner_version"] == "isolated_orchestrator_runner_v1"
    assert first["execution_status"] == "completed"
    assert first["failure_reason"] is None
    assert first["final_status"] == "ACCEPTED_REVIEW_ONLY"
    assert first["run_directory_complete"] is True
    assert first["execution_authority_granted"] is False
    assert first["persistence_authority_granted"] is False
    assert first["provider_calls_used"] == 0
    assert first["registry_write_performed"] is False
    assert first["promotion_performed"] is False

    expected_files = {
        "bundle_manifest.json",
        "checksums.json",
        "orchestrator_result.json",
        "request.json",
        "run_report.json",
    }
    assert {path.name for path in first_dir.iterdir() if path.is_file()} == expected_files
    assert not (first_dir / ".orchestrator-run-staging").exists()
    assert not (first_dir / ".run.lock").exists()

    checksums = json.loads((first_dir / "checksums.json").read_text(encoding="utf-8"))
    assert checksums["version"] == CHECKSUMS_VERSION
    assert set(checksums["files"]) == {
        "bundle_manifest.json",
        "orchestrator_result.json",
        "request.json",
        "run_report.json",
    }
    assert json.loads((first_dir / "run_report.json").read_text(encoding="utf-8")) == first


def test_refuses_non_empty_output_dir_and_completed_run(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "extra.txt").write_text("blocked\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        run_isolated_orchestrator_runner(_request(), output_dir=occupied)

    completed = tmp_path / "completed"
    run_isolated_orchestrator_runner(_request(), output_dir=completed)
    with pytest.raises(ValueError, match="completed"):
        run_isolated_orchestrator_runner(_request(), output_dir=completed)


def test_refuses_unsafe_output_dir_and_existing_lock(tmp_path):
    with pytest.raises(ValueError, match="unsafe_output_dir"):
        run_isolated_orchestrator_runner(_request(), output_dir=ROOT / "unsafe-run-dir")

    locked = tmp_path / "locked-run"
    locked.mkdir()
    (locked / ".run.lock").write_text("held\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lock"):
        run_isolated_orchestrator_runner(_request(), output_dir=locked)


def test_failure_before_finalization_leaves_explicit_incomplete_staging_dir(tmp_path, monkeypatch):
    output_dir = tmp_path / "failed-run"
    module = sys.modules["research_lab.execution.isolated_orchestrator_runner_v1"]
    original = module._write_verified_json

    def failing_writer(path, payload):
        if path.name == "orchestrator_result.json":
            raise OSError("forced write failure")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_verified_json", failing_writer)

    with pytest.raises(OSError, match="forced write failure"):
        run_isolated_orchestrator_runner(_request(), output_dir=output_dir)

    staging_dir = output_dir / ".orchestrator-run-staging"
    marker = staging_dir / "incomplete.json"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["execution_status"] == "incomplete"
    assert not (output_dir / "run_report.json").exists()
    assert not (output_dir / ".run.lock").exists()


def test_cli_reads_request_file_and_writes_output_dir(tmp_path):
    input_path = tmp_path / "request.json"
    output_dir = tmp_path / "out"
    input_path.write_text(json.dumps(_request(), indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output-dir", str(output_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert payload["version"] == RUN_REPORT_VERSION
    assert payload["execution_status"] == "completed"
    assert (output_dir / "run_report.json").exists()


def test_cli_refuses_external_protected_root_and_symlink_traversal(tmp_path, monkeypatch):
    input_path = tmp_path / "request.json"
    input_path.write_text(json.dumps(_request(), indent=2) + "\n", encoding="utf-8")

    cli_module = _load_cli_module()
    external_protected_root = tmp_path / "external-protected-root"
    external_protected_root.mkdir()

    monkeypatch.setattr(
        cli_module,
        "_protected_output_roots",
        lambda repo_root: [repo_root.resolve(), external_protected_root.resolve()],
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = cli_module.main(["--input", str(input_path), "--output-dir", str(external_protected_root / "run")])
    protected_result = json.loads(stdout.getvalue().strip())
    assert exit_code == cli_module.EXIT_UNSAFE_OUTPUT_DIR
    assert protected_result["failure_reason"] == "unsafe_output_dir"

    symlink_root = tmp_path / "symlink-root"
    symlink_root.mkdir()
    symlink_path = symlink_root / "linked-protected"
    try:
        symlink_path.symlink_to(external_protected_root, target_is_directory=True)
        symlink_target = symlink_path / "child-run"
    except OSError:
        monkeypatch.setattr(cli_module, "_resolved_existing_parent", lambda _path: external_protected_root.resolve())
        symlink_target = symlink_root / "apparent-safe" / "child-run"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = cli_module.main(["--input", str(input_path), "--output-dir", str(symlink_target)])
    symlink_result = json.loads(stdout.getvalue().strip())
    assert exit_code == cli_module.EXIT_UNSAFE_OUTPUT_DIR
    assert symlink_result["failure_reason"] == "unsafe_output_dir"


def test_module_and_cli_do_not_import_provider_registry_or_runtime_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "research_lab.llm",
        "requests",
        "aiohttp",
        "urllib",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for import_name in imports:
            assert not any(
                import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
                for forbidden_root in forbidden_roots
            ), f"{path.name} imported forbidden module {import_name}"
