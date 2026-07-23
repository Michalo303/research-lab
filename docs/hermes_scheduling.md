# Hermes Scheduling

Hermes is a research-only pre-daily stage. It may submit structured hypotheses using existing strategy builders, but it cannot generate executable strategy code, change validation gates, promote strategies, or place trades.

## CLI

Run from the repository root:

```bash
.venv/bin/python -m research_lab.hermes.run_hypothesis_generation --root /opt/trading/research-lab
```

Every invocation writes an immutable JSON artifact under `reports/hermes/runs/YYYY-MM-DD/`. Missing provider configuration produces a `provider_unavailable` artifact and leaves `registry/hypothesis_queue.jsonl` unchanged.

Remote OpenAI-compatible endpoints must use HTTPS. Plaintext HTTP is accepted only for exact loopback hosts (`localhost`, `127.0.0.1`, or `::1`), so an API key is never sent to a remote HTTP endpoint. Invalid or disallowed endpoint URLs produce an audited `provider_error` without changing the queue.

Hermes derives the active research blocker from the structured rejection counts in the latest daily report before considering free-form risk prose. It maps only the supported walk-forward, drawdown, and cost-stress reasons. When canonical Knihomol inputs exist but yield no usable notes for that blocker, Hermes writes a `book_context_unavailable` terminal artifact and does not call the provider or change the queue. A generated hypothesis must cite at least one selected `note_id`; uncited output is rejected.

For valid hypotheses, Hermes first writes an immutable `artifact_written` record containing the planned queue impact. It then writes the complete updated JSONL queue to a temporary file under the registry lock and commits it with `os.replace`. A terminal immutable artifact records `queue_committed` or `queue_commit_failed`; a failed commit preserves the complete prior queue.

The daily runner records a deterministic identity for the resolved data snapshot. A queued Hermes strategy already evaluated with the same execution fingerprint and snapshot identity is skipped before backtesting. It becomes eligible again only when a material snapshot field changes, such as source, provider, symbol order, time bounds, fallback status, or an approved content hash.

## Command Provider

Configure these values in the server's existing operator-managed environment:

```text
HERMES_PROVIDER=command
HERMES_COMMAND=/absolute/path/to/hermes-agent --structured-json
HERMES_TIMEOUT_SECONDS=120
```

The configured command receives the prompt on stdin. It is parsed into an argument vector and executed with `shell=False`. Provider output is treated only as JSON data and must pass the local builder/parameter whitelist.

## OpenAI-Compatible Provider

For OpenAI, OpenRouter, or another compatible endpoint, configure:

```text
HERMES_PROVIDER=openai_compatible
HERMES_OPENAI_BASE_URL=https://provider.example/v1
HERMES_OPENAI_MODEL=provider/model-name
HERMES_OPENAI_API_KEY=<operator-managed value>
HERMES_TIMEOUT_SECONDS=120
```

For a loopback Ollama-compatible endpoint, an API key is optional:

```text
HERMES_PROVIDER=openai_compatible
HERMES_OPENAI_BASE_URL=http://127.0.0.1:11434/v1
HERMES_OPENAI_MODEL=qwen2.5
```

Do not put credentials in systemd unit files or Git.

## Validate Before Installation

```bash
cd /opt/trading/research-lab
.venv/bin/python -m pytest tests/test_hermes_systemd.py tests/test_hermes_runner.py -q
.venv/bin/python -m research_lab.hermes.run_hypothesis_generation --root /opt/trading/research-lab
find reports/hermes/runs -type f -name '*.json' | sort | tail -1
```

The final command may create a `provider_unavailable` audit artifact when no provider is configured. It must not change the queue in that state.

## Install On Hetzner

Installation is a separate operator-approved action. These commands are documentation only and are not run by the repository tests:

```bash
cd /opt/trading/research-lab
sudo install -m 0644 ops/systemd/hermes-hypothesis.service /etc/systemd/system/hermes-hypothesis.service
sudo install -m 0644 ops/systemd/hermes-hypothesis.timer /etc/systemd/system/hermes-hypothesis.timer
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-hypothesis.timer
```

The Hermes timer runs at `02:00 UTC`. The existing daily research timer runs at `02:30 UTC`.

## Verify

```bash
systemctl list-timers --all | grep -E 'hermes|trading-research-daily'
systemctl status hermes-hypothesis.timer --no-pager
systemctl status hermes-hypothesis.service --no-pager
journalctl -u hermes-hypothesis.service --no-pager -n 100
```

## Rollback

```bash
sudo systemctl disable --now hermes-hypothesis.timer
sudo rm -f /etc/systemd/system/hermes-hypothesis.timer /etc/systemd/system/hermes-hypothesis.service
sudo systemctl daemon-reload
```

Rollback does not delete Hermes run artifacts or queue history.
