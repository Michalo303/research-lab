from __future__ import annotations

import json

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import _canonical_sha256
from test_macro_aware_pilot_request_builder_v1 import _snapshot_payload
from test_macro_aware_pilot_runner_v1 import _pilot_request


def test_execution_package_exports_only_required_pilot_operations():
    from research_lab import execution

    assert execution.run_macro_aware_pilot is not None
    assert execution.verify_macro_aware_pilot_run is not None
    assert execution.replay_macro_aware_pilot is not None


def test_cli_run_verify_and_replay(tmp_path, monkeypatch, capsys):
    import research_lab.execution.macro_aware_pilot_runner_v1 as runner_module
    import scripts.run_macro_aware_pilot_v1 as cli

    monkeypatch.setattr(runner_module, "PRIVATE_RUN_ROOT", tmp_path)
    request_path = tmp_path / "pilot-request.json"
    request_path.write_text(json.dumps(_pilot_request()), encoding="utf-8")
    run_dir = tmp_path / "run"
    replay_dir = tmp_path / "replay"

    assert cli.main(["run", "--input", str(request_path), "--output-dir", str(run_dir)]) == 0
    run_output = json.loads(capsys.readouterr().out)
    assert run_output["execution_status"] == "COMPLETED"

    assert cli.main(["verify", "--run-dir", str(run_dir)]) == 0
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["verification_status"] == "VERIFIED"

    assert cli.main(
        ["replay", "--source-run-dir", str(run_dir), "--output-dir", str(replay_dir)]
    ) == 0
    replay_output = json.loads(capsys.readouterr().out)
    assert replay_output["replay_status"] == "REPLAY_MATCH"


def test_cli_emits_bounded_failure_for_invalid_input(tmp_path, capsys):
    import scripts.run_macro_aware_pilot_v1 as cli

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    assert cli.main(["run", "--input", str(invalid), "--output-dir", str(tmp_path / "run")]) != 0
    output = json.loads(capsys.readouterr().out)
    assert output["execution_status"] == "FAILED_VALIDATION"
    assert output["production_runtime_supported"] is False


def test_cli_prepares_fixed_controlled_pilot_request(tmp_path, monkeypatch, capsys):
    import research_lab.execution.macro_aware_pilot_request_builder_v1 as builder_module
    import scripts.run_macro_aware_pilot_v1 as cli

    snapshot = _snapshot_payload()
    snapshot_path = tmp_path / "normalized_ohlcv.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(builder_module, "PRIVATE_RUN_ROOT", tmp_path)
    monkeypatch.setattr(builder_module, "EXPECTED_SNAPSHOT_SHA256", _canonical_sha256(snapshot))
    monkeypatch.setattr(builder_module, "EXPECTED_ROW_COUNT", 8)
    monkeypatch.setattr(builder_module, "EXPECTED_FIRST_TIMESTAMP", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(builder_module, "EXPECTED_LAST_TIMESTAMP", "2026-01-10T00:00:00Z")
    output_path = tmp_path / "request.json"

    assert cli.main(
        [
            "prepare",
            "--market-snapshot",
            str(snapshot_path),
            "--request-output",
            str(output_path),
            "--run-id",
            "cli-pilot-test",
            "--created-at",
            "2026-01-11T00:00:00Z",
        ]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["request_status"] == "PREPARED"
    assert output_path.exists()
