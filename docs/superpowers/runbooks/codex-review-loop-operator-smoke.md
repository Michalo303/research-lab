# Codex Review Loop Operator Smoke Runbook

## Purpose

This runbook defines the deterministic, dry-run-only operator smoke path for `scripts/run_codex_review_loop.py`.
Its purpose is to let an operator verify review-loop behavior, audit serialization, and final report output without
running live Codex, GitHub, provider, Hermes, deployment, or backtest actions.

## Preconditions

- The tracked working tree must be clean before the run starts.
- Untracked local-only files may exist, but they must not be staged, deleted, cleaned, or modified as part of the smoke run.
- The smoke path must stay in dry-run mode only.
- On Windows, use Git for Windows Bash explicitly for git-state checks:
  `C:\Program Files\Git\bin\bash.exe`

Known local-only paths that may exist and must remain untouched include:

- `logs/`
- `scripts/snapshot_real_eod.py`
- `tests/fixtures/ohlcv_real/`
- `local-drift-backups/`
- `pr67_*`
- `tmp_safe_sync_gitbash_check.sh`

## Safe Commands

Use Git for Windows Bash for branch and tracked-tree checks:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -lc "cd /c/Users/lojka/trading/research-lab && git branch --show-current && git status --short --untracked-files=no && git status --short --untracked-files=all"
```

Run the dry-run operator smoke with the fake executor:

```powershell
python scripts/run_codex_review_loop.py --task "Operator smoke: fake review loop." --executor fake --enable-live-codex false --dry-run-external-calls true --fake-reviewer-verdicts PASS --output-dir codex_runs/review-loop-operator-smoke
```

An optional blocked dry-run smoke for executor metadata can use the CLI executor while still keeping live execution disabled:

```powershell
python scripts/run_codex_review_loop.py --task "Operator smoke: dry-run codex_cli blocked path." --executor codex_cli --enable-live-codex false --dry-run-external-calls true --output-dir codex_runs/review-loop-operator-smoke-codex-cli
```

Review the serialized artifacts:

```powershell
Get-Content codex_runs/review-loop-operator-smoke\audit.json
Get-Content codex_runs/review-loop-operator-smoke\final_report.md
```

## Expected Behavior On A Clean Tracked Tree

- The run proceeds past the pre-run tracked-tree probe.
- The loop writes `audit.json` and `final_report.md` to the selected output directory.
- In the default smoke path, execution remains fake/non-live and deterministic.
- `pre_run_tracked_dirty` is `false`.
- `pre_run_tracked_status` is empty.
- `final_tracked_dirty` remains `false` if no tracked file changes appear during the run.
- `final_tracked_status` remains empty if the tracked tree stays clean.

## Expected Hard-Abort Behavior On A Tracked Dirty Tree

- The run must abort before executor, validation, and reviewer work starts.
- No attempt entries should be recorded for a pre-run tracked-dirty abort.
- `pre_run_tracked_dirty` is `true`.
- `pre_run_tracked_status` records the tracked `git status --short --untracked-files=no` output.
- `final_tracked_dirty` is `true`.
- `final_tracked_status` records the same unsafe tracked status when the run aborts before execution.
- `final_report.md` must state that the review loop aborted before executor start because the tracked tree was not clean.

## Expected `audit.json` Fields

Run-level fields:

- `pre_run_tracked_dirty`
- `pre_run_tracked_status`
- `final_tracked_dirty`
- `final_tracked_status`
- `executor_type`
- `live_codex_enabled`
- `dry_run_external_calls`
- `live_codex_attempted`
- `blocked_reason`

Per-attempt fields:

- `post_attempt_tracked_dirty`
- `post_attempt_tracked_status`

Interpretation:

- `executor_type` identifies whether the run used the fake executor or `codex_cli`.
- `live_codex_enabled` must remain `false` for the recommended operator smoke path.
- `dry_run_external_calls` must remain `true`.
- `live_codex_attempted` should remain `false` for the dry-run smoke path.
- `blocked_reason` should be present when the executor path is intentionally blocked, for example because live Codex is disabled or dry-run external calls are enabled.

## Expected `final_report.md` Metadata

The final report should include dirty-tree metadata lines for:

- `Pre-run tracked tree dirty`
- `Pre-run tracked status`
- `Final tracked tree dirty`
- `Final tracked status`

If attempts were recorded, the report should also include:

- per-attempt tracked tree dirty state
- per-attempt tracked status

If the run aborts before executor start because the tracked tree is dirty, the report should explicitly say so.

## Fail-Closed Interpretation

- A dirty tracked tree means abort.
- A tracked-tree probe failure means abort or explicit unsafe status.
- The smoke path must not silently continue when tracked-tree state is unknown.
- If `git status` probing fails, treat the run as blocked and inspect `tracked_tree_failure_reason`.
- Do not reinterpret an unsafe or unknown tracked-tree state as a warning-only condition.

## Forbidden Actions

The operator smoke path must not perform any of the following:

- `git reset --hard`
- `git clean`
- stash or cleanup of tracked/untracked work
- deployment
- service restart
- daily research
- provider call
- LLM call
- Hermes call
- broker/order/API action
- registry append
- runtime report write outside reviewed CLI serialization behavior
- cache write
- broad backtest
- real backtest

## Operator Checklist

Before running:

- Confirm the current branch is the intended operator branch.
- Confirm tracked status is clean with `git status --short --untracked-files=no`.
- Note any existing untracked local-only files and leave them untouched.
- Confirm the command keeps `--dry-run-external-calls true`.
- Prefer the fake executor for the default smoke path.

During artifact review:

- Confirm `audit.json` and `final_report.md` were written only to the selected output directory.
- Confirm `pre_run_tracked_dirty`, `final_tracked_dirty`, and tracked status fields match the observed state.
- Confirm `dry_run_external_calls` is `true`.
- Confirm `live_codex_attempted` is `false`.
- If using `codex_cli` in dry-run mode, confirm any block is explicit in `blocked_reason`.

After the run:

- Re-check tracked status with `git status --short --untracked-files=no`.
- Confirm unrelated local-only paths remain unstaged and untouched.
- Keep the run as evidence only; do not escalate it into deployment, sync, or research execution.

If an abort occurs:

- Stop immediately and inspect the tracked-tree status output.
- If `tracked_tree_failure_reason` is present, treat it as a fail-closed probe error.
- Resolve the tracked-tree issue intentionally before another smoke run.
- Do not use destructive cleanup commands to force the tree back to clean.
