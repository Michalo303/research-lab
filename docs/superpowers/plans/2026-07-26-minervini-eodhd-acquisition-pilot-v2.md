# Minervini EODHD Acquisition Pilot V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, immutable, exactly 24-request pilot using only EODHD capabilities included in the current EOD Historical Data plan.

**Architecture:** A new V2 module owns atomic-ticker universe normalization, deterministic eleven-symbol sampling, paired EOD/split planning, validation, estimates, and bounded execution. It reuses the V1 immutable artifact writer and EOD row validator without invoking the unavailable V1 request plan. A separate V2 CLI defaults to zero-network dry-run.

**Tech Stack:** Python 3.12 standard library, pytest, SHA-256 canonical JSON, injected raw HTTP getter, existing immutable pilot artifact contract.

---

### Task 1: Frozen EOD-only plan and pure validators

**Files:**
- Create: `research_lab/research/minervini_eodhd_acquisition_pilot_v2.py`
- Create: `tests/test_minervini_eodhd_acquisition_pilot_v2.py`

- [ ] **Step 1: Write failing planner tests**

Add deterministic active and delisted fixtures and assert:

```python
def test_v2_plan_uses_only_eod_plan_capabilities():
    plan = build_minervini_eodhd_acquisition_plan_v2(
        active_rows=_active_rows(),
        delisted_rows=_delisted_rows(),
    )

    assert plan["version"] == "minervini_eodhd_acquisition_plan_v2"
    assert plan["provider_request_limit"] == 24
    assert len(plan["sample_symbols"]) == 11
    assert plan["sample_symbols"][0] == "SPY.US"
    assert "AAPL.US" in plan["sample_symbols"]
    assert "ATVI.US" in plan["sample_symbols"]
    assert len(plan["request_specs"]) == 22
    assert all(
        "/symbol-change-history" not in spec["endpoint_identity"]
        and "/calendar/" not in spec["endpoint_identity"]
        for spec in plan["request_specs"]
    )
    assert [
        spec["kind"] for spec in plan["request_specs"]
    ] == ["eod", "splits"] * 11
```

Add tests for stable input reordering, anchor duplication and fill, malformed
provider codes, non-common-stock rejection, duplicate identities, and
active/delisted ticker collisions.

- [ ] **Step 2: Run the planner test and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v2.py -q
```

Expected: collection fails because the V2 module does not exist.

- [ ] **Step 3: Implement the V2 planner**

Define:

```python
PLAN_VERSION = "minervini_eodhd_acquisition_plan_v2"
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"

def build_minervini_eodhd_acquisition_plan_v2(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build eleven paired EOD/split requests for atomic provider tickers."""
```

Normalize exact `Common Stock` rows. Preserve code, exchange, currency, and
optional ISIN. Rank non-anchor rows by SHA-256 of:

```text
status:code:exchange:currency:isin-or-empty
```

Build the sample as SPY, AAPL, four additional active identities, ATVI, and
four additional delisted identities. For each sample emit adjacent requests:

```text
/api/eod/{symbol}?from=2010-01-01&to=2025-12-31&period=d&fmt=json
/api/splits/{symbol}?from=2010-01-01&to=2025-12-31&fmt=json
```

The result includes:

```python
{
    "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
    "rename_continuity_supported": False,
    "wide_acquisition_scope": "ATOMIC_PROVIDER_TICKER_HISTORIES",
}
```

- [ ] **Step 4: Write failing per-symbol split validator tests**

Add:

```python
def test_v2_split_validator_accepts_empty_and_valid_ordered_rows():
    empty = validate_minervini_symbol_splits_v2([])
    valid = validate_minervini_symbol_splits_v2(
        [
            {"date": "2014-06-09", "split": "7.000000/1.000000"},
            {"date": "2020-08-31", "split": "4.000000/1.000000"},
        ]
    )

    assert empty["record_count"] == 0
    assert empty["lineage_classification"] == (
        "PROVIDER_REPORTED_EVENTS_NOT_COMPLETENESS_PROOF"
    )
    assert valid["record_count"] == 2
    assert valid["first_date"] == "2014-06-09"
```

Test malformed ratios, zero or negative factors, unordered dates, duplicate
dates, rows outside the frozen interval, and unsupported payload shapes.

- [ ] **Step 5: Implement the split validator and exact estimator**

Expose `validate_minervini_symbol_splits_v2(payload: object) ->
dict[str, object]` and
`estimate_minervini_atomic_acquisition_v2(*,
deduplicated_symbol_count: int,
sample_summaries: Sequence[Mapping[str, object]]) -> dict[str, object]`.

The estimator computes:

```python
eod_requests = deduplicated_symbol_count
split_requests = deduplicated_symbol_count
total_requests = 2 + eod_requests + split_requests
minimum_days = math.ceil(total_requests / 100_000)
```

Storage bounds use observed EOD and split response byte counts. Every estimate
records whether it is exact or sample-derived.

- [ ] **Step 6: Run Task 1 tests**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v2.py -q
```

Expected: all pure V2 tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- research_lab/research/minervini_eodhd_acquisition_pilot_v2.py tests/test_minervini_eodhd_acquisition_pilot_v2.py
git commit -m "feat: add EOD-only Minervini acquisition plan v2"
```

### Task 2: Exactly-once V2 executor and immutable evidence

**Files:**
- Modify: `research_lab/research/minervini_eodhd_acquisition_pilot_v2.py`
- Modify: `tests/test_minervini_eodhd_acquisition_pilot_v2.py`
- Reuse: `research_lab/research/minervini_immutable_pilot_artifacts_v1.py`

- [ ] **Step 1: Write failing exactly-once execution test**

Use an injected getter:

```python
def test_v2_executor_uses_exactly_24_requests_in_frozen_pair_order(tmp_path):
    seen: list[str] = []

    def getter(url: str):
        seen.append(url)
        return _fixture_bytes_for(url), {"http_status": 200}

    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key="secret-value",
        output_dir=tmp_path / "pilot",
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )

    assert len(seen) == 24
    assert result["provider_requests_used"] == 24
    assert result["status"] == "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL"
    assert all(
        ("/eod/" in seen[index] and "/splits/" in seen[index + 1])
        for index in range(2, 24, 2)
    )
```

Assert that no URL, result, journal, manifest, or exception includes the token.

- [ ] **Step 2: Write failing safety and partial-evidence tests**

Add five separate tests. The 403 getter returns valid universe payloads for
calls one and two, then `b"Forbidden."` with `http_status=403`; assert exactly
three calls, `BLOCKED_PROVIDER_CAPABILITY`, and stopping ordinal three. The
missing-key test passes `env={}` and asserts no calls and no output directory.
The acknowledgement test passes 23 and asserts `ValueError` before the getter
is invoked. The hard-cap test supplies more fixtures than required and asserts
the executor consumes exactly 24. The partial-evidence test fails a chosen
split response and asserts the saved directory returns `VERIFIED` from offline
replay without making another getter call.

The 403 test expects `BLOCKED_PROVIDER_CAPABILITY`, its exact stopping ordinal,
and no later requests.

- [ ] **Step 3: Run executor tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_acquisition_pilot_v2.py -q
```

Expected: failures because the V2 executor is absent.

- [ ] **Step 4: Implement the bounded executor**

Expose:

```python
RawHttpGet = Callable[[str], tuple[bytes, Mapping[str, object]]]

def run_minervini_eodhd_acquisition_pilot_v2(
    *,
    output_dir: Path,
    expected_provider_requests: int,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: RawHttpGet | None = None,
    now_utc: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute exactly 24 EOD-plan-compatible read-only requests."""
```

Execution performs the two universe calls, builds the plan, then executes the
eleven adjacent EOD/split pairs. Reuse:

```python
MinerviniPilotArtifactWriterV1
validate_minervini_eod_sample_v1
```

The default HTTP getter is bounded to 30 seconds, 100 MiB per response, same
host redirects, and no retries. Persist all received bytes before returning a
failure verdict.

- [ ] **Step 5: Finalize the V2 result**

The ready result contains:

```python
{
    "status": "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL",
    "provider_requests_used": 24,
    "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
    "rename_continuity_supported": False,
    "wide_acquisition_authorized": False,
    "broker_actions_used": 0,
    "registry_write_performed": False,
    "promotion_performed": False,
    "deployment_performed": False,
}
```

It also contains universe counts and hashes, eleven EOD summaries, eleven split
summaries, the exact wide request estimate, and raw artifact hashes.

- [ ] **Step 6: Run V2 and V1 regression tests**

Run:

```powershell
python -m pytest \
  tests/test_minervini_eodhd_acquisition_pilot_v2.py \
  tests/test_minervini_eodhd_acquisition_pilot_v1.py \
  tests/test_minervini_immutable_pilot_artifacts_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 2**

```powershell
git add -- research_lab/research/minervini_eodhd_acquisition_pilot_v2.py tests/test_minervini_eodhd_acquisition_pilot_v2.py
git commit -m "feat: execute bounded Minervini acquisition pilot v2"
```

### Task 3: V2 CLI, public exports, and documentation

**Files:**
- Create: `scripts/run_minervini_eodhd_acquisition_pilot_v2.py`
- Create: `tests/test_run_minervini_eodhd_acquisition_pilot_v2.py`
- Modify: `research_lab/research/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

Add:

```python
def test_v2_cli_defaults_to_zero_network_zero_write(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status=DRY_RUN",
        "version=minervini_eodhd_acquisition_pilot_v2",
        "planned_provider_requests=24",
        "writes_performed=False",
    ]
```

Test missing/relative/non-empty/symlink output paths and acknowledgement values
other than exactly 24.

- [ ] **Step 2: Run CLI test and verify RED**

Run:

```powershell
python -m pytest tests/test_run_minervini_eodhd_acquisition_pilot_v2.py -q
```

Expected: import failure because the V2 CLI does not exist.

- [ ] **Step 3: Implement the thin V2 CLI**

Live arguments are exactly:

```text
--execute-live
--output-dir PATH
--expected-provider-requests 24
```

Print only status, request count, universe counts, estimate bounds, manifest
SHA-256, and output directory. Do not print raw provider responses, tokens,
unsanitized URLs, or provider account information.

- [ ] **Step 4: Export the V2 public contract**

Add to `research_lab.research`:

```python
build_minervini_eodhd_acquisition_plan_v2
run_minervini_eodhd_acquisition_pilot_v2
validate_minervini_symbol_splits_v2
```

Document dry-run, live syntax, atomic ticker limitation, separated process logs,
and the separate wide-acquisition approval gate in `README.md`.

- [ ] **Step 5: Run CLI and adjacent tests**

Run:

```powershell
python -m pytest \
  tests/test_minervini_eodhd_acquisition_pilot_v2.py \
  tests/test_run_minervini_eodhd_acquisition_pilot_v2.py \
  tests/test_minervini_eodhd_acquisition_pilot_v1.py \
  tests/test_minervini_immutable_pilot_artifacts_v1.py \
  tests/test_minervini_eodhd_capability_v1.py \
  tests/test_local_ohlcv_file_input_adapter_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- scripts/run_minervini_eodhd_acquisition_pilot_v2.py tests/test_run_minervini_eodhd_acquisition_pilot_v2.py research_lab/research/__init__.py README.md
git commit -m "feat: add Minervini acquisition pilot v2 CLI"
```

### Task 4: Synthetic end-to-end acceptance and strict pre-live review

**Files:**
- Create: `tests/test_minervini_eodhd_acquisition_pilot_e2e_v2.py`

- [ ] **Step 1: Write the synthetic acceptance**

Inject all 24 responses, replay the output directory, and assert:

```python
assert result["status"] == "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL"
assert result["provider_requests_used"] == 24
assert replay["status"] == "VERIFIED"
assert replay["result_manifest_sha256"] == result["result_manifest_sha256"]
assert result["wide_acquisition_authorized"] is False
assert result["broker_actions_used"] == 0
```

Copy the run, mutate one raw response byte, and assert replay reports
`FAILED_RAW_HASH_MISMATCH`.

- [ ] **Step 2: Run the complete focused suite**

Run:

```powershell
python -m pytest \
  tests/test_minervini_eodhd_acquisition_pilot_v2.py \
  tests/test_run_minervini_eodhd_acquisition_pilot_v2.py \
  tests/test_minervini_eodhd_acquisition_pilot_e2e_v2.py \
  tests/test_minervini_eodhd_acquisition_pilot_v1.py \
  tests/test_minervini_eodhd_acquisition_pilot_e2e_v1.py \
  tests/test_minervini_immutable_pilot_artifacts_v1.py \
  tests/test_minervini_eodhd_capability_v1.py \
  tests/test_local_ohlcv_file_input_adapter_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Compile and inspect**

Run:

```powershell
python -m py_compile \
  research_lab/research/minervini_eodhd_acquisition_pilot_v1.py \
  research_lab/research/minervini_eodhd_acquisition_pilot_v2.py \
  research_lab/research/minervini_immutable_pilot_artifacts_v1.py \
  scripts/run_minervini_eodhd_acquisition_pilot_v1.py \
  scripts/run_minervini_eodhd_acquisition_pilot_v2.py
git diff --check
git status --short
```

Expected: compilation and diff checks pass; only intended committed files exist.

- [ ] **Step 4: Strictly review the exact diff**

Reject any path that can:

- exceed 24 requests;
- retry, paginate, fall back, or follow an off-domain redirect;
- invoke V1 unavailable endpoints;
- leak the token;
- overwrite artifacts;
- claim rename continuity or split completeness;
- authorize wide acquisition, trading, promotion, or deployment.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- tests/test_minervini_eodhd_acquisition_pilot_e2e_v2.py
git commit -m "test: cover Minervini acquisition pilot v2 end to end"
```

### Task 5: One explicitly authorized live V2 run and PR lifecycle

**Files:**
- Runtime only under `C:\Users\lojka\trading\data\minervini-eodhd-acquisition-pilot-v2\`

- [ ] **Step 1: Run zero-network dry-run**

```powershell
python scripts/run_minervini_eodhd_acquisition_pilot_v2.py
```

Expected:

```text
status=DRY_RUN
version=minervini_eodhd_acquisition_pilot_v2
planned_provider_requests=24
writes_performed=False
```

- [ ] **Step 2: Obtain explicit live authorization**

Do not infer authorization from implementation approval. Require approval for
exactly one new V2 run with at most 24 read-only EODHD requests.

- [ ] **Step 3: Execute once with separated logs**

Create one run container with empty `artifacts` and sibling `process-logs`
directories. Start the V2 CLI once. Do not retry or resume on failure.

- [ ] **Step 4: Replay live evidence offline**

Run `replay_minervini_pilot_artifacts_v1` against the V2 artifact directory.
Report exact status, request count, universe counts, sample coverage, split
classification, request/runtime/storage estimate, blockers, and hashes.

- [ ] **Step 5: Final verification and PR**

Run the complete focused suite, compilation, `git diff --check`, and full
pytest. Push the exact branch, create a draft PR, and perform strict review of
the remote head. Repair findings test-first.

- [ ] **Step 6: Merge and align**

Merge only the exact reviewed head, fast-forward only the clean worktree that
owns `main`, synchronize Hetzner through
`scripts/run_safe_sync_with_preflight.sh`, and run focused remote tests. Do not
repeat the live V2 pilot on Hetzner.

- [ ] **Step 7: Stop at the next authority gate**

Report either the exact blocking verdict or:

```text
READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL
```

Do not begin the approximately 101,000-request wide acquisition without a new
explicit instruction.
