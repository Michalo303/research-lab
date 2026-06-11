# Token-Safe Agent Workflow Design

## Goal

Reduce agent token waste without weakening research quality, validation gates, or deterministic pipeline behavior.

## Scope

This change only affects agent orientation. It does not alter strategy generation, backtesting, walk-forward validation, tiering, promotion gates, data provider behavior, or deployment rules.

## Approach

Add a deterministic `scripts/agent_brief.py` command that prints a compact project brief from existing small artifacts. The brief should point agents to the current research blockers, latest daily report summary, duplicate-candidate evidence, and safe next actions while explicitly warning against loading large generated files.

Update `AGENTS.md` with token-budget rules. Agents should start with the brief, avoid whole-file reads of large reports and generated artifacts, and only inspect narrow files that support the current task.

## Components

- `scripts/agent_brief.py`: reads `AGENTS.md`, the newest file in `reports/daily`, and lightweight registry files if present; prints a bounded Markdown brief.
- `tests/test_agent_brief.py`: verifies the brief is deterministic, bounded, and does not require large generated artifacts.
- `AGENTS.md`: documents the new workflow and non-negotiable safety constraints.

## Safety

The brief is read-only. It must not call external APIs, run research jobs, modify registry files, or infer promotion status beyond what existing reports say. Missing files should produce explicit "not found" lines rather than failing the agent startup path.

## Success Criteria

- A new agent can run one command and get the next useful context in under a few hundred lines.
- The workflow discourages broad `rg` and whole-report reads.
- Tests prove the brief works from temporary fixture data.
- Existing research gates remain untouched.
