from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import types
import logging
import urllib.request
from pathlib import Path

import pandas as pd
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_daily_research.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("run_daily_research_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_fake_runner(monkeypatch: pytest.MonkeyPatch, *, result=None):
    call_log: list[tuple[Path, dict]] = []
    fake_runner = types.ModuleType("research_lab.runner")

    def fake_run_daily_research(root: Path, **kwargs):
        call_log.append((Path(root), kwargs))
        return [] if result is None else result

    fake_runner.run_daily_research = fake_run_daily_research

    import research_lab

    monkeypatch.setitem(sys.modules, "research_lab", research_lab)
    monkeypatch.setitem(sys.modules, "research_lab.runner", fake_runner)
    return call_log


def _assert_no_runtime_artifacts(root: Path) -> None:
    assert not (root / "reports" / "daily").exists()
    assert not (root / "reports" / "runs").exists()
    assert not (root / "registry" / "hypothesis_results.jsonl").exists()


def _write_recovery_cache(root: Path):
    symbols = ["SPY", "QQQ", "IWM", "IEF", "TLT", "GLD", "SHY"]
    index = pd.bdate_range("2020-01-02", periods=4)
    frames = {
        symbol: pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [101.0, 102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [100.5, 101.5, 102.5, 103.5],
                "volume": [1000.0, 1100.0, 1200.0, 1300.0],
            },
            index=index,
        )
        for symbol in symbols
    }
    panel = pd.concat(frames, axis=1)
    csv_path = root / "data" / "processed" / "eodhd_daily_universe.csv"
    manifest_path = root / "data" / "manifests" / "daily_universe.json"
    csv_path.parent.mkdir(parents=True)
    manifest_path.parent.mkdir(parents=True)
    panel.to_csv(csv_path)
    manifest_path.write_text(
        json.dumps(
            {
                "source": "eodhd",
                "provider": "eodhd",
                "symbols": symbols,
                "rows": len(panel),
                "start": str(index.min()),
                "end": str(index.max()),
                "stored_csv": str(csv_path),
                "fallback_used": False,
            }
        ),
        encoding="utf-8",
    )
    return csv_path, manifest_path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kv_lines(output: str) -> dict[str, list[str]]:
    pairs: dict[str, list[str]] = {}
    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        pairs.setdefault(key, []).append(value)
    return pairs


def test_help_exits_without_running_daily_research(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    assert "--preflight-only" in output
    assert call_log == []
    _assert_no_runtime_artifacts(tmp_path)


def test_preflight_only_exits_without_running_daily_research(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_LAB_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path), "--preflight-only"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "preflight_only=true" in output
    assert str(tmp_path.resolve()) in output
    assert "entrypoint=research_lab.runner.run_daily_research" in output
    assert "root_exists=True" in output
    assert "data_provider=synthetic" in output
    assert "eodhd_credentials_present=false" in output
    assert "manual_cli_loads_dotenv=false" in output
    assert "systemd_service_loads_environmentfile=if_configured" in output
    assert call_log == []
    _assert_no_runtime_artifacts(tmp_path)


def test_preflight_only_does_not_print_eodhd_secret_value(tmp_path, monkeypatch, capsys):
    secret = "super-secret-eodhd-token"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd")
    monkeypatch.setenv("EODHD_API_KEY", secret)
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path), "--preflight-only"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "data_provider=eodhd" in output
    assert "eodhd_credentials_present=true" in output
    assert secret not in output
    assert call_log == []
    _assert_no_runtime_artifacts(tmp_path)


def test_recovery_cache_preflight_is_read_only_provider_free_and_machine_readable(tmp_path, monkeypatch, capsys):
    csv_path, manifest_path = _write_recovery_cache(tmp_path)
    before = (_hash(csv_path), _hash(manifest_path))
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    monkeypatch.setenv("RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK", "0")
    monkeypatch.delenv("EODHD_API_KEY", raising=False)

    def blocked(*args, **kwargs):
        raise AssertionError("provider or execution path invoked")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr("research_lab.data_eodhd.fetch_eodhd_eod", blocked)
    monkeypatch.setattr("research_lab.data._fetch_massive_daily", blocked)
    monkeypatch.setattr("research_lab.data.load_cached_eodhd_daily_universe", blocked)
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "1"]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    pairs = _kv_lines(output)
    for line in (
        "recovery_preflight=true",
        "data_provider=eodhd_cache",
        "provider_calls=0",
        "blocker_reason=none",
        "ready=true",
        "recovery_day=1",
        "recovery_target=4",
        "recovery_selected_new=4",
        "recovery_selected_for_new_execution=4",
        "recovery_covered_by_recent_real=0",
        "recovery_historically_covered=0",
        "recovery_nonqualifying_recent_matches=0",
        "recovery_resolved=4",
        "recovery_shortfall=0",
        "recovery_unresolved=0",
    ):
        assert line in output
    assert pairs["blocker_reason"] == ["none"]
    assert (_hash(csv_path), _hash(manifest_path)) == before
    _assert_no_runtime_artifacts(tmp_path)


@pytest.mark.parametrize(
    ("missing_relative", "present_field"),
    [
        ("data/processed/eodhd_daily_universe.csv", "cached_eodhd_csv_present=false"),
        ("data/manifests/daily_universe.json", "cached_eodhd_manifest_present=false"),
    ],
)
def test_recovery_cache_preflight_returns_nonzero_when_cache_artifact_missing(
    tmp_path, monkeypatch, capsys, missing_relative, present_field
):
    _write_recovery_cache(tmp_path)
    (tmp_path / missing_relative).unlink()
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "1"]
    )

    assert exit_code != 0
    output = capsys.readouterr().out
    pairs = _kv_lines(output)
    assert "recovery_preflight=true" in output
    assert present_field in output
    assert "provider_calls=0" in output
    assert "ready=false" in output
    assert pairs["blocker_reason"] == ["cache_metadata_invalid"]
    _assert_no_runtime_artifacts(tmp_path)


@pytest.mark.parametrize("provider", ["synthetic", "eodhd", "yfinance", "auto"])
def test_recovery_preflight_rejects_every_non_cache_provider_without_side_effects(
    tmp_path, monkeypatch, capsys, provider
):
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", provider)

    def blocked(*args, **kwargs):
        raise AssertionError("recovery preflight reached market data or execution")

    monkeypatch.setattr("research_lab.data.load_cached_eodhd_daily_universe", blocked)
    monkeypatch.setattr("research_lab.runner.run_daily_research", blocked)
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "1"]
    )

    assert exit_code != 0
    output = capsys.readouterr().out
    pairs = _kv_lines(output)
    assert "recovery_preflight=true" in output
    assert f"data_provider={provider}" in output
    assert "provider_calls=0" in output
    assert "ready=false" in output
    assert pairs["blocker_reason"] == ["invalid_data_provider"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("day", [1, 7])
def test_bounded_recovery_preflight_boundary_still_rejects_non_cache_provider(
    tmp_path, monkeypatch, capsys, day
):
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", str(day)]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "recovery_preflight=true" in output
    assert "blocker_reason=invalid_data_provider" in output


def test_day_eight_recovery_preflight_with_non_cache_provider_matches_normal_preflight(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    reached = []

    def blocked(name):
        def fail(*args, **kwargs):
            reached.append(name)
            raise AssertionError(f"day 8 preflight should not reach {name}")
        return fail

    monkeypatch.setattr("research_lab.runner.select_daily_candidates", blocked("select_daily_candidates"))
    monkeypatch.setattr("research_lab.data.validate_cached_eodhd_daily_universe_metadata", blocked("validate_cached_eodhd_daily_universe_metadata"))
    monkeypatch.setattr("research_lab.data.load_cached_eodhd_daily_universe", blocked("load_cached_eodhd_daily_universe"))
    monkeypatch.setattr("research_lab.runner.run_daily_research", blocked("run_daily_research"))
    module = _load_script_module()

    day_eight_exit = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "8"]
    )
    day_eight_output = capsys.readouterr().out

    normal_exit = module.main(["--root", str(tmp_path), "--preflight-only"])
    normal_output = capsys.readouterr().out

    assert day_eight_exit == normal_exit == 0
    assert day_eight_output == normal_output
    assert "invalid_data_provider" not in day_eight_output
    assert "unresolved_recovery" not in day_eight_output
    assert reached == []


def test_day_eight_recovery_preflight_with_cache_provider_skips_bounded_recovery_resolution(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    reached = []

    def blocked(name):
        def fail(*args, **kwargs):
            reached.append(name)
            raise AssertionError(f"day 8 cache preflight should not reach {name}")
        return fail

    monkeypatch.setattr("research_lab.runner.select_daily_candidates", blocked("select_daily_candidates"))
    monkeypatch.setattr("research_lab.data.validate_cached_eodhd_daily_universe_metadata", blocked("validate_cached_eodhd_daily_universe_metadata"))
    monkeypatch.setattr("research_lab.data.load_cached_eodhd_daily_universe", blocked("load_cached_eodhd_daily_universe"))
    monkeypatch.setattr("research_lab.runner.run_daily_research", blocked("run_daily_research"))
    module = _load_script_module()

    day_eight_exit = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "8"]
    )
    day_eight_output = capsys.readouterr().out

    normal_exit = module.main(["--root", str(tmp_path), "--preflight-only"])
    normal_output = capsys.readouterr().out

    assert day_eight_exit == normal_exit == 1
    assert day_eight_output == normal_output
    assert "recovery_target=" not in day_eight_output
    assert "unresolved_recovery" not in day_eight_output
    assert reached == []


def test_unresolved_recovery_preflight_stops_before_cache_metadata_or_market_data(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    call_order = []

    def unresolved(*args, **kwargs):
        call_order.append("resolve")
        return {
            "specs": [],
            "diagnostics": {
                "selection_mode": "bounded_recovery",
                "recovery_target": 4,
                "selected_new": 1,
                "covered_by_recent_real": 1,
                "nonqualifying_recent_matches": 1,
                "recovery_resolved": 2,
                "recovery_shortfall": 2,
            },
        }

    def blocked(*args, **kwargs):
        call_order.append("blocked")
        raise AssertionError("unresolved preflight reached cache data or execution")

    monkeypatch.setattr("research_lab.runner.select_daily_candidates", unresolved)
    monkeypatch.setattr("research_lab.data.load_cached_eodhd_daily_universe", blocked)
    monkeypatch.setattr("research_lab.runner.run_daily_research", blocked)
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "1"]
    )

    assert exit_code != 0
    assert call_order == ["resolve"]
    output = capsys.readouterr().out
    pairs = _kv_lines(output)
    assert "recovery_historically_covered=1" in output
    assert "recovery_selected_for_new_execution=1" in output
    assert "recovery_unresolved=2" in output
    assert "data_provider=eodhd_cache" in output
    assert "provider_calls=0" in output
    assert "ready=false" in output
    assert pairs["blocker_reason"] == ["unresolved_recovery"]
    assert list(tmp_path.iterdir()) == []


def test_recovery_cache_preflight_returns_nonzero_when_manifest_provenance_is_invalid(tmp_path, monkeypatch, capsys):
    _, manifest_path = _write_recovery_cache(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider"] = "eodhd_cache"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "eodhd_cache")
    module = _load_script_module()

    exit_code = module.main(
        ["--root", str(tmp_path), "--preflight-only", "--recovery-mode", "--recovery-day", "1"]
    )

    assert exit_code != 0
    output = capsys.readouterr().out
    pairs = _kv_lines(output)
    assert "recovery_preflight=true" in output
    assert "provider_calls=0" in output
    assert "ready=false" in output
    assert pairs["blocker_reason"] == ["cache_metadata_invalid"]


def test_normal_execution_calls_runner_once(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    call_log = _install_fake_runner(monkeypatch, result=["candidate-a", "candidate-b"])
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path)])

    assert exit_code == 0
    assert call_log == [(tmp_path, {"recovery_mode": False, "recovery_day": None})]
    output = capsys.readouterr().out
    assert "daily research completed: 2 experiments" in output


def test_explicit_recovery_execution_passes_mode_and_day_to_runner(tmp_path, monkeypatch):
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path), "--recovery-mode", "--recovery-day", "4"])

    assert exit_code == 0
    assert call_log == [(tmp_path, {"recovery_mode": True, "recovery_day": 4})]


@pytest.mark.parametrize("day", [1, 7, 8])
def test_positive_recovery_days_reach_runner(tmp_path, monkeypatch, day):
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()
    assert module.main(["--root", str(tmp_path), "--recovery-mode", "--recovery-day", str(day)]) == 0
    assert call_log == [(tmp_path, {"recovery_mode": True, "recovery_day": day})]


def test_recovery_mode_requires_explicit_day(tmp_path, monkeypatch):
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    with pytest.raises(SystemExit):
        module.main(["--root", str(tmp_path), "--recovery-mode"])
    assert call_log == []


@pytest.mark.parametrize("args", [
    ["--recovery-day", "1"],
    ["--recovery-mode", "--recovery-day", "0"],
    ["--recovery-mode", "--recovery-day", "-1"],
    ["--recovery-mode", "--recovery-day", "not-an-int"],
])
def test_invalid_recovery_arguments_fail_before_runner_import(tmp_path, monkeypatch, args):
    module = _load_script_module()
    reached = []

    def blocked(name):
        def fail(*unused_args, **unused_kwargs):
            reached.append(name)
            raise AssertionError(f"operational entry point reached: {name}")
        return fail

    class PoisonModule(types.ModuleType):
        def __getattr__(self, name):
            entry_point = f"module:{self.__name__}.{name}"
            reached.append(entry_point)
            raise AssertionError(f"operational entry point reached: {entry_point}")

    monkeypatch.setattr(module, "_print_preflight", blocked("preflight"))
    monkeypatch.setattr(logging, "basicConfig", blocked("logging"))
    monkeypatch.setattr(Path, "mkdir", blocked("directory_creation"))
    monkeypatch.setattr(Path, "write_text", blocked("artifact_creation"))
    monkeypatch.setattr(urllib.request, "urlopen", blocked("provider_transport"))
    for name in ("research_lab.runner", "research_lab.config", "research_lab.data", "research_lab.reports"):
        monkeypatch.setitem(sys.modules, name, PoisonModule(name))
    with pytest.raises(SystemExit):
        module.main(["--root", str(tmp_path), *args])
    assert reached == []
    _assert_no_runtime_artifacts(tmp_path)
