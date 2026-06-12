# Hermes Scheduled Strategy Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider-neutral, scheduled Hermes pre-daily stage that imports only validated hypotheses mapped to existing strategy builders and records immutable provenance.

**Architecture:** A new `research_lab.hermes` package separates schema validation, provider invocation, artifact handling, and orchestration. Existing queue and reporting code consume the validated provenance without changing promotion or allocation gates. Systemd files are templates only and are not installed by this work.

**Tech Stack:** Python 3.10+, standard-library `subprocess` and `urllib`, JSON/JSONL registries, pytest, systemd unit templates.

---

## File Structure

- Create `research_lab/hermes/schema.py`: builder whitelist and strict parameter validation.
- Create `research_lab/hermes/providers.py`: command and OpenAI-compatible provider adapters.
- Create `research_lab/hermes/artifacts.py`: diagnostic discovery and immutable artifact IO.
- Create `research_lab/hermes/run_hypothesis_generation.py`: CLI and orchestration.
- Modify `research_lab/llm/hypothesis_adapter.py`: diagnostic/schema-aware prompt construction and validated queue payload construction.
- Modify `research_lab/strategies/baselines.py`: execute validated Hermes builder/parameter records and preserve provenance.
- Modify `research_lab/reports.py`: include latest Hermes artifact in daily metadata and markdown.
- Create `ops/systemd/hermes-hypothesis.service` and `.timer`: deployment templates.
- Create `docs/hermes_scheduling.md`: configuration, installation, verification, and rollback.
- Add focused tests under `tests/test_hermes_*.py` and extend reporting/candidate tests.

### Task 1: Strict Hypothesis Schema

**Files:**
- Create: `research_lab/hermes/__init__.py`
- Create: `research_lab/hermes/schema.py`
- Test: `tests/test_hermes_schema.py`

- [ ] **Step 1: Write failing whitelist and parameter tests**

Add tests that call `validate_hypothesis` with a valid `long_term_vol_target_cap` record and assert normalized output. Add separate tests asserting rejection of unknown builders, family mismatch, unknown parameter keys, missing required parameters, unsupported symbols, non-finite numbers, exposure above `1.0`, and rotation without strong explicit risk controls.

```python
result = validate_hypothesis({
    "title": "Conservative trend cap",
    "family": "LONGTERM",
    "builder": "long_term_vol_target_cap",
    "rationale": "Reduce drawdown",
    "parameters": {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.65},
    "risk_controls": strong_risk_controls(),
})
assert result.accepted is True
assert result.hypothesis["parameters"]["max_weight"] == 0.65
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_hermes_schema.py -q`

Expected: import failure because `research_lab.hermes.schema` does not exist.

- [ ] **Step 3: Implement the explicit schemas**

Define `ValidationResult`, `ParameterRule`, `BuilderSchema`, `BUILDER_SCHEMAS`, `validate_hypothesis`, and `schema_prompt_text`. Use only explicit builder entries matching `build_weights`; reject extra keys and normalize symbols to uppercase. Do not use `eval`, dynamic imports, or provider-supplied callables.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_schema.py -q`

Expected: all schema tests pass.

### Task 2: Provider Adapters

**Files:**
- Create: `research_lab/hermes/providers.py`
- Test: `tests/test_hermes_providers.py`

- [ ] **Step 1: Write failing provider tests**

Test missing `HERMES_COMMAND`, command execution with `shell=False` and prompt on stdin, command nonzero exit, valid OpenAI-compatible response extraction, missing remote API key, loopback endpoint without a key, HTTP error redaction, and unsupported provider.

```python
result = invoke_provider("command", "prompt", {"HERMES_COMMAND": "hermes-agent --json"}, run_command=fake_run)
assert result.status == "ok"
assert fake_run.calls[0].kwargs["shell"] is False
assert fake_run.calls[0].kwargs["input"] == "prompt"
```

- [ ] **Step 2: Run provider tests and verify RED**

Run: `python -m pytest tests/test_hermes_providers.py -q`

Expected: import failure because provider adapters do not exist.

- [ ] **Step 3: Implement adapters with standard-library APIs**

Implement `ProviderResult`, `invoke_provider`, `_invoke_command`, and `_invoke_openai_compatible`. Parse the configured command with `shlex.split`, call `subprocess.run(..., shell=False, input=prompt, capture_output=True, text=True, timeout=...)`, and POST JSON using `urllib.request.urlopen`. Never place credentials in returned messages.

- [ ] **Step 4: Run provider tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_providers.py -q`

Expected: all provider tests pass.

### Task 3: Diagnostic Input and Immutable Artifacts

**Files:**
- Create: `research_lab/hermes/artifacts.py`
- Test: `tests/test_hermes_artifacts.py`

- [ ] **Step 1: Write failing artifact tests**

Cover latest immutable daily report selection, daily-report fallback, dominant blocker extraction, exclusive artifact creation, collision refusal, latest-artifact discovery, and absence of secret-looking fields.

```python
path = write_run_artifact(root, artifact, timestamp=fixed_time)
assert path.read_text(encoding="utf-8")
with pytest.raises(FileExistsError):
    write_run_artifact(root, artifact, timestamp=fixed_time)
```

- [ ] **Step 2: Run artifact tests and verify RED**

Run: `python -m pytest tests/test_hermes_artifacts.py -q`

- [ ] **Step 3: Implement deterministic discovery and exclusive writes**

Use targeted `Path.glob` calls, limit diagnostic text size, derive blocker text from known report bullets, and write JSON using mode `x`. Artifact paths must be `reports/hermes/runs/YYYY-MM-DD/<run_id>.json`.

- [ ] **Step 4: Run artifact tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_artifacts.py -q`

### Task 4: Hermes Orchestration and CLI

**Files:**
- Create: `research_lab/hermes/run_hypothesis_generation.py`
- Modify: `research_lab/llm/hypothesis_adapter.py`
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: Write failing orchestration tests**

Test prompt inclusion of latest diagnostics and schema, provider-unavailable artifact with unchanged queue, valid command/openai payload import, malformed envelope rejection, mixed proposal validation, duplicate rejection, immutable artifact counts, and CLI exit behavior.

```python
outcome = run_hypothesis_generation(root, env={}, provider_invoker=fake_unavailable, timestamp=fixed_time)
assert outcome["status"] == "provider_unavailable"
assert not (root / "registry/hypothesis_queue.jsonl").exists()
assert outcome["artifact_path"].exists()
```

- [ ] **Step 2: Run orchestration tests and verify RED**

Run: `python -m pytest tests/test_hermes_runner.py -q`

- [ ] **Step 3: Implement prompt, parse, validate, dedupe, append, and audit flow**

Require a top-level JSON object containing `hypotheses`. Parse the full provider response before writes. Validate each proposal with Task 1, fingerprint accepted execution records, compare against existing queue fingerprints, append only new records with `hermes_run_id` and `hermes_provider`, and always write a sanitized artifact. Provider failure must not call queue append.

- [ ] **Step 4: Run orchestration tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_runner.py -q`

### Task 5: Deterministic Queue Consumption and Provenance

**Files:**
- Modify: `research_lab/strategies/baselines.py`
- Modify: `research_lab/runner.py`
- Test: `tests/test_hermes_queue_mapping.py`
- Test: `tests/test_candidate_generation_guidance.py`

- [ ] **Step 1: Write failing executable mapping tests**

Write queue records with validated `builder` and `parameters`, assert exact `StrategySpec` mapping, provenance preservation, rejection/skip of unwhitelisted records, and unchanged legacy fallback behavior.

- [ ] **Step 2: Run mapping tests and verify RED**

Run: `python -m pytest tests/test_hermes_queue_mapping.py tests/test_candidate_generation_guidance.py -q`

- [ ] **Step 3: Implement validated mapping**

For `llm_generated` Hermes records, revalidate before creating `StrategySpec`; use only the schema-approved builder and parameters. Add source IDs/provider/run ID as non-executable provenance keys and exclude those keys from builder parameter validation and execution fingerprints where appropriate. Keep legacy family mapping for non-Hermes records.

- [ ] **Step 4: Run mapping tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_queue_mapping.py tests/test_candidate_generation_guidance.py -q`

### Task 6: Daily Report Provenance

**Files:**
- Modify: `research_lab/reports.py`
- Test: `tests/test_hermes_reporting.py`
- Test: `tests/test_reporting_run_artifacts.py`

- [ ] **Step 1: Write failing report tests**

Test no-artifact output, successful artifact fields, provider-unavailable fields, rejection-reason rendering, metadata artifact path/run ID, and timestamp selection that excludes future Hermes artifacts.

- [ ] **Step 2: Run report tests and verify RED**

Run: `python -m pytest tests/test_hermes_reporting.py tests/test_reporting_run_artifacts.py -q`

- [ ] **Step 3: Add Hermes metadata and markdown section**

Resolve the latest eligible artifact in `write_daily_report_artifacts`, sanitize it into run metadata, and render `## Hermes Pre-Research Stage` from metadata. Do not read raw provider output or credentials.

- [ ] **Step 4: Run report tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_reporting.py tests/test_reporting_run_artifacts.py -q`

### Task 7: Systemd Templates and Operations Documentation

**Files:**
- Create: `ops/systemd/hermes-hypothesis.service`
- Create: `ops/systemd/hermes-hypothesis.timer`
- Create: `docs/hermes_scheduling.md`
- Modify: `README.md`
- Test: `tests/test_hermes_systemd.py`

- [ ] **Step 1: Write failing static unit tests**

Assert `User=trading`, the safe working directory, module entrypoint, optional environment file, research lock, `OnCalendar=*-*-* 02:00:00 UTC`, no secret values/names embedded in unit files, and timer ordering before the existing `02:30 UTC` daily timer.

- [ ] **Step 2: Run systemd tests and verify RED**

Run: `python -m pytest tests/test_hermes_systemd.py -q`

- [ ] **Step 3: Add templates and runbook**

Document provider variables, local validation, `sudo install`, `systemctl daemon-reload`, `enable --now`, `list-timers`, `status`, `journalctl`, and `disable --now` rollback. State explicitly that this branch does not deploy or restart services.

- [ ] **Step 4: Run systemd tests and verify GREEN**

Run: `python -m pytest tests/test_hermes_systemd.py -q`

### Task 8: Integrated Validation

**Files:**
- Review all modified files.

- [ ] **Step 1: Run focused Hermes tests**

Run: `python -m pytest tests/test_hermes_schema.py tests/test_hermes_providers.py tests/test_hermes_artifacts.py tests/test_hermes_runner.py tests/test_hermes_queue_mapping.py tests/test_hermes_reporting.py tests/test_hermes_systemd.py -q`

- [ ] **Step 2: Run relevant existing tests**

Run: `python -m pytest tests/test_risk_management_guidance.py tests/test_candidate_generation_guidance.py tests/test_strategy_queue.py tests/test_reporting_run_artifacts.py tests/test_daily_report_rejection_diagnostics.py tests/test_operational_cli_wrappers.py -q`

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`

- [ ] **Step 4: Perform static safety checks**

Run:

```bash
git diff --check
rg -n "eval\(|exec\(|shell=True|git reset --hard|git clean" research_lab/hermes ops/systemd docs/hermes_scheduling.md
git status --short
```

Expected: no arbitrary-code execution path, no destructive commands, and only intended files changed.
