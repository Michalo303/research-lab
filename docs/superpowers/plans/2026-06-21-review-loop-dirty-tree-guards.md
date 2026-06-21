# Review-Loop Dirty-Tree Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic tracked-tree guards and audit/report metadata to the review-loop before any real autonomous execution.

**Architecture:** Put the tracked-tree probe in `research_lab/orchestration/codex_review_loop.py` behind an injectable checker that defaults to `git status --short --untracked-files=no`. Abort before executor/validator/reviewer on probe failure or pre-run tracked dirtiness, and record both pre-run and post-attempt tracked-tree state in the audit consumed by the CLI report writer.

**Tech Stack:** Python, pytest, subprocess, dataclasses

---

### Task 1: Add failing review-loop core tests

**Files:**
- Modify: `tests/test_codex_review_loop.py`
- Test: `tests/test_codex_review_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_pre_run_dirty_tracked_tree_aborts_before_executor_validator_and_reviewer():
    ...


def test_clean_tracked_tree_allows_review_loop_to_run():
    ...


def test_post_attempt_tracked_dirty_state_is_recorded():
    ...


def test_git_status_probe_failure_aborts_safely():
    ...
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `python -m pytest tests/test_codex_review_loop.py -q`
Expected: FAIL because the loop has no tracked-tree checker, no abort path, and no tracked-tree audit fields yet.

- [ ] **Step 3: Implement the minimal loop-side data model and control-flow changes**

```python
@dataclass
class TrackedTreeState:
    dirty: bool
    status: str
    probe_failed: bool = False
    failure_reason: str | None = None
```

```python
if pre_run_state.probe_failed or pre_run_state.dirty:
    return CodexReviewLoopAudit(...)
```

- [ ] **Step 4: Re-run the targeted tests to verify they pass**

Run: `python -m pytest tests/test_codex_review_loop.py -q`
Expected: PASS

### Task 2: Add failing CLI/report tests for new audit metadata

**Files:**
- Modify: `tests/test_codex_review_loop_cli.py`
- Modify: `scripts/run_codex_review_loop.py`
- Test: `tests/test_codex_review_loop_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

```python
def test_cli_dirty_tree_abort_writes_audit_and_report_metadata(tmp_path: Path):
    ...


def test_cli_audit_includes_post_attempt_tracked_tree_metadata(tmp_path: Path, monkeypatch):
    ...
```

- [ ] **Step 2: Run the targeted CLI tests to verify they fail**

Run: `python -m pytest tests/test_codex_review_loop_cli.py -q`
Expected: FAIL because the CLI payload/report do not expose the new tracked-tree metadata yet.

- [ ] **Step 3: Implement the minimal audit/report serialization changes**

```python
payload["dry_run_external_calls"] = ...
payload["pre_run_tracked_dirty"] = ...
payload["final_tracked_dirty"] = ...
```

```python
lines.append(f"- Pre-run tracked tree dirty: {audit_payload['pre_run_tracked_dirty']}")
```

- [ ] **Step 4: Re-run the targeted CLI tests to verify they pass**

Run: `python -m pytest tests/test_codex_review_loop_cli.py -q`
Expected: PASS

### Task 3: Validate blocked executor metadata and full required suite

**Files:**
- Modify: `tests/test_codex_review_loop_executors.py`
- Test: `tests/test_codex_review_loop.py`
- Test: `tests/test_codex_review_loop_cli.py`
- Test: `tests/test_codex_review_loop_executors.py`
- Test: `tests/test_codex_review_loop_output_parser.py`
- Test: `tests/test_codex_autonomous_loop.py`
- Test: `tests/test_codex_autonomous_contract.py`
- Test: `tests/test_github_pr_adapter.py`

- [ ] **Step 1: Adjust executor-facing assertions only if needed**

```python
assert result.executor_details["live_codex_attempted"] is False
assert "blocked_reason" in result.executor_details
```

- [ ] **Step 2: Run the exact required validation suite**

Run: `python -m pytest tests/test_codex_review_loop.py tests/test_codex_review_loop_cli.py tests/test_codex_review_loop_executors.py tests/test_codex_review_loop_output_parser.py tests/test_codex_autonomous_loop.py tests/test_codex_autonomous_contract.py tests/test_github_pr_adapter.py -q`
Expected: PASS

- [ ] **Step 3: Run repository cleanliness validation**

Run: `git diff --check`
Expected: no output, exit 0

Run: `git status --short --untracked-files=no`
Expected: only intended tracked changes in review-loop code/tests, or no tracked changes after the task if nothing changed.
