# EODHD Search Approval V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the superseded review-only EODHD identity chain with a replayable, externally approval-bound, dry-run-safe M31N–M31P contract.

**Architecture:** M31N derives a deterministic capability manifest solely from the exact merged M31I manifest and compact official EODHD evidence. M31O accepts only an externally supplied M31P approval-manifest hash plus a single immutable plan record and validates an injected fake client response. M31P builds the only executable approval manifest and embeds each replayable adapter request without ever performing a provider call.

**Tech Stack:** Python standard library, committed JSON evidence, pytest, git/GitHub/Hetzner validation.

---

### Task 1: M31N evidence and manifest

**Files:**
- Create: `research_lab/evidence/eodhd_exact_identity_capability_v2.json`
- Create: `research_lab/execution/eodhd_exact_identity_capability_v2.py`
- Modify: `research_lab/execution/__init__.py`
- Test: `tests/test_eodhd_exact_identity_capability_v2.py`

- [ ] Write failing tests that compose all exact M31I identities, reject tampered M31I/evidence records, distinguish lower-case search parameters from response categories, enforce MIC membership/namespace classification, preserve 4GLD and USO taxonomy, and prove deep immutability/deterministic hashes.
- [ ] Run `C:\Users\lojka\trading\research-lab\.venv\Scripts\python -m pytest tests/test_eodhd_exact_identity_capability_v2.py -q`; expect import failure before implementation.
- [ ] Implement canonical JSON hashing, closed-world validation of the 15 M31I identities, structured official Search and exchange evidence, per-record mapping hashes, and no-I/O safety fields.
- [ ] Re-run the dedicated test; expect all tests to pass.
- [ ] Commit only the M31N files with `git add` exact paths and message `feat: add eodhd exact identity capability v2`.

### Task 2: M31O approval-bound adapter

**Files:**
- Create: `research_lab/eodhd_approval_bound_search_metadata_adapter_v2.py`
- Test: `tests/test_eodhd_approval_bound_search_metadata_adapter_v2.py`

- [ ] Write failing tests for dry run, every external-hash/plan/record/budget rejection, credential redaction, one fake client call, response parsing, type policy, namespace review, deterministic hashes, and deep immutability.
- [ ] Run `C:\Users\lojka\trading\research-lab\.venv\Scripts\python -m pytest tests/test_eodhd_approval_bound_search_metadata_adapter_v2.py -q`; expect import failure.
- [ ] Implement `DRY_RUN` and `APPROVED_EXECUTION`; construct exactly `/api/search/{ISIN}` with `{exchange,type,limit:10,fmt:json}`; require an external M31P hash; consume an injected one-call ledger; and never write files or expose credentials.
- [ ] Re-run the dedicated test; expect all tests to pass.
- [ ] Commit only M31O files with message `feat: add approval bound eodhd search adapter v2`.

### Task 3: M31P readiness and integration

**Files:**
- Create: `research_lab/execution/eodhd_exact_symbol_resolution_readiness_v3.py`
- Modify: `research_lab/execution/__init__.py`
- Test: `tests/test_eodhd_exact_symbol_resolution_readiness_v3.py`

- [ ] Write failing tests for deterministic complete/blocked records, path safety, exact budgets, superseded hashes, approval-manifest hashing, and M31P-to-M31O fake-client approval binding.
- [ ] Run `C:\Users\lojka\trading\research-lab\.venv\Scripts\python -m pytest tests/test_eodhd_exact_symbol_resolution_readiness_v3.py -q`; expect import failure.
- [ ] Implement fully replayable records below the required pending V3 root, reject unsafe destinations, record zero non-metadata calls, and emit `HUMAN_APPROVAL_REQUIRED_FOR_CONTROLLED_EODHD_SEARCH_RESOLUTION_V2` only when exact authorizable records exist.
- [ ] Re-run dedicated plus focused M31I/M31N/M31O tests; expect all to pass.
- [ ] Commit only M31P files with message `feat: add exact symbol resolution readiness v3`.

### Task 4: Final verification and lifecycle

- [ ] Run `python -m compileall research_lab`, `git diff --check`, the dedicated and focused suites, then one full `C:\Users\lojka\trading\research-lab\.venv\Scripts\python -m pytest` after final behavior changes.
- [ ] Strictly review P0/P1/P2 risks: external approval cannot be self-computed, types are not conflated, selected MIC evidence is explicit, destinations are safe, and credentials remain redacted.
- [ ] Push, open/review/merge each milestone only when its checks pass; fast-forward the separate main worktree; verify GitHub main; synchronize `hetzner-research`; run its dedicated, focused, then full suites; and verify clean aligned states.
