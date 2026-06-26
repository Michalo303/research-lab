from __future__ import annotations

import importlib.util
import sys
import types
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
    call_log: list[Path] = []
    fake_runner = types.ModuleType("research_lab.runner")

    def fake_run_daily_research(root: Path):
        call_log.append(Path(root))
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
    call_log = _install_fake_runner(monkeypatch)
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path), "--preflight-only"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "preflight_only=true" in output
    assert str(tmp_path.resolve()) in output
    assert "entrypoint=research_lab.runner.run_daily_research" in output
    assert "root_exists=True" in output
    assert call_log == []
    _assert_no_runtime_artifacts(tmp_path)


def test_normal_execution_calls_runner_once(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    call_log = _install_fake_runner(monkeypatch, result=["candidate-a", "candidate-b"])
    module = _load_script_module()

    exit_code = module.main(["--root", str(tmp_path)])

    assert exit_code == 0
    assert call_log == [tmp_path]
    output = capsys.readouterr().out
    assert "daily research completed: 2 experiments" in output
