# Massive Fundamental Edge Discovery V1 Implementation Plan

**Goal:** Acquire a hash-bound Massive fundamental snapshot for the existing EODHD universe and run one ten-factor development-only economic screen that either names a credible candidate or stops the branch.

**Architecture:** Keep online acquisition separate from offline evaluation. A closed request and resumable executor create immutable per-ticker raw/normalized artifacts. A point-in-time loader joins filings to the next verified EODHD session, a frozen factor catalog builds ten factor panels, and one deterministic weekly portfolio evaluator produces directly comparable return/risk evidence. A coordinator validates and appends all attempts to the existing global ledger and writes an immutable scorecard bundle.

**Tech stack:** Python 3.12, standard library HTTP/gzip/sqlite/hashlib/json, pandas, NumPy, pytest, existing EODHD dataset and global-ledger contracts.

## Task 1: Freeze provider and factor contracts

**Files:**

- Create `research_lab/research/massive_fundamental_catalog_v1.py`
- Create `tests/test_massive_fundamental_catalog_v1.py`

Write failing tests for the exact ordered ten-factor identity, directions, component fields, quarterly history requirements, composite weights, and canonical hash. Implement only enough pure code to pass. Reject unknown catalog fields and nonfinite inputs.

## Task 2: Add closed acquisition preparation

**Files:**

- Create `research_lab/research/massive_fundamental_acquisition_v1.py`
- Create `tests/test_massive_fundamental_acquisition_v1.py`

Write failing tests for exact EODHD manifest SHA validation, the 3,914-symbol frozen universe, fixed provider endpoint, bounded pages/calls/timeouts/rate, secret-free request artifacts, and zero provider calls during preparation. Implement canonical request and acquisition-plan builders.

## Task 3: Add resumable and redacted provider execution

**Files:**

- Modify `research_lab/research/massive_fundamental_acquisition_v1.py`
- Extend `tests/test_massive_fundamental_acquisition_v1.py`
- Create `scripts/run_massive_fundamental_acquisition_v1.py`

Write failing tests for deterministic gzip raw responses, page cursors, SQLite resume, 429 backoff, bounded HTTP wall time, retries, call accounting, API-key redaction, partial completion, and idempotent resume. The production CLI reads `MASSIVE_API_KEY` only for explicit `execute`; `prepare` and `verify` must not access it.

## Task 4: Normalize issuer-bound statements

**Files:**

- Create `research_lab/research/massive_fundamental_dataset_v1.py`
- Create `tests/test_massive_fundamental_dataset_v1.py`

Write failing tests for unique-CIK binding, multiple-CIK rejection, filing-date availability, next-session lag, later restatements, annual/quarterly-only filtering, TTM/ratio rejection, exact period alignment, no imputation, checksum verification, and sealed-row exclusion. Emit compact normalized per-ticker artifacts and a coverage report.

## Task 5: Build ten point-in-time factor panels

**Files:**

- Modify `research_lab/research/massive_fundamental_catalog_v1.py`
- Extend `tests/test_massive_fundamental_catalog_v1.py`

Write synthetic tests for trailing-four-quarter flows, annual-minus-Q1-to-Q3 Q4 reconstruction, prior-year comparisons, balance values, margins, low asset growth, and the fixed quality-momentum composite. Prove that filings are invisible before the next session and that a later restatement cannot change earlier values. Do not mix split-adjusted prices with vendor share counts.

## Task 6: Add direct weekly portfolio economics

**Files:**

- Create `research_lab/research/fundamental_portfolio_screen_v1.py`
- Create `tests/test_fundamental_portfolio_screen_v1.py`

Write failing tests for weekly ranking, next-session execution, top-15 equal weights, cash, turnover, 15/30/50-bps costs, SPY and universe baselines, CAGR, drawdown, Sharpe, Sortino, Calmar, annual returns, rolling-12-month loss, coverage, and contribution concentration. Test every continuation veto independently.

## Task 7: Audit Massive values against official SEC filings

**Files:**

- Create `research_lab/research/massive_sec_filing_audit_v1.py`
- Create `tests/test_massive_sec_filing_audit_v1.py`
- Create `scripts/run_massive_sec_filing_audit_v1.py`

Deterministically select 30 latest annual filings from distinct CIKs with atomic SEC accession identities. Compare accession, filing date, period end, and at least three standardized USD values against SEC Company Facts. Store bounded raw SEC responses and hashes in a private immutable audit bundle. Read `SEC_USER_AGENT` only from process environment. A missing, failed, stale, or tampered audit must stop the economic coordinator before price loading and trial consumption.

## Task 8: Bind the ledger, SEC audit, and immutable scorecard

**Files:**

- Create `research_lab/research/massive_fundamental_edge_discovery_v1.py`
- Create `tests/test_massive_fundamental_edge_discovery_v1.py`
- Create `scripts/run_massive_fundamental_edge_discovery_v1.py`

Write failing tests requiring the exact previous Qlib-updated ledger SHA, at least ten remaining family/global attempts, ten new hypotheses/trials, retained failures, zero sealed consumption, zero online calls during evaluation, and output status limited to `FUNDAMENTAL_EDGE_CANDIDATE_FOUND`, `NO_FUNDAMENTAL_EDGE`, or precise fail-closed states. Build an immutable request-bound bundle with complete checksums and deterministic replay.

## Task 9: Verify implementation before provider use

Run focused tests after every task. Then run all fundamental, EODHD-dataset, Qlib-pilot, ledger, and promotion-gate tests; full pytest; `py_compile`; `git diff --check`; secret scan; and a review focused on look-ahead, identity ambiguity, pagination, rate limiting, cost timing, and experiment accounting.

No provider call occurs until these checks pass.

## Task 10: Acquire without language-model supervision

Create the exact request using:

- source manifest `C:\Users\lojka\trading-private\research-lab\eodhd-us-equity-2006-2022-v1\dataset_manifest.json`;
- expected manifest SHA `bbd610ac97946e20f2c0405fbeede75b6fff321e9504b4c630e538b2f83cee7e`;
- filing interval 2009-01-01 through 2022-12-31;
- a new private output directory.

Run the downloader as a hidden, resumable process with the Massive credential inherited for that process only. Poll the public progress artifact rather than using an LLM loop. On completion, verify every checksum and publish the exact coverage/ambiguity report.

If fewer than 500 eligible instruments have point-in-time factor coverage in the median development week, stop with `FUNDAMENTAL_COVERAGE_INSUFFICIENT` before consuming the ten economic trials.

Run and bind the 30-record SEC audit after acquisition succeeds and before any economic trial.

## Task 11: Run once and decide

Bind the completed fundamental manifest, the passing SEC audit and its canonical SHA, the EODHD manifest, and the Qlib pilot updated-ledger SHA. Dry-run first to prove ten attempts, zero provider calls, zero sealed reads, complete accounting, and an unmodified passing SEC audit. Then execute exactly once on 2019-2022 development data.

- If at least one factor passes every continuation criterion, return `FUNDAMENTAL_EDGE_CANDIDATE_FOUND` and authorize only one separately designed portfolio-construction test.
- Otherwise return `NO_FUNDAMENTAL_EDGE`, stop the stock-selection branch, and move to the already approved diversified-ETF trend fallback without tuning these factors.

## Task 12: Finalize the reviewed code

After implementation and all tests pass, perform strict precommit review, commit intentionally, push, open a draft PR, inspect GitHub checks/review, merge only on PASS, verify local/origin/GitHub alignment, and sync Hetzner through the existing repository-managed safe-sync wrapper. Provider result artifacts remain private and are never committed.
