# Controlled EODHD Search Batch Executor V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, approval-bound M31Q executor that can perform only the 15 exact authorized EODHD Search resolutions after all offline validation passes.

**Architecture:** A single execution module owns pure contract validation, deterministic scheduling, injected atomic journal/result-store implementations, and a one-call-per-record M31O coordinator. The module deep-copies all inputs, derives authoritative call accounting from persistent journal markers, and keeps provider credentials confined to the injected M31O call.

**Tech Stack:** Python 3.12, stdlib `copy`, `hashlib`, `json`, `pathlib`, `os`, `tempfile`, `pytest`.

---

## File structure

- Create `research_lab/execution/controlled_eodhd_search_batch_executor_v1.py`: constants, strict request validation, schedule construction, in-memory/filesystem protocols, and executor.
- Modify `research_lab/execution/__init__.py`: narrow public export of executor and test fakes where repository convention allows.
- Create `tests/test_controlled_eodhd_search_batch_executor_v1.py`: offline fake-client, journal, result-store, crash, batch, redaction, and path tests.
- Create `docs/superpowers/plans/2026-07-17-controlled-eodhd-search-batch-executor-v1.md`: this committed implementation record.

### Task 1: Strict request and upstream validation

**Files:** Create `tests/test_controlled_eodhd_search_batch_executor_v1.py`; create `research_lab/execution/controlled_eodhd_search_batch_executor_v1.py`.

- [ ] Write failing tests using rebuilt M31I/M31N/M31P fixtures for exact fixed authorization acceptance and each one-field mutation: approval hash, plan hash, M31I hash, M31N hash, M31O version, readiness result, approval manifest, record hash, missing record, duplicate record, reordered record, duplicate sequence, duplicate destination, unsafe destination, and nonzero historical/corporate-action/calendar budget.
- [ ] Run `.venv\\Scripts\\python -m pytest tests/test_controlled_eodhd_search_batch_executor_v1.py -k validation -v`; expect import/attribute failure before module exists.
- [ ] Implement canonical JSON SHA-256, deep-copy validation, exact request-field sets, recomputation of M31I/M31N/M31P/record/plan/manifest hashes, fixed authorization constants, and `ValidationError` statuses.
- [ ] Re-run the focused command; expect all validation tests pass with zero client calls.
- [ ] Commit implementation and test paths only if this independently reviewable boundary is complete: `feat: validate M31Q authorization chain`.

### Task 2: Deterministic schedule and DRY_RUN

**Files:** Modify the same module and test file.

- [ ] Write failing tests asserting exact 15 ascending sequences, immutable schedule/output, deterministic repeat output, exact M31P paths/parameters, and DRY_RUN zero client, credential, journal, result-store, and private filesystem activity.
- [ ] Run the DRY_RUN tests and observe the missing runner/schedule assertion failure.
- [ ] Implement `build_controlled_eodhd_search_schedule_v1` and `run_controlled_eodhd_search_batch_v1`; make DRY_RUN terminate after pure validation with fixed zero safety fields.
- [ ] Run `.venv\\Scripts\\python -m pytest tests/test_controlled_eodhd_search_batch_executor_v1.py -k 'dry_run or schedule' -v`; expect pass.

### Task 3: Injected atomic journal and result-store protocols

**Files:** Modify module and test file.

- [ ] Write failing tests for memory journal exclusive intent/start/completion/summary, duplicate refusal, sequence inspection/listing, and temporary filesystem journal no-overwrite behavior; add result-store tests for exclusive publication, traversal, duplicate, outside-root, symlink/canonical/ SPY collision refusal.
- [ ] Run `-k 'journal or result_store or path_safety'`; expect protocol class/import failure.
- [ ] Implement `InMemoryExecutionJournal`, `FilesystemExecutionJournal`, `InMemoryResultStore`, and `FilesystemResultStore` with canonical JSON, exclusive create, temporary-plus-atomic rename, and `fsync` where available. Make the real root exact and run identity approval-hash derived.
- [ ] Re-run the focused tests; expect all pass without deletion or overwrite behavior.

### Task 4: Intent, start markers, and replay refusal

**Files:** Modify module and test file.

- [ ] Write failing tests proving intent exists before the first fake client observation, each start marker exists before its client observation, an existing intent rejects a second run, started-without-completed returns `MANUAL_REVIEW_REQUIRED_POSSIBLE_CALL_ALREADY_CONSUMED`, completed sequences cannot replay, and caller ledger zero cannot bypass persistent state.
- [ ] Run `-k 'intent or started or replay or ledger'`; expect executor ordering assertion failure.
- [ ] Implement journal reconciliation before the run and each sequence; derive attempted/completed accounting only from journal state; create intent and `CALL_STARTED` exclusively before invoking M31O.
- [ ] Re-run focused tests; expect pass and no provider call on rejection.

### Task 5: One-call M31O coordinator and completed artifacts

**Files:** Modify module and test file.

- [ ] Write failing tests for one exact M31O client path/parameters per record, exactly one fake call, adapter result validation, raw-response/adaptor-result hashes, result persistence before completion marker, and no credential text in output, journal, store, or exception.
- [ ] Run `-k 'coordinator or completed or credential or adapter'`; expect missing completion flow failure.
- [ ] Implement a coordinator that supplies the exact M31O request and journal-derived ledger, invokes M31O once, increments attempted accounting exactly once after an attempted call, validates result statuses, stores redacted artifacts, then creates `CALL_COMPLETED`.
- [ ] Re-run focused tests; expect pass and credentials absent from serialized output.

### Task 6: Fail-closed and review-required behavior

**Files:** Modify module and test file.

- [ ] Write failing tests for HTTP exception, malformed response, response limit excess, adapter `FAILED_VALIDATION`, result-store failure, completed-marker failure, budget exhaustion, zero retry/fallback/pagination/health, first transport failure stopping later calls, and persisted review-required no-match/ambiguity/type-taxonomy/namespace continuing the batch.
- [ ] Run `-k 'failure or transport or review_required or stop'`; expect fail before failure policy exists.
- [ ] Implement stable stop statuses and exception redaction. Treat only the four persisted review-required statuses as completed continuation outcomes; every structural/transport/persistence failure stops before the next provider call.
- [ ] Re-run focused tests; expect pass with exact uncalled sequence lists.

### Task 7: Crash windows and summary reconciliation

**Files:** Modify module and test file.

- [ ] Write failing tests simulating failure before intent, after intent, after start before client, client exception during request, response/result-store failure, result stored/completed-marker failure, and summary failure after all completions. Assert every uncertain started state blocks automatic replay and a missing summary remains manual-review-only.
- [ ] Run `-k 'crash or summary or uncertain'`; expect failure until reconciliation exists.
- [ ] Implement only fail-closed reconciliation and exclusive summary publication; do not add resume/retry behavior.
- [ ] Re-run focused tests; expect all crash/replay tests pass.

### Task 8: Complete deterministic 15-call batch

**Files:** Modify module and test file.

- [ ] Write failing test using a fake client response per authorized record and assert exact 15-call order, no SPY/forbidden endpoint, total calls <=15, every completed sequence, deterministic summary hash, raw/adaptor hashes, resolved symbols, review reasons, and zero forbidden counters.
- [ ] Run `-k complete_15_call_batch -v`; expect failure prior to complete coordinator behavior.
- [ ] Complete only the minimum loop needed to satisfy the 15-record schedule and final immutable audit summary.
- [ ] Run `.venv\\Scripts\\python -m pytest tests/test_controlled_eodhd_search_batch_executor_v1.py -v`; expect all dedicated M31Q tests pass.

### Task 9: Public export, compilation, and strict review

**Files:** Modify `research_lab/execution/__init__.py`; inspect all three M31Q files.

- [ ] Write a failing import test from `research_lab.execution` for the public runner.
- [ ] Run its node-id; expect ImportError before export.
- [ ] Add the exact export and `__all__` entry without modifying M31I/M31N/M31O/M31P behavior.
- [ ] Run dedicated M31Q tests; focused M31I/M31N/M31O/M31P/M31Q tests; `python -m py_compile research_lab/execution/controlled_eodhd_search_batch_executor_v1.py`; and `git diff --check`; expect zero failures.
- [ ] Perform P0/P1/P2 review for self-generated approval, hash/membership bypass, intent/start ordering, replay, overwrite/cleanup, credential leakage, hidden requests, unsafe paths, clocks, forbidden calls, and production enablement. Add failing regression tests before repairing any finding.
- [ ] Commit exact implementation, export, and test paths: `feat: add controlled EODHD search batch executor`.

### Task 10: Final local verification and PR lifecycle

**Files:** Inspect exact branch diff only.

- [ ] Run dedicated M31Q, focused M31I/M31N/M31O/M31P/M31Q, complete fake batch, crash/replay, path safety, compilation, and `git diff --check`; record totals, warnings, elapsed time, and exit statuses.
- [ ] Run `.venv\\Scripts\\python -m pytest` exactly once after the final behavior change, using redirected background output/polling if Windows stdout is unstable; record final total and elapsed time.
- [ ] Stage only intended M31Q/docs paths, inspect cached diff, push `codex/controlled-eodhd-search-batch-executor-v1`, create a ready PR, inspect its remote diff/comments/reviews/threads/base/head/merge base/divergence/mergeability/checks/protection/rulesets, then merge only on PASS without bypassing configured requirements.

### Task 11: Separate-main, Hetzner, and controlled execution

**Files:** No repository source changes; server private run root only after merged validation.

- [ ] Fast-forward only `C:/Users/lojka/trading/research-lab-volume-lineage-fix` from `origin/main`; verify its HEAD equals merge SHA.
- [ ] Use `ssh hetzner-research` to safely synchronize `/opt/trading/research-lab`; verify clean tracked/staged state and HEAD/origin main equal merge SHA.
- [ ] Run dedicated, focused, crash/replay, full Hetzner pytest, and `git diff --check`; require every result pass before credential access or real call.
- [ ] Rebuild M31I/M31N/M31P offline on merged Hetzner main; recompute and compare every fixed approval hash; inspect exact run root and require no intent/artifacts; load server credential without printing; invoke M31Q directly once in APPROVED_EXECUTION with at most 15 approved Search calls.
- [ ] Audit journal read-only for exact marker ordering, no duplicate/late calls, zero retries/fallback/pagination/health/forbidden calls/SPY/canonical mutation, credential absence, no promotions, and clean Git. Do not commit private artifacts.
