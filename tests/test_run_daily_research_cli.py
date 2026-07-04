from __future__ import annotations

import importlib.util
import sys
import types
import logging
import urllib.request
from pathlib import Path

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
