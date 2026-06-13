# Hermes Book Runtime Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded, fail-open book-derived research context to scheduled Hermes prompts and provide a skeleton-only note preparation CLI.

**Architecture:** A focused runtime loader reads the external book index and validated JSONL notes, selects blocker-relevant entries, and returns prompt text plus safe metadata. The existing Hermes orchestrator passes the diagnostic blocker into prompt construction. A separate CLI selects relevant indexed books and writes only short schema-valid note skeletons beneath an explicitly supplied notes directory.

**Tech Stack:** Python standard library, existing `hermes_knowledge` validators and ranking helpers, pytest.

---

### Task 1: Runtime Book Context

**Files:**
- Create: `hermes_knowledge/runtime.py`
- Modify: `research_lab/llm/hypothesis_adapter.py`
- Modify: `research_lab/hermes/run_hypothesis_generation.py`
- Test: `tests/test_hermes_book_runtime.py`

- [x] Write failing tests proving valid notes add a `BOOK-DERIVED RESEARCH CONTEXT` section while missing index or notes fail open.
- [x] Run the focused tests and confirm failure because runtime integration is absent.
- [x] Implement a bounded loader using `load_book_index`, `load_knowledge_jsonl`, and `build_hermes_knowledge_prompt`.
- [x] Pass the active diagnostic blocker into `build_hermes_prompt` and expose only note count and selected book IDs as safe metadata.
- [x] Run the focused tests and confirm they pass.

### Task 2: Controlled Note Skeleton CLI

**Files:**
- Create: `hermes_knowledge/extract_notes.py`
- Test: `tests/test_hermes_book_extraction.py`

- [x] Write failing tests for dry-run, validated output, path containment, and bounded book/note counts.
- [x] Run the focused tests and confirm failure because the CLI module is absent.
- [x] Implement required arguments and deterministic skeleton generation from index metadata only; never open PDF paths.
- [x] Validate every output with `validate_entry` before writing JSONL beneath `--notes-dir`.
- [x] Run the focused tests and confirm they pass.

### Task 3: Regression And Safety Verification

**Files:**
- Modify only tests if a regression requires clarification.

- [x] Run all new book runtime and extraction tests.
- [x] Run existing Hermes planner, provider, queue, report, schema, systemd, and book-knowledge tests.
- [x] Inspect Git status and tracked paths to confirm no PDF, private index, extracted note, runtime report, registry, cache, environment, or secret file was added.
- [x] Review the final diff for changes to promotion, tiering, backtesting, walk-forward, execution, or deployment behavior; none are permitted.
