from __future__ import annotations

import hashlib
import json
from pathlib import Path

import scripts.run_massive_fundamental_edge_discovery_v1 as cli_module


def test_cli_dry_run_does_not_execute_or_write(tmp_path: Path, monkeypatch, capsys) -> None:
    request_path = (tmp_path / "request.json").resolve()
    request_path.write_text("{}\n", encoding="utf-8")
    expected = hashlib.sha256(request_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        cli_module,
        "build_massive_fundamental_edge_discovery_plan_v1",
        lambda _: {
            "version": "massive_fundamental_edge_discovery_plan_v1",
            "pilot_id": "P1",
            "planned_trial_count": 10,
            "planned_hypothesis_count": 10,
            "provider_calls_used": 0,
            "sealed_oos_opened": False,
        },
    )
    monkeypatch.setattr(
        cli_module,
        "run_massive_fundamental_edge_discovery_v1",
        lambda _: (_ for _ in ()).throw(AssertionError("execution used")),
    )

    exit_code = cli_module.main(["--request", str(request_path), "--expected-request-sha256", expected])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "DRY_RUN"
    assert result["planned_trial_count"] == 10
    assert result["provider_calls_used"] == 0


def test_result_publication_is_atomic_and_checksum_complete(tmp_path: Path) -> None:
    output = (tmp_path / "output").resolve()
    request = {"output_dir": str(output)}
    result = {
        "pilot_id": "P1",
        "status": "NO_FUNDAMENTAL_EDGE",
        "canonical_result_sha256": "a" * 64,
        "updated_ledger": {"canonical_ledger_sha256": "b" * 64},
        "economic_scorecard": {"status": "NO_FUNDAMENTAL_EDGE"},
        "fundamental_screen": {"status": "COMPLETED"},
        "new_trial_count": 10,
        "new_hypothesis_count": 10,
        "accounting_complete": True,
        "provider_calls_used": 0,
        "sealed_oos_opened": False,
    }

    public = cli_module._publish_result_bundle(request, b"{}\n", result)

    assert public["status"] == "NO_FUNDAMENTAL_EDGE"
    assert output.is_dir()
    assert (output / "COMPLETE").read_text().strip() == "a" * 64
    checksums = json.loads((output / "checksums.json").read_text())
    assert checksums["record_count"] == len(checksums["records"])
    for record in checksums["records"]:
        assert hashlib.sha256((output / record["path"]).read_bytes()).hexdigest() == record["sha256"]
