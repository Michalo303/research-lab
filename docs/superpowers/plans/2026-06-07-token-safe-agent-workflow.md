# Token-Safe Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic startup brief and agent rules that reduce token waste without changing research logic.

**Architecture:** Keep the change outside the research pipeline. A small Python script reads existing text/CSV/JSONL artifacts with strict line limits and prints a compact Markdown brief; docs tell agents to use it first and avoid large generated files.

**Tech Stack:** Python standard library, pytest, Markdown docs.

---

### Task 1: Brief Script Contract

**Files:**
- Create: `tests/test_agent_brief.py`
- Create: `scripts/agent_brief.py`

- [ ] Write tests that build a temporary lab with `AGENTS.md`, `reports/daily/YYYY-MM-DD.md`, and `registry/leaderboard.csv`, then assert the brief includes the latest report summary, next actions, and large-file warnings.
- [ ] Run `pytest tests/test_agent_brief.py -q` and confirm it fails because `scripts/agent_brief.py` does not exist.
- [ ] Implement `scripts/agent_brief.py` with pure standard-library code and bounded output.
- [ ] Run `pytest tests/test_agent_brief.py -q` and confirm it passes.

### Task 2: Agent Rules

**Files:**
- Modify: `AGENTS.md`

- [ ] Add a "Token-safe operating mode" section requiring `python scripts/agent_brief.py` before broad exploration.
- [ ] Document forbidden broad reads of `INVENTORY_full_diff.patch`, full `reports/runs`, full `backtests/runs`, full processed data, and unrestricted `rg` over generated artifacts.
- [ ] Preserve all existing validation and deployment restrictions.

### Task 3: Verification

**Files:**
- Validate: `scripts/agent_brief.py`
- Validate: `tests/test_agent_brief.py`

- [ ] Run `pytest tests/test_agent_brief.py -q`.
- [ ] Run `python scripts/agent_brief.py`.
- [ ] Run `git diff -- AGENTS.md scripts/agent_brief.py tests/test_agent_brief.py docs/superpowers/specs/2026-06-07-token-safe-agent-workflow-design.md docs/superpowers/plans/2026-06-07-token-safe-agent-workflow.md` and confirm no research gate logic changed.
