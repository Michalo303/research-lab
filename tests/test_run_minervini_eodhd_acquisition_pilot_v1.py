from __future__ import annotations

from research_lab import research
from scripts.run_minervini_eodhd_acquisition_pilot_v1 import main


def test_cli_defaults_to_zero_network_zero_write(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status=DRY_RUN",
        "planned_provider_requests=24",
        "writes_performed=False",
    ]


def test_live_cli_requires_absolute_output_and_exact_acknowledgement(
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


def test_live_cli_rejects_nonempty_output_before_executor(tmp_path, capsys):
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


def test_public_api_exports_acquisition_pilot_contracts():
    expected = {
        "build_minervini_eodhd_acquisition_plan_v1",
        "replay_minervini_pilot_artifacts_v1",
        "run_minervini_eodhd_acquisition_pilot_v1",
    }

    assert expected <= set(research.__all__)
    assert all(callable(getattr(research, name)) for name in expected)
