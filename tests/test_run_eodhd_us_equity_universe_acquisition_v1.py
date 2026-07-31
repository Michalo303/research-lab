from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.run_eodhd_us_equity_universe_acquisition_v1 as cli_module
from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import _open_state
from scripts.run_eodhd_us_equity_universe_acquisition_v1 import main


def _request(tmp_path: Path) -> dict[str, object]:
    return {
        "version": "eodhd_us_equity_universe_acquisition_request_v1",
        "acquisition_id": "EODHD-US-EQUITY-2006-2022-V1",
        "output_dir": str((tmp_path / "final").resolve()),
        "provider": "EODHD",
        "approved_host": "eodhd.com",
        "start_date": "2006-01-01",
        "end_date": "2022-12-31",
        "maximum_call_units": 90_000,
        "maximum_attempts_per_request": 2,
        "history_concurrency": 8,
        "timeout_seconds": 90,
        "maximum_symbol_response_bytes": 2_000_000,
        "maximum_bulk_response_bytes": 20_000_000,
        "provenance": {"source": "operator_approved_eodhd_acquisition_v1"},
    }


def _write_request(tmp_path: Path) -> tuple[Path, str]:
    path = (tmp_path / "request.json").resolve()
    body = (json.dumps(_request(tmp_path), indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(body)
    return path, hashlib.sha256(body).hexdigest()


def test_cli_defaults_to_dry_run_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path, request_sha256 = _write_request(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "run_eodhd_us_equity_universe_acquisition_v1",
        lambda request: (_ for _ in ()).throw(AssertionError("execution used")),
    )

    exit_code = main(["--request", str(request_path), "--expected-request-sha256", request_sha256])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "DRY_RUN"
    assert output["maximum_call_units"] == 90_000
    assert output["planned_sealed_oos_rows"] == 0
    assert output["provider_call_units_used"] == 0
    assert not Path(_request(tmp_path)["output_dir"]).exists()
    assert not list(tmp_path.glob("*.partial"))


def test_top_level_research_package_exports_only_the_acquisition_entry_point() -> None:
    from research_lab.research import run_eodhd_us_equity_universe_acquisition_v1

    assert (
        run_eodhd_us_equity_universe_acquisition_v1
        is cli_module.run_eodhd_us_equity_universe_acquisition_v1
    )


def test_cli_rejects_wrong_request_hash_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path, _ = _write_request(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "run_eodhd_us_equity_universe_acquisition_v1",
        lambda request: (_ for _ in ()).throw(AssertionError("execution used")),
    )

    exit_code = main(
        [
            "--request",
            str(request_path),
            "--expected-request-sha256",
            "0" * 64,
            "--execute",
        ]
    )

    assert exit_code == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "REQUEST_VALIDATION_FAILED",
        "version": "eodhd_us_equity_universe_acquisition_cli_result_v1",
    }


def test_execute_atomically_publishes_hash_verified_complete_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path, request_sha256 = _write_request(tmp_path)
    final = Path(_request(tmp_path)["output_dir"])
    staging = final.with_name(".fake.partial")

    def fake_run(request: dict[str, object]) -> dict[str, object]:
        staging.mkdir()
        connection = _open_state(staging / "state.sqlite", "a" * 64)
        connection.close()
        (staging / "raw").mkdir()
        (staging / "raw" / "sample.bin").write_bytes(b"evidence")
        return {
            "status": "DOWNLOAD_COMPLETE_PENDING_SELECTION",
            "staging_dir": str(staging),
            "provider_call_units_used": 43_168,
            "provider_http_requests_used": 22_972,
            "sealed_oos_opened": False,
        }

    def fake_selection(*, staging_root: Path, state_connection):
        manifest = staging_root / "dataset_manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        (staging_root / "membership_report.json").write_text("{}\n", encoding="utf-8")
        (staging_root / "selection_report.json").write_text("{}\n", encoding="utf-8")
        return {
            "status": "POINT_IN_TIME_MANIFEST_COMPLETE",
            "manifest_path": str(manifest),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "selected_instrument_count": 1_234,
            "minimum_development_cross_section": 1_100,
            "median_development_cross_section": 1_300.0,
            "sealed_oos_rows": 0,
        }

    monkeypatch.setattr(cli_module, "run_eodhd_us_equity_universe_acquisition_v1", fake_run)
    monkeypatch.setattr(cli_module, "build_point_in_time_qlib_manifest_v1", fake_selection)

    exit_code = main(
        [
            "--request",
            str(request_path),
            "--expected-request-sha256",
            request_sha256,
            "--execute",
        ]
    )

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPLETE"
    assert result["selected_instrument_count"] == 1_234
    assert final.is_dir()
    assert not staging.exists()
    assert (final / "COMPLETE").is_file()
    checksums = json.loads((final / "checksums.json").read_text(encoding="utf-8"))
    assert "COMPLETE" not in checksums["files"]
    for relative, expected in checksums["files"].items():
        assert hashlib.sha256((final / relative).read_bytes()).hexdigest() == expected
    serialized = repr(result) + capsys.readouterr().out
    assert "api_token" not in serialized
    assert "EODHD_API_KEY" not in serialized


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("EODHD_API_KEY_UNAVAILABLE", 3),
        ("CALL_BUDGET_PREFLIGHT_FAILED", 5),
        ("PROVIDER_RESPONSE_INVALID", 6),
        ("STAGING_IO_FAILED", 7),
    ],
)
def test_execute_maps_fail_closed_status_without_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int,
) -> None:
    request_path, request_sha256 = _write_request(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "run_eodhd_us_equity_universe_acquisition_v1",
        lambda request: {
            "status": status,
            "provider_call_units_used": 0,
            "provider_http_requests_used": 0,
        },
    )

    observed = main(
        [
            "--request",
            str(request_path),
            "--expected-request-sha256",
            request_sha256,
            "--execute",
        ]
    )

    assert observed == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status
    assert not Path(_request(tmp_path)["output_dir"]).exists()
