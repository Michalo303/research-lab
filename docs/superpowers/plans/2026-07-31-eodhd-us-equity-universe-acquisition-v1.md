# EODHD US Equity Universe Acquisition V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acquire one resumable, immutable, point-in-time-aware US common-stock dataset through 2022-12-31 and immediately use it for the already implemented real-Qlib eight-factor economic screen.

**Architecture:** A closed-world acquisition contract creates a sanitized request plan. A resumable local executor stores deterministic gzip raw responses and normalized per-symbol CSV files in a request-bound staging directory. A memory-bounded SQLite reduction derives monthly major-exchange membership and the union of daily top-1,500 eligible instruments, then publishes the exact existing Qlib manifest atomically.

**Tech Stack:** Python 3.12 standard library, pandas, SQLite, urllib, pytest, existing `eodhd_qlib_dataset_v1`, existing `real_qlib_eodhd_edge_discovery_pilot_v1`, EODHD read-only APIs.

---

## Scope and file map

**Create:**

- `research_lab/research/eodhd_us_equity_universe_acquisition_v1.py` — request validation, identity normalization, sanitized provider plan, resumable downloads, raw/CSV validation, call accounting, and final artifact publication.
- `research_lab/research/eodhd_us_equity_universe_selection_v1.py` — monthly membership validation, SQLite candidate reduction, top-1,500 union, and `eodhd_qlib_dataset_manifest_v1` creation.
- `scripts/run_eodhd_us_equity_universe_acquisition_v1.py` — hash-bound dry-run/execute CLI.
- `tests/test_eodhd_us_equity_universe_acquisition_v1.py` — acquisition contract, security, retry, resume, and artifact tests.
- `tests/test_eodhd_us_equity_universe_selection_v1.py` — point-in-time selection and manifest tests.
- `tests/test_run_eodhd_us_equity_universe_acquisition_v1.py` — CLI and atomic-publication tests.

**Modify:**

- `research_lab/research/__init__.py` — export the top-level acquisition entry point only.

No existing strategy, factor, gate, ledger, broker, registry, deployment, or sealed-OOS module is changed.

### Task 1: Freeze the acquisition contract and provider plan

**Files:**

- Create: `research_lab/research/eodhd_us_equity_universe_acquisition_v1.py`
- Test: `tests/test_eodhd_us_equity_universe_acquisition_v1.py`

- [ ] **Step 1: Write failing closed-world request tests**

Define a helper request containing only:

```python
{
    "version": "eodhd_us_equity_universe_acquisition_request_v1",
    "acquisition_id": "EODHD-US-EQUITY-2006-2022-V1",
    "output_dir": str((tmp_path / "final").resolve()),
    "provider": "EODHD",
    "approved_host": "eodhd.com",
    "start_date": "2006-01-01",
    "end_date": "2022-12-31",
    "maximum_call_units": 90000,
    "maximum_attempts_per_request": 2,
    "history_concurrency": 8,
    "timeout_seconds": 90,
    "maximum_symbol_response_bytes": 2_000_000,
    "maximum_bulk_response_bytes": 20_000_000,
    "provenance": {"source": "operator_approved_eodhd_acquisition_v1"},
}
```

Required tests reject unknown fields, relative or in-repository output paths, symlinks, changed dates, host/provider drift, changed call/attempt/concurrency limits, and any caller-supplied universe, factor, token, or endpoint list.

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_eodhd_us_equity_universe_acquisition_v1.py -q
```

Expected: collection fails because the module does not exist.

- [ ] **Step 3: Implement immutable constants and sanitized plan construction**

Expose:

```python
def build_eodhd_us_equity_acquisition_plan_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Validate the frozen request and return a credential-free plan."""


def run_eodhd_us_equity_universe_acquisition_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Execute or resume the exact local acquisition and publish only when complete."""
```

The plan contains the three initial request identities, fixed cost rules (`bulk=100`, all other requests `=1`), exact thresholds, supported exchanges/MICs, retry policy, output identity, request SHA-256, `sealed_oos_opened=False`, and no credential or authorized URL.

- [ ] **Step 4: Add identity and SPY-month-end plan tests**

Test exact duplicate collapse, conflicting code rejection, common-stock/USD/major-exchange filtering, MIC mapping, unsafe symbol rejection, and deterministic ordering. Supply a synthetic SPY history spanning several months and assert exactly the last observed session per calendar month is selected. Assert no selected date exceeds 2022-12-31.

- [ ] **Step 5: Implement the pure helpers and make Task 1 green**

Use a strict symbol regex, canonical JSON hashes, and fixed endpoint identities without query tokens. Run the Task 1 test command and expect PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add research_lab/research/eodhd_us_equity_universe_acquisition_v1.py tests/test_eodhd_us_equity_universe_acquisition_v1.py
git commit -m "feat: add bounded EODHD universe acquisition contract"
```

### Task 2: Add resumable bounded downloads and immutable raw/CSV artifacts

**Files:**

- Modify: `research_lab/research/eodhd_us_equity_universe_acquisition_v1.py`
- Modify: `tests/test_eodhd_us_equity_universe_acquisition_v1.py`

- [ ] **Step 1: Write failing execution tests**

Use a private downloader monkeypatch; the public function accepts no API key or HTTP callback. Required tests:

- missing `EODHD_API_KEY` returns `EODHD_API_KEY_UNAVAILABLE` with zero calls and no final directory;
- active, delisted, SPY, month-end bulk, and symbol-history requests occur in deterministic phases;
- a token, encoded token, and token-bearing URL never occur in output, exceptions, checkpoint rows, or captured stdout;
- HTTP 429/5xx/timeout retries once, HTTP 4xx other than 429 does not retry, and no third attempt occurs;
- the executor refuses the next request before its call cost would exceed 90,000;
- response-size, host/path drift, malformed JSON, unordered/duplicate/out-of-range dates, and non-finite or invalid OHLC in SPY or per-symbol histories fail closed; membership-only bulk rows validate bounded identity/date/endpoint and their unused price fields are ignored;
- zero-row history becomes `RESOLVED_EMPTY`, while an unresolved response remains a failure;
- resume verifies the canonical request hash and all existing artifact hashes before skipping a request;
- altered staged evidence returns `STAGING_HASH_MISMATCH` without overwriting it.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the Task 1 command. Expected: new execution tests fail because execution is not implemented.

- [ ] **Step 3: Implement deterministic staging and state**

Use a sibling directory named `.EODHD-US-EQUITY-2006-2022-V1-<request-hash-prefix>.partial`. Store mutable resume state in SQLite with the request hash, sanitized endpoint identity, request kind, call cost, attempt count, terminal status, raw path/hash, normalized path/hash, byte count, and row count.

Write every raw payload as deterministic `gzip.compress(raw, mtime=0)` through a temporary file, reread and hash it, then atomically rename. Write normalized CSV with exact columns:

```text
timestamp,open,high,low,close,adjusted_close,volume
```

Partition symbol files by the first two characters of SHA-256(instrument ID) to avoid one huge directory. Commit SQLite state only after artifact verification.

- [ ] **Step 4: Implement bounded network execution**

Read the token only from `EODHD_API_KEY`. Use HTTPS, an exact host/path allowlist, no redirects, response byte caps, a short socket-idle timeout plus a 90-second total wall-clock response limit, and sanitized fixed failures. Active/delisted/SPY/bulk phases are sequential. Symbol histories use `ThreadPoolExecutor(max_workers=8)` while the main thread owns call reservation, state commits, and deterministic artifact ordinals.

The nominal three-exchange plan must fit under 90,000 before any bulk request starts. Remaining capacity is a retry reserve. Each transient request may retry once, but the atomic global call-budget reservation always refuses an attempt that would exceed 90,000. If the live identity count makes the nominal plan impossible, return `CALL_BUDGET_PREFLIGHT_FAILED` before bulk or history calls.

- [ ] **Step 5: Run acquisition tests and verify GREEN**

Run the Task 1 command. Expected: all acquisition tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add research_lab/research/eodhd_us_equity_universe_acquisition_v1.py tests/test_eodhd_us_equity_universe_acquisition_v1.py
git commit -m "feat: add resumable EODHD universe downloads"
```

### Task 3: Build the memory-bounded point-in-time Qlib manifest

**Files:**

- Create: `research_lab/research/eodhd_us_equity_universe_selection_v1.py`
- Create: `tests/test_eodhd_us_equity_universe_selection_v1.py`
- Modify: `research_lab/research/eodhd_us_equity_universe_acquisition_v1.py`

- [ ] **Step 1: Write failing membership and trimming tests**

Create synthetic active/delisted metadata, monthly bulk snapshots, and EOD histories. Assert:

- only common-stock identities present on supported major exchanges in a monthly snapshot continue;
- normalized rows are trimmed from the first through last verified major-exchange month-end;
- a material internal membership gap with continuing EOD rows is rejected as `AMBIGUOUS_EXCHANGE_MEMBERSHIP`;
- pre-membership OTC-like history and post-membership history are absent from CSV;
- active instruments use `listing_end=None`, while delisted instruments use their last retained row;
- the manifest uses `XNAS`, `XNYS`, or `XASE`, relative CSV paths, and exact raw-byte CSV SHA-256.

- [ ] **Step 2: Write failing SQLite top-universe tests**

Use 40 synthetic instruments and a private test limit of 30. Assert 252 prior sessions, USD 5 raw close, 63-session median raw dollar volume USD 10 million, deterministic liquidity/instrument tie-breaking, per-date cap, and union-of-selected-instruments behavior. Assert that later observations cannot affect earlier eligibility.

- [ ] **Step 3: Run selection tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_eodhd_us_equity_universe_selection_v1.py -q
```

Expected: collection fails because the selection module does not exist.

- [ ] **Step 4: Implement streaming candidates and SQL selection**

Expose:

```python
def build_point_in_time_qlib_manifest_v1(
    *,
    staging_root: Path,
    state_connection: sqlite3.Connection,
) -> dict[str, object]:
    """Build and hash the selected Qlib manifest without loading all histories together."""
```

Read one CSV at a time with pandas, compute point-in-time eligibility, and batch eligible triples into SQLite. Use `ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY liquidity DESC, instrument_id ASC)` and retain ranks at most 1,500. Build the union and manifest in sorted instrument-ID order.

The selection report includes daily counts, minimum and median development cross-section, candidate/selected rows, selected instrument count, active/delisted counts, membership exclusions, empty histories, unresolved failures, and canonical SHA-256.

- [ ] **Step 5: Add production acceptance tests**

Reject any 2023+ row, absolute/symlink/escaping CSV path, manifest schema drift, more than 1% unresolved identity failures, fewer than 30 development instruments on any evaluated cross-section, inconsistent call accounting, or a manifest not accepted by the existing `_validate_manifest` contract.

- [ ] **Step 6: Run Task 3 tests and relevant existing loader tests**

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_eodhd_us_equity_universe_selection_v1.py tests/test_eodhd_qlib_dataset_v1.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add research_lab/research/eodhd_us_equity_universe_selection_v1.py research_lab/research/eodhd_us_equity_universe_acquisition_v1.py tests/test_eodhd_us_equity_universe_selection_v1.py
git commit -m "feat: build point-in-time Qlib universe manifest"
```

### Task 4: Add hash-bound CLI and atomic publication

**Files:**

- Create: `scripts/run_eodhd_us_equity_universe_acquisition_v1.py`
- Create: `tests/test_run_eodhd_us_equity_universe_acquisition_v1.py`
- Modify: `research_lab/research/__init__.py`

- [ ] **Step 1: Write failing CLI tests**

CLI arguments are exactly:

```text
--request ABSOLUTE_JSON_PATH
--expected-request-sha256 LOWERCASE_64_HEX
--execute
```

Test dry-run writes nothing and reports planned phases/call ceiling; execute requires exact request hash and nonexistent final output; an interrupted run leaves resumable staging but no final output; success writes `COMPLETE` last and atomically renames staging; fixed exit codes distinguish success, missing key, validation, budget, unresolved provider data, and I/O failure; captured output is secret-free.

- [ ] **Step 2: Run CLI tests and verify RED**

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_run_eodhd_us_equity_universe_acquisition_v1.py -q
```

Expected: collection fails because the CLI does not exist.

- [ ] **Step 3: Implement the CLI and final bundle**

The final root contains `COMPLETE`, `request.json`, `acquisition_plan.json`, `raw_manifest.json`, `membership_report.json`, `selection_report.json`, `dataset_manifest.json`, `checksums.json`, compressed raw directories, and normalized `ohlcv/` files. `COMPLETE` is written last inside staging; every hash is reread; staging is atomically renamed to the absent final path.

Export only `run_eodhd_us_equity_universe_acquisition_v1` from `research_lab/research/__init__.py`.

- [ ] **Step 4: Run CLI, acquisition, selection, and loader tests**

Run the three new test files plus `tests/test_eodhd_qlib_dataset_v1.py`. Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```powershell
git add scripts/run_eodhd_us_equity_universe_acquisition_v1.py research_lab/research/__init__.py tests/test_run_eodhd_us_equity_universe_acquisition_v1.py
git commit -m "feat: add immutable EODHD universe acquisition CLI"
```

### Task 5: Verify, review, merge, acquire, and run the economic screen

**Files:**

- Modify only milestone files if verification exposes a defect.
- Create request/result artifacts only outside the repository.

- [ ] **Step 1: Run focused and full verification**

Run all three new test files, existing Qlib loader/pilot/CLI tests, genuine Qlib integration in its isolated environment, full pytest, `py_compile`, `git diff --check`, and a secret/action-surface scan. Expected: every test passes; only the known main-environment Qlib skip and existing warnings remain.

- [ ] **Step 2: Perform strict precommit review**

Review for P0/P1/P2 findings: token leakage, incorrect call units, retry overspend, nondeterministic resume, survivor bias, future exchange membership, sealed rows, incorrect adjusted-price normalization, selection look-ahead, manifest mismatch, and non-atomic finalization. Repair and rerun all affected checks.

- [ ] **Step 3: Commit, push, open a draft PR, obtain strict review, and merge only on PASS**

Stage only the milestone files. Preserve unrelated worktrees and drift. PR text must state that no economic result exists yet and that live/broker/promotion/deployment remain unauthorized.

- [ ] **Step 4: Create and dry-run the exact local acquisition request**

Use an output directory outside the repository and a request whose SHA-256 is recorded before execution. Dry-run must report no writes and zero calls.

- [ ] **Step 5: Execute or resume the bounded local acquisition**

Load `EODHD_API_KEY` only from the local environment. Monitor progress without printing the key or URLs. Continue until a complete hash-verified manifest exists or a fixed fail-closed status is reached.

- [ ] **Step 6: Locate and validate the canonical prior experiment ledger**

Validate the full ledger, policy, M32A binding, hypotheses, trials, sealed-consumption records, and at least eight remaining price/volume and global attempts before any Qlib observation. If no authoritative ledger exists, stop with `LEDGER_BINDING_FAILED`; do not invent one after seeing data.

- [ ] **Step 7: Build one exact Qlib request and run once**

Bind the final dataset manifest SHA-256 and prior ledger SHA-256. Use discovery 2006-2018, development 2019-2022, sealed start 2023-01-01, 15/30/50 bps costs, and a new output directory. Run the existing genuine-Qlib CLI once.

- [ ] **Step 8: Report the economic verdict without exaggeration**

Report each factor's RankIC, annualized net top-minus-universe return, stress/severe result, yearly consistency, concentration, and `FACTOR_CONTINUE`/`FACTOR_STOP`. Explicitly state that this is not portfolio CAGR or maximum drawdown. Continue to portfolio construction only for retained factors; otherwise stop price/volume work and start the Sharadar branch.
