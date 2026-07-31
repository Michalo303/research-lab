from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import scripts.run_real_qlib_eodhd_edge_discovery_pilot_v1 as cli_module
from scripts.run_real_qlib_eodhd_edge_discovery_pilot_v1 import main


EXPECTED_BUNDLE = {
    "COMPLETE",
    "request.json",
    "dataset_metadata.json",
    "qlib_runtime.json",
    "factor_screen.json",
    "reference_parity.json",
    "economic_scorecard.json",
    "updated_ledger.json",
    "checksums.json",
    "result.json",
}


def _write_request(tmp_path: Path, *, output_dir: str | None = None) -> tuple[Path, dict[str, object], str]:
    request = {
        "version": "real_qlib_eodhd_edge_discovery_request_v1",
        "pilot_id": "QLIB-PV-001",
        "dataset_manifest_path": str((tmp_path / "manifest.json").resolve()),
        "expected_dataset_manifest_sha256": "1" * 64,
        "previous_ledger_path": str((tmp_path / "ledger.json").resolve()),
        "expected_previous_ledger_sha256": "2" * 64,
        "output_dir": output_dir or str((tmp_path / "bundle").resolve()),
        "discovery_interval": {"start": "2006-01-01", "end": "2018-12-31"},
        "development_interval": {"start": "2019-01-01", "end": "2022-12-31"},
        "sealed_oos_interval": {
            "dataset_version": "SEALED-PV-V1",
            "start": "2023-01-01",
            "end": "2026-06-30",
        },
        "universe": {
            "minimum_price": 5.0,
            "minimum_history_sessions": 252,
            "minimum_median_dollar_volume": 10_000_000.0,
            "maximum_instruments": 1500,
        },
        "costs": {
            "base_bps_one_way": 15.0,
            "stress_bps_one_way": 30.0,
            "severe_bps_one_way": 50.0,
        },
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path, request, hashlib.sha256(path.read_bytes()).hexdigest()


def _success_result(status: str = "EDGE_CANDIDATE_FOUND") -> dict[str, object]:
    return {
        "version": "real_qlib_eodhd_edge_discovery_pilot_v1",
        "pilot_id": "QLIB-PV-001",
        "status": status,
        "dataset_metadata": {"version": "dataset", "canonical_metadata_sha256": "3" * 64},
        "qlib_runtime": {"version": "runtime", "qlib_version": "0.9.7"},
        "reference_parity": {"version": "parity", "status": "PASS"},
        "factor_screen": {"version": "screen", "factor_count": 8},
        "economic_scorecard": {"version": "scorecard", "status": status},
        "updated_ledger": {"version": "ledger", "canonical_ledger_sha256": "4" * 64},
        "new_hypothesis_count": 8,
        "new_trial_count": 8,
        "provider_calls_used": 0,
        "broker_calls_used": 0,
        "registry_write_performed": False,
        "deployment_performed": False,
        "knihomol_used": False,
        "rd_agent_used": False,
        "sealed_oos_opened": False,
        "canonical_result_sha256": "5" * 64,
    }


def _args(path: Path, sha256: str, *, execute: bool = False) -> list[str]:
    result = ["--request", str(path.resolve()), "--expected-request-sha256", sha256]
    if execute:
        result.append("--execute")
    return result


def test_cli_defaults_to_dry_run_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path, request, request_sha = _write_request(tmp_path)

    exit_code = main(
        _args(request_path, request_sha),
        pilot_runner=lambda value: pytest.fail("dry run must not execute pilot"),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.splitlines() == [
        "status=DRY_RUN",
        "pilot_id=QLIB-PV-001",
        "planned_factor_count=8",
        "planned_provider_calls=0",
        "planned_sealed_oos_reads=0",
    ]
    assert not Path(request["output_dir"]).exists()


@pytest.mark.parametrize("corruption", ["relative", "existing", "symlink", "hash"])
def test_execute_requires_safe_output_and_exact_request_hash(
    corruption: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle"
    request_path, _, request_sha = _write_request(
        tmp_path,
        output_dir="relative-bundle" if corruption == "relative" else str(output.resolve()),
    )
    if corruption == "existing":
        output.mkdir()
    elif corruption == "symlink":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self.resolve() == output.resolve() or original(self))
    elif corruption == "hash":
        request_sha = "0" * 64

    exit_code = main(
        _args(request_path, request_sha, execute=True),
        pilot_runner=lambda value: pytest.fail("invalid request must not execute pilot"),
    )

    assert exit_code == 4


def test_execute_writes_complete_hash_verified_bundle_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path, request, request_sha = _write_request(tmp_path)
    write_order: list[str] = []
    original_write = cli_module._write_bytes

    def recording_write(path: Path, body: bytes) -> None:
        write_order.append(path.name)
        original_write(path, body)

    monkeypatch.setattr(cli_module, "_write_bytes", recording_write)

    exit_code = main(
        _args(request_path, request_sha, execute=True),
        pilot_runner=lambda value: _success_result(),
    )

    bundle = Path(request["output_dir"])
    assert exit_code == 0
    assert {path.name for path in bundle.iterdir()} == EXPECTED_BUNDLE
    checksums = json.loads((bundle / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in checksums["sha256"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == expected
    assert write_order[-1] == "COMPLETE"
    assert (bundle / "COMPLETE").read_text(encoding="utf-8") == "status=COMPLETE\n"
    assert not list(tmp_path.glob(".bundle.tmp-*"))


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [("EDGE_CANDIDATE_FOUND", 0), ("NO_PRICE_VOLUME_EDGE", 2)],
)
def test_edge_and_no_edge_have_fixed_exit_codes(
    status: str,
    expected_exit: int,
    tmp_path: Path,
) -> None:
    case_root = tmp_path / status
    case_root.mkdir()
    request_path, _, request_sha = _write_request(case_root)

    assert main(
        _args(request_path, request_sha, execute=True),
        pilot_runner=lambda value: _success_result(status),
    ) == expected_exit


def test_runtime_unavailable_returns_exit_3_without_bundle(tmp_path: Path) -> None:
    request_path, request, request_sha = _write_request(tmp_path)

    exit_code = main(
        _args(request_path, request_sha, execute=True),
        pilot_runner=lambda value: {"status": "QLIB_RUNTIME_UNAVAILABLE"},
    )

    assert exit_code == 3
    assert not Path(request["output_dir"]).exists()


def test_validation_failure_is_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path, request, request_sha = _write_request(tmp_path)

    def fail(value: dict[str, object]) -> dict[str, object]:
        raise ValueError("secret-token at C:/private/path")

    exit_code = main(_args(request_path, request_sha, execute=True), pilot_runner=fail)

    output = capsys.readouterr().out
    assert exit_code == 4
    assert output == "reason=VALIDATION_OR_LEDGER_FAILURE\n"
    assert "secret-token" not in output
    assert "private" not in output
    assert not Path(request["output_dir"]).exists()


def test_cli_source_has_no_forbidden_action_imports() -> None:
    source_path = Path(cli_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.lower())
    forbidden = ("provider", "broker", "registry", "deploy", "knihomol", "rdagent", "qlib_isolated_evaluator")
    assert not [name for name in imported if any(token in name for token in forbidden)]
