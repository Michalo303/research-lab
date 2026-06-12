# Hermes Scheduled Strategy Agent Design

## Objective

Turn the dormant Hermes prompt/ingest adapter into an auditable pre-daily research stage. Hermes may propose structured strategy hypotheses, but deterministic repository code remains solely responsible for validation, queue insertion, backtesting, tiering, and promotion decisions.

## Current Execution Path

The current daily path is:

1. `scripts/run_daily_research.py` calls `research_lab.runner.run_daily_research`.
2. The runner combines baseline strategies, deterministic near-miss mutations, and up to four entries read from `registry/hypothesis_queue.jsonl`.
3. `queued_hypothesis_strategies` maps broad queue families to a small set of existing builders.
4. The deterministic runner evaluates those specs and writes registries and reports.

Hermes currently has only prompt construction and direct JSONL ingestion helpers. There is no provider invocation, CLI, scheduled unit, immutable run artifact, or daily-report provenance. `run_self_improvement` only writes a report.

The read-only Hetzner audit on June 12, 2026 confirmed that no Hermes service or timer is installed. It also found no Hermes/OpenAI/Anthropic/Ollama executable or Python client package. Existing daily and weekly services were failed, but this feature does not modify or restart them.

## Architecture

Add a focused `research_lab.hermes` package with four responsibilities:

- `schema.py`: whitelist existing builders and validate every field and parameter.
- `providers.py`: invoke either a configured command or an OpenAI-compatible HTTP endpoint.
- `artifacts.py`: discover the latest diagnostics and write immutable timestamped run artifacts.
- `run_hypothesis_generation.py`: orchestrate prompt creation, provider invocation, validation, deduplication, queue append, and audit logging.

The module entrypoint will be:

```bash
python -m research_lab.hermes.run_hypothesis_generation
```

No provider may return executable code. Provider output is data only and must pass the local schema before queue insertion.

## Input Diagnostics

Each run selects the latest available immutable daily run report under `reports/runs/`. If no immutable run report exists, it falls back to the latest `reports/daily/*.md`. The selected report path and a deterministic dominant-blocker summary are included in the prompt and artifact.

The existing Hermes system contract and risk-management guidance remain part of the prompt. The prompt explicitly requests a JSON object with a `hypotheses` array and lists the permitted builders and schemas.

## Structured Hypothesis Contract

Each proposal must include:

- `title`
- `family`
- `builder`
- `rationale`
- `parameters`
- `risk_controls`

Optional provenance fields are `tags` and `source_url`.

Accepted builders are limited to builders already present in `build_weights`:

- `long_term_trend_filter`
- `long_term_vol_target`
- `long_term_strict_cash_filter`
- `long_term_vol_target_cap`
- `active_momentum_rotation`
- `rotation_momentum_drawdown_filter`
- `rotation_momentum_circuit_breaker`
- `defensive_asset_rotation`
- `swing_rsi_pullback`
- `swing_trend_filtered_pullback`
- `intraday_vwap_rsi_reclaim`

Each builder has an explicit family, required keys, optional keys, types, ranges, and allowed symbol/universe values. Unknown keys, missing required keys, non-finite numbers, unsupported symbols, invalid ranges, and family/builder mismatches are rejected. The validator also rejects leverage-like exposure settings above 1.0 and rotation proposals without an explicit strong risk overlay.

Accepted queue records retain the validated builder and parameters. `queued_hypothesis_strategies` will use those exact whitelisted fields instead of replacing them with a generic family template. Legacy non-Hermes queue records retain the existing fallback mapping.

## Providers

### Command Provider

`HERMES_PROVIDER=command` requires `HERMES_COMMAND`.

The command is configured by the operator, parsed with `shlex`, and executed directly with `shell=False`. The prompt is sent on stdin and JSON is read from stdout. The repository never evaluates provider output and never adds provider-supplied command arguments. Missing configuration, missing executable, timeout, nonzero exit, or empty output yields a safe provider failure.

### OpenAI-Compatible Provider

`HERMES_PROVIDER=openai_compatible` requires:

- `HERMES_OPENAI_BASE_URL`
- `HERMES_OPENAI_MODEL`
- `HERMES_OPENAI_API_KEY`, except for loopback endpoints where an empty key is allowed for local Ollama-compatible deployments

The adapter uses the Python standard library to POST to `<base_url>/chat/completions`. It requests non-streaming JSON output, applies a finite timeout, and extracts `choices[0].message.content`. Remote endpoints require HTTPS; HTTP is allowed only for exact loopback hosts (`localhost`, `127.0.0.1`, and `::1`). Credentials are sent only as an authorization header and are never written to artifacts, logs, exceptions, reports, or commands.

Unsupported or missing provider configuration returns `provider_unavailable`. Network or provider execution failures return `provider_error`. Neither state changes the queue.

## Queue Safety

Provider output is parsed completely before any queue commit occurs. Every proposal receives an individual validation result. Only valid, nonduplicate hypotheses are prepared for an atomic locked JSONL replacement.

Each accepted record includes:

- `source_title: hermes`
- `source_key` derived from the validated execution fingerprint
- `llm_generated: true`
- `hermes_run_id`
- `hermes_provider`
- validated `builder` and `parameters`
- existing machine-readable risk guidance

Malformed provider envelopes reject the entire output. Individual malformed hypotheses are recorded as rejected while valid siblings may be imported. Provider failure, envelope failure, or zero valid hypotheses leaves the queue unchanged.

## Immutable Artifacts

Each invocation writes immutable JSON artifacts under:

```text
reports/hermes/runs/YYYY-MM-DD/<run_id>[.validated].json
```

Artifacts are created with exclusive-create semantics and are never overwritten. A valid import writes an `artifact_written` precommit record before queue mutation, followed by a terminal `queue_committed` or `queue_commit_failed` record. They contain:

- `run_id`
- UTC timestamp
- git commit
- provider
- status
- input report path
- dominant blocker
- generated count
- imported count
- rejected count
- rejection reasons
- output queue path
- imported hypothesis IDs

Raw prompts, raw model responses, environment variables, authorization headers, and secrets are not stored.

## Daily Report Provenance

Daily report generation discovers the latest Hermes artifact whose timestamp is not later than the daily run timestamp. It adds a `Hermes Pre-Research Stage` section showing whether Hermes ran, provider, status, counts, rejection reasons, artifact path, and imported hypothesis IDs.

Daily run metadata also records the selected Hermes artifact path and run ID. Strategy results derived from Hermes queue records preserve `hermes_run_id` and `hermes_provider` in their parameters and hypothesis-result provenance.

The daily runner remains functional when no artifact exists or the latest artifact reports provider failure.

## Scheduling

Add templates:

- `ops/systemd/hermes-hypothesis.service`
- `ops/systemd/hermes-hypothesis.timer`

The service runs as `trading`, uses `/opt/trading/research-lab`, loads the existing optional `.env`, takes the same research lock, and executes the module entrypoint from `.venv`. It contains no credentials.

The timer runs daily at `02:00 UTC`, before the existing daily research timer at `02:30 UTC`. Installation is documented but not automated by tests or this branch. Documentation includes install, enable, status, journal, timer-list, manual dry verification, and rollback commands.

## Failure Handling

- Provider unavailable: write artifact with `provider_unavailable`, return a nonfatal CLI result, queue unchanged.
- Provider execution/network error: write artifact with `provider_error`, queue unchanged.
- Malformed envelope: write artifact with `invalid_output`, queue unchanged.
- Mixed valid and invalid proposals: import valid proposals, record individual rejection reasons.
- Artifact collision: fail rather than overwrite.
- Queue commit failure: terminal artifact reports `queue_commit_failed`; atomic replacement preserves the complete existing queue.

The systemd service treats provider-unavailable as a successful audited no-op so missing optional credentials do not break the daily pipeline. Unexpected internal errors remain nonzero failures.

## Testing

Focused tests cover both provider adapters, missing configuration, malformed responses, whitelist enforcement, parameter schemas, queue atomicity on provider failure, immutable artifacts, executable queue mapping, daily provenance, and secret-free systemd templates scheduled before daily research.

Relevant existing candidate-generation, risk-guidance, reporting, and operational wrapper tests will run after focused tests. The full suite will run before completion.

## Non-Goals

- No deployment or systemd enablement.
- No service restart.
- No repair of currently failed daily or weekly services.
- No secret or `.env` changes.
- No promotion-gate or allocation-policy changes.
- No arbitrary strategy code generation or execution.
- No deletion or rewrite of historical runtime artifacts.
