# Claude Operating Rules for research-lab

## Project identity

This repository is `research-lab`, an algorithmic-trading research system.

Primary goals:

- Research strategies and risk overlays.
- Maintain deterministic promotion gates.
- Avoid uncontrolled deployment, registry writes, or runtime mutation.

## Hard safety constraints

Never do any of the following unless the user explicitly asks in the current session:

- Do not deploy.
- Do not promote strategies.
- Do not append to registry files.
- Do not modify production leaderboard or report artifacts.
- Do not restart services or systemd timers.
- Do not run daily research jobs.
- Do not modify `.env` or secrets.
- Do not delete runtime data, reports, cache, logs, or fixtures.
- Do not use `git reset --hard`.
- Do not use `git clean`.
- Do not remove untracked files.

Protected/unrelated untracked paths known to exist locally:

- `logs/`
- `scripts/snapshot_real_eod.py`
- `tests/fixtures/ohlcv_real/`

## Claude Code Primary Agent Mode

### Current agent policy

- Claude Code is the primary implementation, refactoring, review, and PR-preparation agent.
- Codex is currently unavailable and should not be assumed available.
- Roo Code is not part of the active workflow.

### Default Claude autonomy

After the user gives a coding task, Claude Code is allowed to proceed without step-by-step approval through:

- repository inspection
- relevant file discovery
- design of the implementation approach
- implementation
- refactoring inside the task scope
- adding or updating tests
- running targeted validation
- fixing failures discovered by targeted validation
- formatting / whitespace fixes
- git diff checks
- committing
- pushing
- opening a PR

Claude Code does not need to ask for approval for every file read, every edit, or every test command when those actions are part of the approved task.

### Scope discipline

Claude Code should use its own engineering judgment, but must keep changes relevant to the user's task.

If the implementation naturally requires additional files, Claude may modify them when they are directly necessary for the task and should report them clearly.

Claude should avoid unrelated cleanup, broad rewrites, style churn, or opportunistic refactors unless they are necessary for the task.

### Stop-and-ask actions

Claude Code must stop and ask before:

- deployment
- strategy promotion
- production registry mutation
- daily research run
- production backtest run
- service restart
- systemd modification
- `.env` or secrets modification
- deletion of runtime data, reports, logs, cache, registry, or leaderboards
- broker/trading execution changes
- provider credential/configuration changes
- destructive git operations such as `git reset --hard`, `git clean`, force push, branch deletion, or deleting untracked files
- large repository-wide rewrites unrelated to the task

### Protected local untracked paths

These local untracked paths are known and must not be deleted or cleaned:

- `logs/`
- `scripts/snapshot_real_eod.py`
- `tests/fixtures/ohlcv_real/`

They may appear in `git status` and should normally be ignored.

### Normal coding workflow

For normal tasks, Claude Code should:

- create a task branch
- implement autonomously
- add/update tests
- run targeted tests
- run `git diff --check`
- inspect `git status` and changed files
- commit
- push
- open PR
- report exact changed files, validation results, branch, commit SHA, and PR URL

### Review workflow

For review-only tasks, Claude Code should not edit files unless the user asks for fixes.
It should return approve/request changes with concrete findings.

### Merge and Hetzner sync

Merge is allowed only when the user explicitly asks.
Hetzner sync is allowed only when the user explicitly asks and must use the documented safe sync wrapper.
Sync does not imply deployment, daily research, promotion, registry append, or production backtest.

## Git workflow

Before modifying anything:

1. Run `git status --short`.
2. Identify branch and HEAD.
3. Identify tracked vs. untracked changes.
4. Stop if tracked changes are unrelated to the requested task.

For each task:

1. Create a separate branch.
2. Keep the diff minimal.
3. Add or update tests first where practical.
4. Run targeted tests.
5. Run `git diff --check`.
6. Report exact changed files.
7. Do not merge unless explicitly instructed.

## Validation preference

Prefer targeted `pytest` first. Full `pytest` can be run only when the change is ready and the user approves or when it is clearly safe.

## Architecture constraints

The Research Orchestrator must remain deterministic and provider-agnostic.

LLM reasoning may be optional through a model/provider router, but deterministic code must govern:

- blocker selection
- worker routing
- safety policy
- promotion/no-promotion decisions
- audit JSON
- deployment gates

Promotion must remain governed by explicit deterministic gates such as:

- `deployment_gate.py`
- walk-forward validation
- drawdown gates
- cost stress
- stability gates

LLM judgment must never directly promote or deploy a strategy.

## Current next logical task

The next logical task is:

`risk_overlay_controlled_backtest_v1`

It must be implemented only as a separate branch and must not perform:

- promotion
- deployment
- registry append
- leaderboard/report production mutation
- service restart
- daily research run
