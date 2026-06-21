# Codex Review Loop Output Parser V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic JSON-first parser for gated `codex_cli` review-loop output and surface parsed fields through executor and CLI artifacts without enabling any live external integrations.

**Architecture:** Add one focused parser module that turns `stdout`/`stderr`/`exit_code` into a structured parse payload. Keep `CodexRoundResult` unchanged by projecting only `changed_files`, `summary`, and `diff_line_count` to top-level executor fields while storing richer parsed data in `executor_details` for audit and final report rendering.

**Tech Stack:** Python, pytest, dataclasses, existing review-loop executor/CLI modules

---

### Task 1: Add parser contract tests

**Files:**
- Create: `tests/test_codex_review_loop_output_parser.py`
- Test: `tests/test_codex_review_loop_output_parser.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_whole_stdout_json_contract():
    result = parse_codex_review_loop_output(stdout=payload, stderr="", exit_code=0)
    assert result.changed_files == ["research_lab/orchestration/codex_review_loop.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_codex_review_loop_output_parser.py -q`
Expected: FAIL because `parse_codex_review_loop_output` and the new parser module do not exist yet.

- [ ] **Step 3: Write minimal parser implementation**

```python
def parse_codex_review_loop_output(*, stdout: str, stderr: str, exit_code: int | None) -> ParsedCodexReviewLoopOutput:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_codex_review_loop_output_parser.py -q`
Expected: PASS for the parser contract cases.

- [ ] **Step 5: Commit**

```bash
git add tests/test_codex_review_loop_output_parser.py research_lab/orchestration/codex_review_loop_output_parser.py
git commit -m "feat: add codex review loop output parser"
```

### Task 2: Integrate parser into the codex CLI executor

**Files:**
- Modify: `research_lab/orchestration/codex_review_loop_executors.py`
- Modify: `tests/test_codex_review_loop_executors.py`

- [ ] **Step 1: Write the failing executor integration tests**

```python
def test_codex_cli_valid_json_populates_changed_files_and_diff_summary():
    result = executor.execute("Implement safely.", 1)
    assert result.changed_files == ["research_lab/orchestration/codex_review_loop.py"]
    assert result.diff_line_count == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_codex_review_loop_executors.py -q`
Expected: FAIL because the executor still returns placeholder changed-files and diff metadata.

- [ ] **Step 3: Write minimal executor integration**

```python
parsed = parse_codex_review_loop_output(stdout=completed.stdout or "", stderr=completed.stderr or "", exit_code=completed.returncode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_codex_review_loop_executors.py -q`
Expected: PASS with parsed fields projected onto `CodexRoundResult` and parser data attached to `executor_details`.

- [ ] **Step 5: Commit**

```bash
git add research_lab/orchestration/codex_review_loop_executors.py tests/test_codex_review_loop_executors.py
git commit -m "feat: integrate codex parser into review loop executor"
```

### Task 3: Surface parsed fields in CLI audit/report output

**Files:**
- Modify: `scripts/run_codex_review_loop.py`
- Modify: `tests/test_codex_review_loop_cli.py`

- [ ] **Step 1: Write the failing CLI artifact tests**

```python
def test_codex_cli_audit_and_report_include_parsed_fields(tmp_path: Path):
    assert audit["attempts"][0]["executor_result"]["executor_details"]["parsed_output"]["changed_files"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_codex_review_loop_cli.py -q`
Expected: FAIL because the audit/report payload does not yet include parsed output summaries.

- [ ] **Step 3: Write minimal CLI surfacing changes**

```python
parsed_output = executor_details.get("parsed_output", {})
payload["parsed_summary"] = parsed_output.get("summary", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_codex_review_loop_cli.py -q`
Expected: PASS with parsed summary, changed files, diff summary, validation status, and parser warnings rendered in artifacts.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_codex_review_loop.py tests/test_codex_review_loop_cli.py
git commit -m "feat: surface parsed codex output in review loop artifacts"
```

### Task 4: Final validation

**Files:**
- Modify: `research_lab/orchestration/codex_review_loop_output_parser.py`
- Modify: `research_lab/orchestration/codex_review_loop_executors.py`
- Modify: `scripts/run_codex_review_loop.py`
- Modify: `tests/test_codex_review_loop_output_parser.py`
- Modify: `tests/test_codex_review_loop_executors.py`
- Modify: `tests/test_codex_review_loop_cli.py`

- [ ] **Step 1: Run whitespace and patch validation**

Run: `git diff --check`
Expected: no output

- [ ] **Step 2: Run focused review-loop tests**

Run: `python -m pytest tests/test_codex_review_loop.py tests/test_codex_review_loop_cli.py tests/test_codex_review_loop_executors.py tests/test_codex_review_loop_output_parser.py -q`
Expected: PASS

- [ ] **Step 3: Run broader regression coverage**

Run: `python -m pytest tests/test_codex_review_loop.py tests/test_codex_review_loop_cli.py tests/test_codex_review_loop_executors.py tests/test_codex_review_loop_output_parser.py tests/test_codex_autonomous_loop.py tests/test_codex_autonomous_contract.py tests/test_github_pr_adapter.py -q`
Expected: PASS

- [ ] **Step 4: Inspect final diff for scope**

Run: `git status --short`
Expected: only parser/executor/CLI/tests/plan file changes for this branch.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-21-codex-review-loop-output-parser-v1.md
git commit -m "docs: add codex review loop parser implementation plan"
```
