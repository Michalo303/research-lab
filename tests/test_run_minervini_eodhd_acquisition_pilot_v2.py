from __future__ import annotations

from research_lab import research
from scripts.run_minervini_eodhd_acquisition_pilot_v2 import main


def test_v2_cli_defaults_to_zero_network_zero_write(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status=DRY_RUN",
        "version=minervini_eodhd_acquisition_pilot_v2",
        "planned_provider_requests=24",
        "writes_performed=False",
    ]


def test_v2_live_cli_requires_absolute_output_and_exact_acknowledgement(
    tmp_path, capsys
):
    assert (
        main(
            [
                "--execute-live",
                "--output-dir",
                "relative",
                "--expected-provider-requests",
                "24",
            ]
        )
        == 1
    )
    assert "absolute" in capsys.readouterr().out

    assert (
        main(
            [
                "--execute-live",
                "--output-dir",
                str(tmp_path / "pilot"),
                "--expected-provider-requests",
                "23",
            ]
        )
        == 1
    )
    assert "exactly 24" in capsys.readouterr().out


def test_v2_live_cli_rejects_nonempty_output_before_executor(tmp_path, capsys):
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    (output_dir / "preserve.txt").write_text("preserve", encoding="utf-8")

    assert (
        main(
            [
                "--execute-live",
                "--output-dir",
                str(output_dir),
                "--expected-provider-requests",
                "24",
            ]
        )
        == 1
    )
    assert "empty" in capsys.readouterr().out


def test_public_api_exports_v2_contracts():
    expected = {
        "build_minervini_eodhd_acquisition_plan_v2",
        "run_minervini_eodhd_acquisition_pilot_v2",
        "validate_minervini_symbol_splits_v2",
    }

    assert expected <= set(research.__all__)
    assert all(callable(getattr(research, name)) for name in expected)
