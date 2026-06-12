# Hermes Execution Audit

Audit date: June 12, 2026

## Repository State Before This Change

The repository contained:

- `HERMES_ROLE_CONTRACT.md`
- `HERMES_RESEARCH_LAB_PROMPT.md`
- prompt construction in `research_lab/llm/hypothesis_adapter.py`
- direct JSONL ingestion in `research_lab/llm/hypothesis_adapter.py`
- `scripts/write_hermes_hypothesis_prompt.py`

It did not contain a Hermes provider invocation, autonomous CLI, scheduled unit, immutable Hermes run artifact, or daily-report Hermes provenance.

## Previous Execution Path

1. `scripts/run_daily_research.py` called `research_lab.runner.run_daily_research`.
2. The daily runner loaded deterministic baseline strategies.
3. It loaded deterministic near-miss variants.
4. It read up to four records from `registry/hypothesis_queue.jsonl` through `queued_hypothesis_strategies`.
5. Queue records were converted to generic family templates and passed to existing deterministic builders.
6. Results were written to backtest artifacts, registries, strategy cards, leaderboard, and daily reports.

No scheduled stage called `build_hermes_prompt` or `ingest_llm_hypotheses`. The self-improvement timer called `scripts/run_self_improvement.py`, which read existing registries and wrote a markdown report only.

## Queue Consumption Finding

Daily research already consumed the hypothesis queue. However, broad family mapping meant a queue record did not select an exact builder. An untrusted LLM `builder` value was ignored rather than validated. This change introduces strict revalidation and exact mapping for Hermes records while preserving legacy non-Hermes queue behavior.

## Read-Only Hetzner Audit

SSH target used:

```text
trading@91.99.99.158
```

Repository checkout:

```text
/opt/trading/research-lab
commit 57fa7b9d3303069d2963cf385eb3d5949b983b0d
working tree clean
```

Relevant read-only commands included:

```bash
git status --short
git rev-parse HEAD
systemctl list-timers --all --no-pager | grep -i hermes
systemctl list-units --all --no-pager | grep -i hermes
systemctl list-timers --all --no-pager | grep -i research
systemctl list-units --all --no-pager | grep -i research
find /etc/systemd/system \( -iname '*hermes*' -o -iname '*research*' \) -print
find . \( -iname '*hermes*' -o -iname '*hypothesis*' \) -print
```

Findings:

- no Hermes service
- no Hermes timer
- no Hermes provenance in recent daily/run reports
- one historical prompt artifact under `reports/llm/`
- no `hermes`, `openai`, `anthropic`, or `ollama` executable discovered
- no matching LLM provider Python package discovered in the project virtual environment
- existing hourly, daily, weekly, self-improvement, sync, and dashboard units were present
- daily and weekly services were in failed state at audit time

The failed daily and weekly services are outside this feature's scope. The audit did not modify files, inspect `.env`, run research, deploy, enable/disable timers, or restart services.

## Execution Path Added By This Change

1. `python -m research_lab.hermes.run_hypothesis_generation` selects the latest daily diagnostics.
2. It builds a prompt containing the diagnostic context, risk contract, and exact builder schemas.
3. The configured `command` or `openai_compatible` adapter returns JSON data.
4. Local code parses the complete envelope and validates each hypothesis.
5. Only existing builder names and schema-valid parameters are accepted.
6. Valid nonduplicate records are appended to the hypothesis queue with Hermes provenance.
7. Every invocation writes an immutable sanitized run artifact.
8. The existing daily runner revalidates Hermes queue records and maps them to exact existing builders.
9. The daily report records the latest eligible Hermes run status and counts.

No provider output is evaluated or executed as strategy code.
