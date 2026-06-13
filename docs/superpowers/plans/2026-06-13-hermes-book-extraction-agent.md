# Hermes Book Extraction Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a blocker-first private-book extraction pipeline that creates validated proposed notes, requires explicit promotion, supplies only extracted notes to Hermes, preserves used note IDs, and learns deterministic priority overlays from experiment feedback.

**Architecture:** Extend the existing `hermes_knowledge` package. Deterministic modules select books and passages, the existing Hermes provider transforms one bounded passage into one proposal, a separate proposal envelope prevents runtime ingestion before explicit promotion, and immutable extracted entries plus feedback overlays feed current retrieval without changing strategy or gate behavior.

**Tech Stack:** Python 3.10 standard library, optional local `pypdf` import for PDF text, existing Hermes provider and knowledge validators, pytest.

---

### Task 1: Blocker Taxonomy And Book Selection

**Files:**
- Create: `hermes_knowledge/blocker_taxonomy.py`
- Create: `hermes_knowledge/book_selector.py`
- Test: `tests/test_hermes_book_selector.py`

- [ ] Write failing tests proving `walk_forward_fail` has deterministic weighted terms, unsupported blockers fail, duplicate titles collapse, feedback overlays affect rank, and no more than five books are selected.
- [ ] Run `pytest tests/test_hermes_book_selector.py -q` and confirm failure because the modules do not exist.
- [ ] Implement immutable blocker definitions and deterministic ranking over existing `BookRecord` values plus optional metadata previews and book-priority overlays.
- [ ] Run the focused selector tests and confirm they pass.
- [ ] Commit the selector slice.

### Task 2: Bounded Passage Extraction

**Files:**
- Create: `hermes_knowledge/passage_extractor.py`
- Test: `tests/test_hermes_passage_extractor.py`

- [ ] Write failing tests for sidecar discovery, localized match windows, stable passage IDs, page/location provenance, overlap deduplication, missing-text diagnostics, 1,200-character evidence limit, and three-passages-per-book maximum.
- [ ] Run `pytest tests/test_hermes_passage_extractor.py -q` and verify the expected import failure.
- [ ] Implement selected-book-only extraction, preferring `<book_id>.txt` or source-stem sidecars and falling back to an optional `pypdf` reader.
- [ ] Ensure failures return bounded diagnostic codes without private paths or passage text.
- [ ] Run focused passage tests and confirm they pass.
- [ ] Commit the extraction slice.

### Task 3: Proposed Note Contract And Provider Transformation

**Files:**
- Modify: `hermes_knowledge/schema.py`
- Modify: `hermes_knowledge/knowledge_entry.schema.json`
- Create: `hermes_knowledge/note_generator.py`
- Test: `tests/test_hermes_note_schema.py`

- [ ] Write failing tests for required generated provenance, deterministic `note_id`, proposal-envelope validation, one fake-provider call per passage, valid JSON transformation, and isolation of provider, JSON, and schema failures.
- [ ] Run `pytest tests/test_hermes_note_schema.py -q` and verify failure because proposal validation and generation are absent.
- [ ] Add optional backward-compatible runtime provenance fields (`note_id`, `source_location`, `source_passage_id`, `implementation_hint`) while requiring them in proposed envelopes.
- [ ] Implement a strict bounded note-generation prompt and convert provider JSON into repository-owned provenance plus a schema-valid proposal envelope.
- [ ] Run focused schema/generator tests and confirm they pass.
- [ ] Commit the note-contract slice.

### Task 4: Private Stores, Validation, And Explicit Promotion

**Files:**
- Create: `hermes_knowledge/note_store.py`
- Modify: `.gitignore`
- Test: `tests/test_hermes_note_promotion.py`

- [ ] Write failing tests proving extraction storage writes candidates and proposals only, validation is read-only, promotion requires one exact note ID, copied proposal envelopes cannot pass runtime validation, book hashes must match, duplicate promotion is rejected, and destination updates are atomic.
- [ ] Run `pytest tests/test_hermes_note_promotion.py -q` and verify failure because the store does not exist.
- [ ] Implement deterministic JSONL reads/writes, proposal deduplication, validation summaries, and explicit proposal-entry conversion into extracted runtime notes.
- [ ] Expand Git ignore coverage for text sidecars, passage candidates, proposed notes, and feedback artifacts.
- [ ] Run focused promotion tests and existing book-schema tests.
- [ ] Commit the storage slice.

### Task 5: Extraction CLI

**Files:**
- Create: `hermes_knowledge/cli.py`
- Test: `tests/test_hermes_book_cli.py`

- [ ] Write failing end-to-end CLI tests for `extract`, `validate`, and `promote` using temporary private directories and a fake provider invoker.
- [ ] Verify `extract` rejects limits above five books or three passages and never creates `extracted_notes`.
- [ ] Implement path defaults, overrides, bounded summaries, and orchestration of selector, extractor, generator, and store.
- [ ] Run the CLI tests and all Tasks 1-4 tests.
- [ ] Commit the CLI slice.

### Task 6: Extracted-Only Runtime And Used Note Provenance

**Files:**
- Modify: `hermes_knowledge/retriever.py`
- Modify: `hermes_knowledge/prompt.py`
- Modify: `hermes_knowledge/runtime.py`
- Modify: `research_lab/hermes/schema.py`
- Modify: `research_lab/hermes/run_hypothesis_generation.py`
- Modify: `research_lab/strategies/baselines.py`
- Modify: `research_lab/runner.py`
- Test: `tests/test_hermes_book_runtime.py`
- Test: `tests/test_hermes_queue_mapping.py`

- [ ] Write failing tests proving proposed directories are ignored even when explicitly adjacent, selected note IDs appear in runtime context and Hermes artifacts, queue records accept bounded `used_note_ids`, StrategySpec provenance preserves them, and hypothesis-result metadata records them.
- [ ] Run the two focused test files and verify the new assertions fail.
- [ ] Add priority-overlay support to retrieval without mutating extracted entries.
- [ ] Propagate selected note IDs as data-only provenance through validated Hermes records and deterministic result metadata.
- [ ] Run runtime, queue, runner, schema, and reporting regression tests.
- [ ] Commit the provenance slice.

### Task 7: Deterministic Feedback And Feedback CLI

**Files:**
- Create: `hermes_knowledge/feedback.py`
- Modify: `hermes_knowledge/cli.py`
- Test: `tests/test_hermes_feedback.py`
- Test: `tests/test_hermes_book_cli.py`

- [ ] Write failing tests for walk-forward improvement/deterioration, drawdown reduction/increase, gate pass/fail, priority clamping, missing metrics, malformed events, event-ID deduplication, book aggregation, and the `feedback` CLI.
- [ ] Run focused feedback tests and verify failure because feedback logic is absent.
- [ ] Implement constant-weight deterministic deltas, append-only accepted feedback events, and atomic `priorities.json` overlays without editing extracted notes.
- [ ] Run focused feedback and CLI tests.
- [ ] Commit the feedback slice.

### Task 8: Verification And Safety Audit

**Files:**
- Modify only files required to correct verified regressions.

- [ ] Run all new focused tests together.
- [ ] Run existing Hermes book, provider, runner, queue, schema, reporting, and risk-guidance tests.
- [ ] Run full `pytest -q` if feasible.
- [ ] Run `git diff --check` and inspect the complete diff.
- [ ] Confirm no private PDFs, indexes, sidecars, passages, notes, feedback, reports, logs, registry runtime data, credentials, or environment files are tracked.
- [ ] Confirm no strategy implementation, validation gate, promotion gate, deployment, sync, provider runtime, systemd, timer, or service-management behavior changed.
