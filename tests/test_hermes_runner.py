import json
from datetime import datetime, timezone

import pytest

from research_lab.hermes.providers import ProviderResult
from research_lab.hermes.run_hypothesis_generation import main, run_hypothesis_generation


NOW = datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc)


def _valid(title="Conservative trend cap"):
    return {
        "title": title,
        "family": "LONGTERM",
        "builder": "long_term_vol_target_cap",
        "rationale": "Reduce drawdown before seeking return.",
        "parameters": {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.65},
        "risk_controls": {
            "volatility_targeting": "target portfolio volatility",
            "drawdown_circuit_breakers": "move to cash after drawdown threshold",
            "cash_defensive_regimes": "hold cash in risk-off regimes",
            "exposure_caps": "cap gross and single-asset exposure",
            "correlation_aware_portfolio_risk": "avoid correlated sleeves",
            "crisis_period_diagnostics": "test crisis windows",
            "cost_slippage_stress": "double cost stress",
            "parameter_neighborhood_stability": "test adjacent parameters",
        },
        "tags": ["risk-first"],
    }


def _write_report(root):
    path = root / "reports" / "daily" / "2026-06-11.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Daily Research Report\n- biggest risk discovered: unseen max drawdown exceeds 15%\n",
        encoding="utf-8",
    )
    return path


def test_provider_unavailable_writes_artifact_and_keeps_queue_unchanged(tmp_path):
    _write_report(tmp_path)
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text('{"hypothesis_id":"existing"}\n', encoding="utf-8")
    before = queue.read_bytes()

    outcome = run_hypothesis_generation(
        tmp_path,
        env={},
        timestamp=NOW,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult("provider_unavailable", message="missing provider"),
    )

    assert outcome["status"] == "provider_unavailable"
    assert queue.read_bytes() == before
    artifact = json.loads(outcome["artifact_path"].read_text(encoding="utf-8"))
    assert artifact["generated_hypotheses_count"] == 0
    assert artifact["imported_hypotheses_count"] == 0
    assert artifact["rejected_hypotheses_count"] == 0
    assert artifact["rejection_reasons"] == ["missing provider"]


def test_prompt_contains_latest_diagnostics_and_schema(tmp_path):
    report = _write_report(tmp_path)
    prompts = []

    def provider(_name, prompt, _env):
        prompts.append(prompt)
        return ProviderResult("ok", output=json.dumps({"hypotheses": []}))

    outcome = run_hypothesis_generation(tmp_path, env={"HERMES_PROVIDER": "command"}, timestamp=NOW, provider_invoker=provider)

    assert str(report.relative_to(tmp_path)).replace("\\", "/") in prompts[0]
    assert "unseen max drawdown exceeds 15%" in prompts[0]
    assert "long_term_vol_target_cap" in prompts[0]
    assert outcome["status"] == "ok"


def test_valid_output_imports_only_valid_nonduplicate_hypotheses(tmp_path):
    _write_report(tmp_path)
    malformed = _valid("Unsafe")
    malformed["builder"] = "arbitrary_python"
    output = json.dumps({"hypotheses": [_valid(), malformed, _valid("Same execution, new title")]})

    outcome = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "openai_compatible"},
        timestamp=NOW,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult("ok", output=output),
    )

    queue_rows = [json.loads(line) for line in (tmp_path / "registry" / "hypothesis_queue.jsonl").read_text().splitlines()]
    assert outcome["status"] == "completed_with_rejections"
    assert len(queue_rows) == 1
    assert queue_rows[0]["builder"] == "long_term_vol_target_cap"
    assert queue_rows[0]["hermes_provider"] == "openai_compatible"
    assert queue_rows[0]["hermes_run_id"] == outcome["run_id"]
    assert outcome["generated_hypotheses_count"] == 3
    assert outcome["imported_hypotheses_count"] == 1
    assert outcome["rejected_hypotheses_count"] == 2
    assert any("builder_not_allowed" in reason for reason in outcome["rejection_reasons"])
    assert any("duplicate_hypothesis" in reason for reason in outcome["rejection_reasons"])


def test_malformed_provider_envelope_is_rejected_without_queue_write(tmp_path):
    outcome = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "command"},
        timestamp=NOW,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult("ok", output='["not an object"]'),
    )

    assert outcome["status"] == "invalid_output"
    assert not (tmp_path / "registry" / "hypothesis_queue.jsonl").exists()
    assert outcome["rejection_reasons"] == ["provider_output_must_be_object"]


def test_provider_envelope_rejects_unknown_fields_and_too_many_hypotheses(tmp_path):
    unknown = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "command"},
        timestamp=NOW,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult(
            "ok", output=json.dumps({"hypotheses": [], "code": "no"})
        ),
    )
    later = datetime(2026, 6, 12, 2, 0, 1, tzinfo=timezone.utc)
    oversized = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "command"},
        timestamp=later,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult(
            "ok", output=json.dumps({"hypotheses": [_valid(str(index)) for index in range(16)]})
        ),
    )

    assert unknown["status"] == "invalid_output"
    assert unknown["rejection_reasons"] == ["provider_output_unknown_field:code"]
    assert oversized["status"] == "invalid_output"
    assert oversized["rejection_reasons"] == ["provider_output_too_many_hypotheses"]


def test_cli_returns_success_for_audited_provider_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "research_lab.hermes.run_hypothesis_generation.invoke_provider",
        lambda *_args, **_kwargs: ProviderResult("provider_unavailable", message="not configured"),
    )

    assert main([]) == 0


def test_malformed_openai_endpoint_creates_provider_error_artifact(tmp_path):
    outcome = run_hypothesis_generation(
        tmp_path,
        env={
            "HERMES_PROVIDER": "openai_compatible",
            "HERMES_OPENAI_BASE_URL": "not-a-url",
            "HERMES_OPENAI_MODEL": "model",
            "HERMES_OPENAI_API_KEY": "secret-value",
        },
        timestamp=NOW,
    )

    assert outcome["status"] == "provider_error"
    assert outcome["artifact_path"].exists()
    artifact = json.loads(outcome["artifact_path"].read_text(encoding="utf-8"))
    assert artifact["status"] == "provider_error"
    assert "secret-value" not in outcome["artifact_path"].read_text(encoding="utf-8")
    assert not (tmp_path / "registry" / "hypothesis_queue.jsonl").exists()


def test_artifact_write_failure_leaves_queue_unchanged(tmp_path, monkeypatch):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text('{"hypothesis_id":"existing"}\n', encoding="utf-8")
    before = queue.read_bytes()
    monkeypatch.setattr(
        "research_lab.hermes.run_hypothesis_generation.write_run_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("artifact storage unavailable")),
    )

    with pytest.raises(OSError, match="artifact storage unavailable"):
        run_hypothesis_generation(
            tmp_path,
            env={"HERMES_PROVIDER": "command"},
            timestamp=NOW,
            provider_invoker=lambda *_args, **_kwargs: ProviderResult(
                "ok", output=json.dumps({"hypotheses": [_valid()]})
            ),
        )

    assert queue.read_bytes() == before


def test_queue_commit_failure_is_audited_without_partial_import(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text('{"hypothesis_id":"existing"}\n', encoding="utf-8")
    before = queue.read_bytes()
    second = _valid("Second valid hypothesis")
    second["parameters"] = {**second["parameters"], "sma": 150}

    def fail_commit(_path, payloads):
        assert len(payloads) == 2
        raise OSError("atomic replace failed")

    outcome = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "command"},
        timestamp=NOW,
        provider_invoker=lambda *_args, **_kwargs: ProviderResult(
            "ok", output=json.dumps({"hypotheses": [_valid(), second]})
        ),
        queue_committer=fail_commit,
    )

    assert outcome["status"] == "queue_commit_failed"
    assert outcome["imported_hypotheses_count"] == 0
    assert queue.read_bytes() == before
    artifacts = sorted((tmp_path / "reports" / "hermes" / "runs").glob("*/*.json"))
    assert len(artifacts) == 2
    phases = {json.loads(path.read_text(encoding="utf-8"))["artifact_phase"] for path in artifacts}
    assert phases == {"artifact_written", "queue_commit_failed"}


def test_run_id_collision_is_rejected_before_provider_or_queue_write(tmp_path):
    calls = []
    provider = lambda *_args, **_kwargs: calls.append(True) or ProviderResult("ok", output=json.dumps({"hypotheses": [_valid()]}))
    first = run_hypothesis_generation(
        tmp_path,
        env={"HERMES_PROVIDER": "command"},
        timestamp=NOW,
        provider_invoker=provider,
    )
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    before = queue.read_bytes()

    with pytest.raises(FileExistsError):
        run_hypothesis_generation(
            tmp_path,
            env={"HERMES_PROVIDER": "command"},
            timestamp=NOW,
            provider_invoker=provider,
        )

    assert first["artifact_path"].exists()
    assert calls == [True]
    assert queue.read_bytes() == before
