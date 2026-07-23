# Hermes Effective Knowledge Bridge Repair v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduled Hermes runs use recognized Knihomol evidence and prevent the same queued executable hypothesis from being backtested twice on an unchanged data snapshot.

**Architecture:** Deterministic report parsing selects a canonical research blocker before Knihomol retrieval. The Hermes runner blocks provider invocation when configured canonical inputs yield no usable evidence and requires accepted book-informed proposals to cite selected notes. The daily runner derives a canonical data-snapshot identity and uses a bounded recent-result index to skip only repeated LLM-queued executions on that exact snapshot.

**Tech Stack:** Python 3.12, dataclasses, pathlib, hashlib/JSON canonicalization, pandas, pytest, Git worktrees, systemd/Hetzner for bounded post-merge verification.

---

### Task 1: Structured blocker selection

**Files:**
- Modify: `research_lab/hermes/artifacts.py:31-43`
- Modify: `tests/test_hermes_artifacts.py`

- [ ] **Step 1: Write failing structured-count tests**

Add these tests with a report containing a misleading provider-status
`biggest risk discovered` line plus:

```text
- rejection_reasons: failed cost stress=2; insufficient walk-forward robustness=14; max drawdown too deep=9
```

```python
def test_structured_rejection_counts_outrank_descriptive_risk():
    report = "\n".join(
        [
            "- biggest risk discovered: EODHD real EOD data is enabled",
            "- rejection_reasons: failed cost stress=2; "
            "insufficient walk-forward robustness=14; max drawdown too deep=9",
        ]
    )
    assert dominant_blocker(report) == "walk_forward_fail"


def test_structured_rejection_count_tie_has_fixed_priority():
    report = (
        "- rejection_reasons: max drawdown too deep=3; "
        "failed cost stress=3; insufficient walk-forward robustness=3"
    )
    assert dominant_blocker(report) == "walk_forward_fail"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_artifacts.py -q
```

Expected: the new tests fail because the prose line currently returns the
provider-status sentence.

- [ ] **Step 3: Implement the deterministic parser**

In `research_lab/hermes/artifacts.py`, add:

```python
STRUCTURED_BLOCKER_REASONS = (
    ("insufficient walk-forward robustness", "walk_forward_fail"),
    ("max drawdown too deep", "drawdown_fail"),
    ("failed cost stress", "cost_stress"),
)


def _structured_blocker(report_text: str) -> str | None:
    counts: dict[str, int] = {}
    for line in report_text.splitlines():
        if not re.match(r"\s*-\s*rejection_reasons\s*:", line, re.IGNORECASE):
            continue
        summary = line.split(":", 1)[1]
        for item in summary.split(";"):
            reason, separator, raw_count = item.strip().rpartition("=")
            if not separator or not raw_count.strip().isdigit():
                continue
            counts[reason.strip().casefold()] = int(raw_count)
    ranked = [
        (counts.get(reason, 0), -priority, blocker)
        for priority, (reason, blocker) in enumerate(STRUCTURED_BLOCKER_REASONS)
        if counts.get(reason, 0) > 0
    ]
    return max(ranked)[2] if ranked else None
```

Call `_structured_blocker(report_text)` first in `dominant_blocker`.

- [ ] **Step 4: Verify GREEN and compatibility**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_artifacts.py tests/test_hermes_runner.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- research_lab/hermes/artifacts.py tests/test_hermes_artifacts.py
git commit -m "fix: derive Hermes blocker from rejection evidence"
```

### Task 2: Fail-closed canonical book context and evidence use

**Files:**
- Modify: `research_lab/hermes/run_hypothesis_generation.py:20-190`
- Modify: `tests/test_hermes_book_runtime.py`
- Modify: `tests/test_hermes_runner.py`

- [ ] **Step 1: Write the provider-blocking test**

Create canonical index and extracted-notes paths, use a report whose canonical
blocker has no matching valid note, and supply a provider invoker that raises if
called. Assert:

```python
assert outcome["status"] == "book_context_unavailable"
assert outcome["artifact_phase"] == "no_queue_change"
assert outcome["queue_impact"]["state"] == "unchanged"
assert outcome["book_knowledge"]["note_count"] == 0
```

Also assert the queue is absent or byte-identical.

- [ ] **Step 2: Verify RED**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_book_runtime.py -q
```

Expected: the provider invoker is reached.

- [ ] **Step 3: Implement canonical-input availability and early artifact**

Resolve the configured paths once:

```python
book_index_path = Path(current_env.get(
    "HERMES_BOOK_INDEX_PATH",
    "/opt/trading/private/hermes_books/index/book_index.json",
))
book_notes_dir = Path(current_env.get(
    "HERMES_BOOK_NOTES_DIR",
    "/opt/trading/private/hermes_books/extracted_notes",
))
canonical_inputs_available = book_index_path.is_file() and book_notes_dir.is_dir()
```

Build the immutable base artifact before provider invocation. When
`canonical_inputs_available and book_context.note_count == 0`, finish with
`status="book_context_unavailable"`, `artifact_phase="no_queue_change"`, and a
bounded reason code containing no private path or note content.

- [ ] **Step 4: Write the unused-evidence test**

Provide one selected note and a schema-valid hypothesis with
`used_note_ids=[]`. Assert no queue append and:

```python
assert "hypothesis_1:book_evidence_not_used" in outcome["rejection_reasons"]
```

- [ ] **Step 5: Verify RED**

Run the exact new test and confirm that the proposal is currently accepted.

- [ ] **Step 6: Enforce selected-note use**

After schema validation and before fingerprint acceptance:

```python
if book_context.note_count > 0 and not validation.hypothesis["used_note_ids"]:
    rejection_reasons.append(f"hypothesis_{index}:book_evidence_not_used")
    continue
```

Keep the existing validation that every supplied ID belongs to the selected
note set.

- [ ] **Step 7: Verify GREEN**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_book_runtime.py tests/test_hermes_runner.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add -- research_lab/hermes/run_hypothesis_generation.py tests/test_hermes_book_runtime.py tests/test_hermes_runner.py
git commit -m "fix: require usable Knihomol evidence for Hermes"
```

### Task 3: Deterministic data-snapshot identity

**Files:**
- Modify: `research_lab/runner.py`
- Modify: `tests/test_daily_runner_dedupe.py`

- [ ] **Step 1: Write identity tests**

Add tests for `_data_snapshot_identity(manifest)` proving:

- dictionary key order does not change the hash;
- symbol order is preserved because ordered universes can be meaningful;
- changed source, symbol order, start, end, fallback flag, or available content
  hash changes the identity;
- non-boolean explicit fallback values and malformed required identity fields
  raise `ValueError` rather than producing an ambiguous identity;
- a missing fallback field is encoded as the literal state `unknown`, because
  existing valid manifests predate the top-level marker.

- [ ] **Step 2: Verify RED**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_daily_runner_dedupe.py -q
```

Expected: import or attribute failure for `_data_snapshot_identity`.

- [ ] **Step 3: Implement strict canonical hashing**

Add a helper that requires a mapping, a non-empty `source`, a non-empty ordered
symbol list, `start`, and `end`. An explicit `fallback_used` must be a real
boolean; a missing marker becomes `unknown`. Normalize only approved optional
scalar identity fields and approved 64-character SHA-256 fields, then hash
canonical JSON:

```python
encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

Do not hash mutable report paths, row counts, API URLs, credentials, or runtime
timestamps.

- [ ] **Step 4: Verify GREEN**

Run the identity tests and the complete `tests/test_daily_runner_dedupe.py`.

### Task 4: Skip repeated queued execution on the same snapshot

**Files:**
- Modify: `research_lab/runner.py:57-185`
- Modify: `research_lab/runner.py:337-355`
- Modify: `tests/test_daily_runner_dedupe.py`
- Modify: `tests/test_hermes_queue_mapping.py`

- [ ] **Step 1: Write RED integration tests**

Build one LLM-queued `StrategySpec` with `source_hypothesis_id`, one stored
prior experiment with the same execution fingerprint and snapshot identity,
and a current `DataBundle` with the same manifest. Assert:

- `weighted_backtest` is not called;
- no result or hypothesis-result append occurs;
- `same_snapshot_skipped == 1`;
- the daily run completes with zero new results.

Add a second test where only the current manifest `end` changes and assert the
backtest runs once.

- [ ] **Step 2: Verify RED**

Run both exact tests and confirm that the same-snapshot case currently reaches
`weighted_backtest`.

- [ ] **Step 3: Implement a bounded recent execution index**

Add:

```python
def _recent_hypothesis_snapshot_keys(root: Path, max_rows: int = 1000) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for result in tail_jsonl(root / "registry" / "experiments.jsonl", max_rows):
        parameters = result.get("parameters")
        if not isinstance(parameters, dict) or not parameters.get("source_hypothesis_id"):
            continue
        fingerprint = result_execution_fingerprint(result)
        if not fingerprint:
            continue
        snapshot = result.get("data_snapshot_identity")
        if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
            try:
                snapshot = _data_snapshot_identity(result.get("data_manifest"))
            except ValueError:
                continue
        keys.add((fingerprint, snapshot))
    return keys
```

Read only the bounded tail through the repository JSONL helper. Include rows
only when they have `parameters.source_hypothesis_id`. Use the stored
`data_snapshot_identity` when valid; otherwise derive it from the stored
`data_manifest`. Ignore malformed legacy rows without suppressing new work.

- [ ] **Step 4: Apply the skip at the execution boundary**

Import `strategy_execution_fingerprint`. After resolving each spec's actual
daily or intraday `DataBundle` and before building weights:

```python
snapshot_identity = _data_snapshot_identity(data_bundle.manifest)
execution_key = (strategy_execution_fingerprint(spec), snapshot_identity)
if spec.parameters.get("source_hypothesis_id") and execution_key in recent_keys:
    selection["diagnostics"]["same_snapshot_skipped"] += 1
    continue
```

Store `data_snapshot_identity` in every completed result and in
`_persist_hypothesis_result`.

- [ ] **Step 5: Verify GREEN and lineage**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_daily_runner_dedupe.py tests/test_hermes_queue_mapping.py -q
```

Expected: all tests pass and persisted hypothesis rows contain both
`used_note_ids` and `data_snapshot_identity`.

- [ ] **Step 6: Commit**

```powershell
git add -- research_lab/runner.py tests/test_daily_runner_dedupe.py tests/test_hermes_queue_mapping.py
git commit -m "fix: dedupe queued research by data snapshot"
```

### Task 5: Integrated validation and documentation

**Files:**
- Modify if required: `docs/hermes_scheduling.md`
- Verify: all changed production and test files

- [ ] **Step 1: Update operator semantics**

Document `book_context_unavailable`, the no-provider-call behavior, required
`used_note_ids`, and same-snapshot skip behavior. Do not document any secret,
private content, or raw path beyond the already public canonical configuration
examples.

- [ ] **Step 2: Run focused validation**

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_artifacts.py tests/test_hermes_runner.py tests/test_hermes_book_runtime.py tests/test_candidate_generation_guidance.py tests/test_daily_experiment_selector.py tests/test_daily_runner_dedupe.py tests/test_hermes_queue_mapping.py -q
```

- [ ] **Step 3: Run static checks**

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m py_compile research_lab/hermes/artifacts.py research_lab/hermes/run_hypothesis_generation.py research_lab/runner.py
git diff --check
```

- [ ] **Step 4: Run the full suite**

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures; only the already documented resource warnings may
remain.

- [ ] **Step 5: Commit documentation or final test corrections**

Stage only exact milestone paths and commit with a narrow message.

### Task 6: Review, publication, and bounded live proof

**Files:**
- No additional production files unless strict review identifies an in-scope
  defect, which must receive its own RED/GREEN regression test.

- [ ] **Step 1: Independent strict review**

Review the exact base-to-head diff for P0/P1/P2 findings, trust-boundary
bypasses, unbounded reads, secret exposure, provider-before-gate behavior,
lineage breaks, and false dedupe.

- [ ] **Step 2: Publish only after PASS**

Push the branch, create a draft PR, verify exact head and absent/present GitHub
checks, mark ready, and merge only the independently reviewed head.

- [ ] **Step 3: Align and sync**

Fast-forward the separate clean local `main`, confirm local/origin/GitHub
alignment, then run only:

```bash
cd /opt/trading/research-lab
bash scripts/run_safe_sync_with_preflight.sh
```

under the existing `trading` account. Run focused Hetzner tests before any live
provider call.

- [ ] **Step 4: Bounded Hermes live run**

Verify no Hermes instance is running, snapshot only safe queue counts/hashes,
then invoke the existing scheduled Hermes command exactly once. Confirm:

- recognized canonical blocker;
- `note_count > 0`;
- selected note IDs;
- any committed hypothesis cites selected notes;
- no secret or raw response is printed;
- no promotion or broker action occurred.

- [ ] **Step 5: Bounded EODHD lineage run**

Invoke the existing daily research service exactly once only if the Hermes run
committed a new unique hypothesis. Verify either:

- the selected hypothesis reaches a result with exact `used_note_ids` and
  `data_snapshot_identity`; or
- it is deterministically rejected/skipped with an explicit bounded reason.

Confirm all timers remain enabled/active, dashboard remains read-only, and
local/origin/GitHub/Hetzner stay aligned.
