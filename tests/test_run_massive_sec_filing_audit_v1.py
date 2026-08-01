from __future__ import annotations

import hashlib
import json
from pathlib import Path

import scripts.run_massive_sec_filing_audit_v1 as cli_module


def test_cli_dry_run_never_executes_sec_request(tmp_path: Path, monkeypatch, capsys) -> None:
    request_path = (tmp_path / "request.json").resolve()
    request_path.write_text("{}\n", encoding="utf-8")
    expected = hashlib.sha256(request_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        cli_module,
        "build_massive_sec_filing_audit_plan_v1",
        lambda _: {
            "version": "massive_sec_filing_audit_plan_v1",
            "audit_id": "A1",
            "sample_size": 30,
            "provider_http_requests_used": 0,
            "sealed_oos_opened": False,
        },
    )
    monkeypatch.setattr(
        cli_module,
        "run_massive_sec_filing_audit_v1",
        lambda _: (_ for _ in ()).throw(AssertionError("network audit executed")),
    )

    exit_code = cli_module.main(["--request", str(request_path), "--expected-request-sha256", expected])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "DRY_RUN"
    assert result["sample_size"] == 30
    assert result["provider_http_requests_used"] == 0
