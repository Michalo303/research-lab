# Minervini EODHD Acquisition Pilot V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run one immutable, exactly 24-request EODHD pilot that measures whether a full survivorship-aware Minervini dataset can be acquired safely and economically.

**Architecture:** A pure planning/validation module owns endpoint construction, deterministic sampling, schema checks, identity analysis, and acquisition estimates. A separate artifact module owns secret-free raw-byte persistence, append-only journaling, atomic finalization, and deterministic replay. A thin CLI defaults to dry-run and is the only live entrypoint.

**Tech Stack:** Python 3.12 standard library, pytest, injected HTTP getter, SHA-256 canonical JSON, existing research-only safety conventions.

---

### Task 1: Frozen request plan and deterministic sample

**Files:**
- Create: `research_lab/research/minervini_eodhd_acquisition_pilot_v1.py`
- Create: `tests/test_minervini_eodhd_acquisition_pilot_v1.py`

- [ ] **Step 1: Write failing tests for the frozen plan**

Add fixtures for active and delisted common-stock rows and assert the public
planner produces exactly four metadata requests plus twenty EOD requests:

```python
def test_plan_has_exactly_24_secret_free_requests_and_deterministic_sample():
    plan = build_minervini_eodhd_acquisition_plan_v1(
        active_rows=_active_rows(),
        delisted_rows=_delisted_rows(),
    )

    assert plan["version"] == "minervini_eodhd_acquisition_plan_v1"
    assert plan["provider_request_limit"] == 24
    assert len(plan["sample_symbols"]) == 20
    assert plan["sample_symbols"][0] == "SPY.US"
    assert "AAPL.US" in plan["sample_symbols"]
    assert "ATVI.US" in plan["sample_symbols"]
    assert len(plan["request_specs"]) == 22
    assert all("api_token" not in item["endpoint_identity"] for item in plan["request_specs"])
```

The initial two universe requests cannot be planned from universe payloads, so
`request_specs` contains the symbol-change request, split request, and twenty
EOD requests. The executor prepends the two fixed universe requests.

Add independent tests for duplicate identities, active/delisted collisions,
non-common-stock rows, malformed provider codes, unstable input ordering, and
sample fill after anchor duplication.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py -q
```

Expected: collection fails because
`research_lab.research.minervini_eodhd_acquisition_pilot_v1` does not exist.

- [ ] **Step 3: Implement the frozen planner**

Expose:

```python
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"

def build_minervini_eodhd_acquisition_plan_v1(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Normalize the universe and construct the remaining 22 requests."""
```

Normalize only exact `Common Stock` rows. Provider codes must match
`[A-Z0-9][A-Z0-9._-]{0,31}` after uppercase normalization. Preserve provider
`Exchange`, `Currency`, and `Isin` as identity evidence; do not merge symbols
by name.

Rank non-anchor identities by:

```python
hashlib.sha256(
    f"{status}:{code}:{exchange}:{currency}:{isin or ''}".encode("utf-8")
).hexdigest()
```

Build the sample as `SPY.US`, `AAPL.US`, eight further active identities,
`ATVI.US`, and nine further delisted identities. Deduplicate while filling from
the next ranked identity until there are exactly twenty symbols.

Build sanitized endpoint identities for:

```text
/api/symbol-change-history?from=2010-01-01&to=2025-12-31&ex=US&fmt=json
/api/calendar/splits?from=2010-01-01&to=2025-12-31&fmt=json
/api/eod/{SYMBOL}?from=2010-01-01&to=2025-12-31&period=d&fmt=json
```

Do not accept a token in this function.

- [ ] **Step 4: Run the planner tests**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- research_lab/research/minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_eodhd_acquisition_pilot_v1.py
git commit -m "feat: add frozen Minervini acquisition plan"
```

### Task 2: Immutable artifact writer and replay

**Files:**
- Create: `research_lab/research/minervini_immutable_pilot_artifacts_v1.py`
- Create: `tests/test_minervini_immutable_pilot_artifacts_v1.py`

- [ ] **Step 1: Write failing artifact tests**

Test an empty temporary output directory:

```python
def test_writer_persists_exact_bytes_hashes_and_append_only_journal(tmp_path):
    writer = MinerviniPilotArtifactWriterV1.create(tmp_path / "run")
    raw = b'[{"Code":"AAPL","Type":"Common Stock"}]'

    record = writer.write_response(
        ordinal=1,
        artifact_name="active-common-stocks.json",
        endpoint_identity="https://eodhd.com/api/exchange-symbol-list/US?type=common_stock&fmt=json",
        http_status=200,
        raw_bytes=raw,
        retrieved_at_utc="2026-07-25T20:00:00Z",
        parsed_row_count=1,
        schema_status="VALID",
    )

    assert (tmp_path / "run" / "active-common-stocks.json").read_bytes() == raw
    assert record["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "api_token" not in (tmp_path / "run" / "request-journal.jsonl").read_text()
```

Also test refusal of relative paths, symlinks, non-empty directories, duplicate
ordinals, duplicate artifact names, path traversal, secret-bearing endpoint
identities, non-200 evidence, atomic final manifest, and replay rejection after
one raw byte changes.

- [ ] **Step 2: Run artifact tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_immutable_pilot_artifacts_v1.py -q
```

Expected: import failure for the new artifact module.

- [ ] **Step 3: Implement the writer**

Expose:

```python
@dataclass
class MinerviniPilotArtifactWriterV1:
    root: Path
    journal_path: Path
    used_ordinals: set[int]
    used_names: set[str]

    @classmethod
    def create(cls, output_dir: Path) -> "MinerviniPilotArtifactWriterV1": ...

    def write_response(
        self,
        *,
        ordinal: int,
        artifact_name: str,
        endpoint_identity: str,
        http_status: int,
        raw_bytes: bytes,
        retrieved_at_utc: str,
        parsed_row_count: int,
        schema_status: str,
    ) -> dict[str, object]: ...

    def finalize(self, result: Mapping[str, object]) -> Path: ...
```

Write raw responses with exclusive creation (`xb`). Append one canonical JSON
line per request and flush plus `os.fsync`. Write the final manifest to a
same-directory temporary file, fsync, then `os.replace`.

Expose:

```python
def replay_minervini_pilot_artifacts_v1(output_dir: Path) -> dict[str, object]:
    """Recompute every raw hash and the canonical manifest hash."""
```

Replay performs no network and no writes.

- [ ] **Step 4: Run artifact tests**

Run:

```powershell
python -m pytest tests/test_minervini_immutable_pilot_artifacts_v1.py -q
```

Expected: all artifact and replay tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- research_lab/research/minervini_immutable_pilot_artifacts_v1.py tests/test_minervini_immutable_pilot_artifacts_v1.py
git commit -m "feat: add immutable Minervini pilot artifacts"
```

### Task 3: Schema validation, identity analysis, and estimates

**Files:**
- Modify: `research_lab/research/minervini_eodhd_acquisition_pilot_v1.py`
- Modify: `tests/test_minervini_eodhd_acquisition_pilot_v1.py`

- [ ] **Step 1: Write failing validation tests**

Add tests for:

```python
def test_eod_validation_rejects_unordered_duplicate_and_invalid_ohlcv():
    with pytest.raises(ValueError, match="strictly ordered"):
        validate_minervini_eod_sample_v1(_unordered_eod())

def test_symbol_change_cycles_block_readiness():
    analysis = analyze_minervini_symbol_changes_v1(
        [
            {"old_symbol": "AAA", "new_symbol": "BBB", "effective": "2020-01-02", "exchange": "US"},
            {"old_symbol": "BBB", "new_symbol": "AAA", "effective": "2021-01-04", "exchange": "US"},
        ]
    )
    assert "SYMBOL_CHANGE_CYCLE" in analysis["blockers"]

def test_estimate_is_deterministic_and_never_claims_unknown_split_pages_exact():
    estimate = estimate_minervini_wide_acquisition_v1(
        deduplicated_symbol_count=50_000,
        sample_summaries=_sample_summaries(),
        split_metadata={"coverage_complete": False, "page_count": None},
    )
    assert estimate["full_history_eod_requests"] == 50_000
    assert estimate["split_request_upper_bound"] == 50_000
    assert estimate["total_call_units_exact"] is False
```

Cover missing `adjusted_close`, non-finite values, high/low inconsistencies,
negative volume, missing SPY/active/delisted coverage, identity collisions,
ambiguous symbol-change chains, split schema variants, missing pagination
metadata, and conservative byte/runtime bounds.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py -q
```

Expected: failures for the new public validation and estimation functions.

- [ ] **Step 3: Implement validators and estimator**

Expose:

```python
def validate_minervini_eod_sample_v1(payload: object) -> dict[str, object]: ...
def analyze_minervini_symbol_changes_v1(payload: object) -> dict[str, object]: ...
def analyze_minervini_split_coverage_v1(payload: object) -> dict[str, object]: ...
def estimate_minervini_wide_acquisition_v1(
    *,
    deduplicated_symbol_count: int,
    sample_summaries: Sequence[Mapping[str, object]],
    split_metadata: Mapping[str, object],
) -> dict[str, object]: ...
```

Use 100,000 call units per day and 1,000 requests per minute only as documented
planning constants. Also report conservative runtime at five requests per
second. Storage estimates use sample byte-per-row quantiles and observed
history lengths; when the sample is insufficient, return
`storage_estimate_status="INSUFFICIENT_SAMPLE"` rather than a number.

The split parser accepts only documented JSON shapes:

```text
{"splits": [...]}
{"data": [...], "meta": {"total": ..., "limit": ..., "offset": ...}}
```

Any other shape is blocking.

- [ ] **Step 4: Run Task 3 and Task 2 tests**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_immutable_pilot_artifacts_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- research_lab/research/minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_eodhd_acquisition_pilot_v1.py
git commit -m "feat: validate and estimate Minervini acquisition"
```

### Task 4: Exactly-once pilot executor

**Files:**
- Modify: `research_lab/research/minervini_eodhd_acquisition_pilot_v1.py`
- Modify: `tests/test_minervini_eodhd_acquisition_pilot_v1.py`

- [ ] **Step 1: Write failing exactly-once execution tests**

Use an injected raw-byte getter:

```python
def test_executor_uses_exactly_24_requests_without_retry_and_redacts_secret(tmp_path):
    seen = []

    def getter(url: str):
        seen.append(url)
        return _fixture_bytes_for(url), {"http_status": 200, "content_type": "application/json"}

    result = run_minervini_eodhd_acquisition_pilot_v1(
        api_key="secret-value",
        output_dir=tmp_path / "pilot",
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-25T20:00:00Z",
    )

    assert result["provider_requests_used"] == 24
    assert len(seen) == 24
    assert result["status"] == "READY_FOR_WIDE_ACQUISITION_APPROVAL"
    assert "secret-value" not in json.dumps(result)
    assert "secret-value" not in (tmp_path / "pilot" / "request-journal.jsonl").read_text()
```

Add tests that the first failed request stops immediately, no fifth metadata
request can occur, `expected_provider_requests != 24` fails before network,
missing key makes zero calls and zero writes, and partial artifacts remain
replayable after failure.

- [ ] **Step 2: Run focused executor tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py -q
```

Expected: failure because
`run_minervini_eodhd_acquisition_pilot_v1` is absent.

- [ ] **Step 3: Implement the executor**

Expose:

```python
RawHttpGet = Callable[[str], tuple[bytes, Mapping[str, object]]]

def run_minervini_eodhd_acquisition_pilot_v1(
    *,
    output_dir: Path,
    expected_provider_requests: int,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: RawHttpGet | None = None,
    now_utc: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute at most 24 read-only requests and persist immutable evidence."""
```

Execution order is:

1. active list;
2. delisted list;
3. build deterministic plan;
4. symbol changes;
5. split calendar;
6. twenty EOD samples.

Before every request, assert the next ordinal is no greater than 24. Perform
no retries inside the getter or executor. Convert exceptions into a
`FAILED_VALIDATION` partial journal record without including exception text
that could contain the token.

The default getter reads exact bytes with a 30-second timeout, rejects redirects
away from `eodhd.com`, caps each response at 100 MiB, and reports only sanitized
metadata.

- [ ] **Step 4: Run all pilot tests**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_immutable_pilot_artifacts_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- research_lab/research/minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_eodhd_acquisition_pilot_v1.py
git commit -m "feat: execute bounded Minervini acquisition pilot"
```

### Task 5: Dry-run CLI and public export

**Files:**
- Create: `scripts/run_minervini_eodhd_acquisition_pilot_v1.py`
- Create: `tests/test_run_minervini_eodhd_acquisition_pilot_v1.py`
- Modify: `research_lab/research/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_defaults_to_zero_network_zero_write(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status=DRY_RUN",
        "planned_provider_requests=24",
        "writes_performed=False",
    ]

def test_live_cli_requires_absolute_output_and_exact_acknowledgement(capsys):
    assert main(["--execute-live", "--output-dir", "relative", "--expected-provider-requests", "24"]) == 1
    assert "absolute" in capsys.readouterr().out
```

Also test that `--execute-live` without `--output-dir`, without exactly `24`,
or with a non-empty/symlink output directory fails before calling the
executor.

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```powershell
python -m pytest tests/test_run_minervini_eodhd_acquisition_pilot_v1.py -q
```

Expected: import failure for the script.

- [ ] **Step 3: Implement the thin CLI**

Accepted arguments are exactly:

```text
--execute-live
--output-dir PATH
--expected-provider-requests 24
```

Dry-run ignores the environment and performs no filesystem mutation. Live
stdout prints status, provider request count, universe counts, estimate bounds,
manifest SHA-256, and output directory. It never prints raw responses,
unsanitized URLs, provider account information, or token values.

Export:

```python
build_minervini_eodhd_acquisition_plan_v1
run_minervini_eodhd_acquisition_pilot_v1
replay_minervini_pilot_artifacts_v1
```

Add a short README section with dry-run and live syntax plus the separate
wide-acquisition approval boundary.

- [ ] **Step 4: Run focused and adjacent tests**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_immutable_pilot_artifacts_v1.py tests/test_run_minervini_eodhd_acquisition_pilot_v1.py tests/test_minervini_eodhd_capability_v1.py tests/test_local_ohlcv_file_input_adapter_v1.py -q
python -m py_compile research_lab/research/minervini_eodhd_acquisition_pilot_v1.py research_lab/research/minervini_immutable_pilot_artifacts_v1.py scripts/run_minervini_eodhd_acquisition_pilot_v1.py
git diff --check
```

Expected: all tests pass and both validation commands exit zero.

- [ ] **Step 5: Commit Task 5**

```powershell
git add -- scripts/run_minervini_eodhd_acquisition_pilot_v1.py tests/test_run_minervini_eodhd_acquisition_pilot_v1.py research_lab/research/__init__.py README.md
git commit -m "feat: add Minervini acquisition pilot CLI"
```

### Task 6: Synthetic end-to-end replay acceptance

**Files:**
- Create: `tests/test_minervini_eodhd_acquisition_pilot_e2e_v1.py`

- [ ] **Step 1: Write one complete synthetic acceptance**

Run all 24 injected responses into a temporary directory, replay the directory,
and assert:

```python
assert result["status"] == "READY_FOR_WIDE_ACQUISITION_APPROVAL"
assert replay["status"] == "VERIFIED"
assert replay["result_manifest_sha256"] == result["result_manifest_sha256"]
assert result["provider_requests_used"] == 24
assert result["broker_actions_used"] == 0
assert result["registry_write_performed"] is False
assert result["promotion_performed"] is False
assert result["deployment_performed"] is False
```

Mutate one copied raw artifact and assert replay becomes
`FAILED_RAW_HASH_MISMATCH`.

- [ ] **Step 2: Run only the acquisition-pilot suite**

Run:

```powershell
$tests = @(rg --files tests | Where-Object { $_ -match 'minervini_eodhd_acquisition_pilot|immutable_pilot_artifacts' })
python -m pytest @tests -q
```

Expected: all pilot tests pass.

- [ ] **Step 3: Commit Task 6**

```powershell
git add -- tests/test_minervini_eodhd_acquisition_pilot_e2e_v1.py
git commit -m "test: cover Minervini acquisition pilot end to end"
```

### Task 7: Strict pre-live review and one live pilot

**Files:**
- No repository changes expected.
- Runtime artifacts: a new directory outside the repository under
  `C:\Users\lojka\trading\data\minervini-eodhd-acquisition-pilot-v1\`.

- [ ] **Step 1: Perform strict local review**

Inspect `origin/main...HEAD` and fail the review for any path that can exceed 24
requests, retry, follow an off-domain redirect, leak the token, overwrite an
artifact, or authorize a wide acquisition.

- [ ] **Step 2: Re-run fresh focused verification**

Run the exact Task 5 focused/adjacent test command, compilation, and
`git diff --check`. Confirm the worktree is clean.

- [ ] **Step 3: Run dry-run**

Run:

```powershell
python scripts/run_minervini_eodhd_acquisition_pilot_v1.py
```

Expected:

```text
status=DRY_RUN
planned_provider_requests=24
writes_performed=False
```

- [ ] **Step 4: Create one new empty run directory**

Resolve a UTC timestamp once and create:

```text
C:\Users\lojka\trading\data\minervini-eodhd-acquisition-pilot-v1\pilot-<UTC timestamp>
```

Verify the resolved path is inside the named pilot root and is empty.

- [ ] **Step 5: Execute exactly one live pilot**

Only if `EODHD_API_KEY` is present, run:

```powershell
python scripts/run_minervini_eodhd_acquisition_pilot_v1.py --execute-live --output-dir <absolute-empty-run-directory> --expected-provider-requests 24
```

Do not rerun on failure. Report the exact stopping ordinal, status, manifest
path, hashes, request count, universe counts, sample coverage, storage/call
estimates, and blockers without printing the token.

- [ ] **Step 6: Replay live artifacts offline**

Invoke `replay_minervini_pilot_artifacts_v1` against the live run directory.
Expected: `VERIFIED`, or an exact fail-closed evidence status.

### Task 8: PR lifecycle and alignment

**Files:**
- No new source files expected unless review finds a defect.

- [ ] **Step 1: Commit any reviewed live-result documentation only if it is secret-free**

Do not commit raw provider responses or runtime artifacts. The live manifest
remains outside the repository.

- [ ] **Step 2: Push and open a draft PR**

Push `codex/minervini-eodhd-acquisition-pilot-v1` and create a draft PR
summarizing exact provider request count, focused tests, live verdict, and the
wide-acquisition approval boundary.

- [ ] **Step 3: Strictly review the exact remote head**

Resolve the PR head SHA, inspect the remote diff, comments, mergeability, and
checks. Repair P0/P1/P2 findings test-first. Record one strict PASS review only
when no findings remain.

- [ ] **Step 4: Merge only the reviewed head**

Mark ready and merge with the reviewed head SHA as an expected-head guard.

- [ ] **Step 5: Fast-forward the dedicated clean `main` worktree**

Use `git worktree list --porcelain`, preserve the drifted primary checkout, and
fast-forward only the worktree that owns `main`.

- [ ] **Step 6: Synchronize Hetzner only through the safe wrapper**

Run:

```bash
ssh -o BatchMode=yes hetzner-research 'cd /opt/trading/research-lab && bash scripts/run_safe_sync_with_preflight.sh'
```

Then run the focused acquisition-pilot tests remotely and prove local
`main`, `origin/main`, GitHub merge SHA, and Hetzner HEAD alignment.

The live local pilot is not rerun on Hetzner.

- [ ] **Step 7: Stop at the wide-acquisition approval gate**

Report either:

```text
READY_FOR_WIDE_ACQUISITION_APPROVAL
```

with the exact measured envelope, or the exact blocking verdict. Do not begin
the wide acquisition without a separate user instruction.
