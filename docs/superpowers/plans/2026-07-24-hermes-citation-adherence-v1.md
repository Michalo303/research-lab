# Hermes Citation Adherence V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every book-informed Hermes hypothesis cite at least one exact selected Knihomol `note_id`, while preserving fail-closed validation, atomic queue behavior, and one-shot research-only execution.

**Architecture:** The schema module becomes the single authority for the mandatory citation contract and exact allowlist validation. The prompt builder renders that same contract and a citation-bearing JSON example in both normal and sanitized prompts. The runner verifies the complete contract survived prompt construction before invoking the provider, then relies on the schema validator without repairing model output.

**Tech Stack:** Python 3.12, JSON canonical formatting, urllib-based OpenAI-compatible adapter, pytest, Git worktrees, systemd/Hetzner bounded verification.

---

### Task 1: Centralize the mandatory citation contract

**Files:**
- Modify: `research_lab/hermes/schema.py:165-249`
- Modify: `research_lab/hermes/schema.py:339-348`
- Test: `tests/test_hermes_schema.py`
- Test: `tests/test_hermes_book_runtime.py`

- [ ] **Step 1: Write failing schema-text tests**

Add tests that call:

```python
from research_lab.hermes.schema import schema_prompt_text


def test_schema_prompt_requires_exact_selected_note_ids():
    text = schema_prompt_text(
        required_note_ids=(
            "note-1111111111111111",
            "note-2222222222222222",
        )
    )

    assert "MANDATORY CITATION CONTRACT" in text
    assert (
        '["note-1111111111111111","note-2222222222222222"]'
        in text
    )
    assert "Every hypothesis must contain used_note_ids" in text
    assert "omit it or use []" not in text


def test_generic_schema_prompt_retains_optional_note_semantics():
    text = schema_prompt_text()

    assert "MANDATORY CITATION CONTRACT" not in text
    assert "used_note_ids" in text
```

- [ ] **Step 2: Run the exact tests and verify RED**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_schema.py -q
```

Expected: fail because `schema_prompt_text()` does not accept
`required_note_ids` and still says citations may be omitted.

- [ ] **Step 3: Write failing validator tests**

Extend the schema tests with:

```python
from research_lab.hermes.schema import validate_hypothesis


def test_book_informed_hypothesis_requires_nonempty_allowed_note_ids():
    allowed = frozenset({"note-1111111111111111"})
    missing = _valid()
    empty = _valid(used_note_ids=[])

    assert validate_hypothesis(
        missing, allowed_note_ids=allowed
    ).reasons == ["missing_used_note_ids"]
    assert validate_hypothesis(
        empty, allowed_note_ids=allowed
    ).reasons == ["missing_used_note_ids"]


def test_book_informed_hypothesis_preserves_allowed_note_order():
    item = {
        **_valid(),
        "used_note_ids": [
            "note-2222222222222222",
            "note-1111111111111111",
            "note-2222222222222222",
        ],
    }
    result = validate_hypothesis(
        item,
        allowed_note_ids=frozenset(
            {
                "note-1111111111111111",
                "note-2222222222222222",
            }
        ),
    )

    assert result.accepted is True
    assert result.hypothesis["used_note_ids"] == [
        "note-2222222222222222",
        "note-1111111111111111",
    ]
```

Use the existing `_valid()` helper in `tests/test_hermes_schema.py` rather than
creating a second divergent payload.

- [ ] **Step 4: Run the validator tests and verify RED**

Run the exact new test nodes. Expected: missing and empty citations are
currently accepted by `validate_hypothesis()` and therefore the first test
fails.

- [ ] **Step 5: Implement the schema contract**

Change the signature to:

```python
def schema_prompt_text(
    *, required_note_ids: tuple[str, ...] = ()
) -> str:
```

For a non-empty tuple, append a deterministic block:

```python
allowed_json = json.dumps(
    list(required_note_ids),
    separators=(",", ":"),
    ensure_ascii=True,
)
lines.extend(
    [
        "MANDATORY CITATION CONTRACT",
        f"Allowed note IDs (exact JSON array): {allowed_json}",
        (
            "Every hypothesis must contain used_note_ids with 1-5 values "
            "taken only from the exact allowed array."
        ),
        (
            "Empty, missing, malformed, invented, or non-allowlisted note "
            "IDs cause rejection."
        ),
    ]
)
```

When the tuple is empty, preserve a generic sentence saying the field is
optional. Do not include the contradictory optional sentence in the mandatory
branch.

Update `validate_hypothesis()` so that:

```python
raw_used_note_ids = item.get("used_note_ids")
requires_citation = bool(allowed_note_ids)

if raw_used_note_ids is None:
    used_note_ids = []
    if requires_citation:
        reasons.append("missing_used_note_ids")
elif not isinstance(raw_used_note_ids, list):
    used_note_ids = []
    reasons.append("invalid_used_note_ids")
else:
    used_note_ids = raw_used_note_ids
    # retain existing format and maximum-length validation
    if requires_citation and not used_note_ids:
        reasons.append("missing_used_note_ids")
```

Retain exact allowlist checking, the five-ID maximum, and stable
`dict.fromkeys()` deduplication. Do not mutate or synthesize provider output.

- [ ] **Step 6: Run schema and book-runtime tests and verify GREEN**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_schema.py tests/test_hermes_book_runtime.py -q --basetemp C:\Users\lojka\AppData\Local\Temp\pytest-hermes-citation-schema
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add -- research_lab/hermes/schema.py tests/test_hermes_schema.py tests/test_hermes_book_runtime.py
git commit -m "fix: require selected note citations in Hermes schema"
```

### Task 2: Render one unambiguous citation-aware prompt

**Files:**
- Modify: `research_lab/llm/hypothesis_adapter.py:48-146`
- Modify: `research_lab/hermes/run_hypothesis_generation.py:120-128`
- Test: `tests/test_hermes_book_runtime.py`

- [ ] **Step 1: Write the failing normal-prompt test**

Build a `BookKnowledgeContext` with one selected note and assert:

```python
from hermes_knowledge.runtime import BookKnowledgeContext
from research_lab.hermes.schema import schema_prompt_text

context = BookKnowledgeContext(
    prompt=(
        "Hermes curated book-inspired hypothesis seeds\n"
        "Dominant blocker: drawdown\n"
        "- note_id: note-1111111111111111\n"
        "  concept: Volatility targeting"
    ),
    note_count=1,
    selected_book_ids=("book-aaaaaaaaaaaa",),
    selected_note_ids=("note-1111111111111111",),
    canonical_blocker_id="drawdown",
    blocker_diagnostic="exact",
)
prompt = build_hermes_prompt(
    tmp_path,
    schema_text=schema_prompt_text(
        required_note_ids=("note-1111111111111111",)
    ),
    book_context=context,
)

assert "MANDATORY CITATION CONTRACT" in prompt
assert '"used_note_ids":["note-1111111111111111"]' in prompt
assert "omit it or use []" not in prompt
```

Expected initial failure: the JSON hypothesis example omits
`used_note_ids`.

- [ ] **Step 2: Write the failing sanitized-fallback test**

Supply diagnostics containing an already-forbidden private-path marker so the
prompt builder takes its existing sanitized branch:

```python
forbidden_private_path = (
    "/opt/trading/private/hermes_books/raw/secret-book.pdf"
)
prompt = build_hermes_prompt(
    tmp_path,
    diagnostics_text=f"do not expose {forbidden_private_path}",
    schema_text=schema_prompt_text(
        required_note_ids=("note-1111111111111111",)
    ),
    book_context=context,
)

assert forbidden_private_path not in prompt
assert "MANDATORY CITATION CONTRACT" in prompt
assert '"used_note_ids":["note-1111111111111111"]' in prompt
assert "note-1111111111111111" in prompt
```

Expected initial failure: the fallback omits the full hypothesis example and
therefore does not carry the citation-bearing example.

- [ ] **Step 3: Run the exact prompt tests and verify RED**

Run the two new nodes only. Confirm the failures are about the absent
`used_note_ids` example, not fixture setup.

- [ ] **Step 4: Implement a shared JSON example**

In `build_hermes_prompt()`, derive:

```python
example_note_ids = list(selected_book_context.selected_note_ids[:1])
example = {
    "title": "...",
    "family": "LONGTERM|ROTATION|SWING|INTRADAY",
    "builder": "allowed builder",
    "rationale": "...",
    "parameters": {},
    "tags": ["..."],
    "source_url": "...",
    "used_note_ids": example_note_ids,
    "risk_controls": {
        "volatility_targeting": "...",
        "drawdown_circuit_breakers": "...",
        "cash_defensive_regimes": "...",
        "exposure_caps": "...",
        "correlation_aware_portfolio_risk": "...",
        "crisis_period_diagnostics": "...",
        "cost_slippage_stress": "...",
        "parameter_neighborhood_stability": "...",
    },
}
hypothesis_example = json.dumps(
    example, separators=(",", ":"), ensure_ascii=True
)
```

Insert the same `hypothesis_example` string into the normal and sanitized
section lists. Do not hand-maintain two schema examples.

- [ ] **Step 5: Pass exact note IDs into schema text**

In the runner, replace:

```python
schema_text=schema_prompt_text(),
```

with:

```python
schema_text=schema_prompt_text(
    required_note_ids=book_context.selected_note_ids
),
```

- [ ] **Step 6: Run the prompt and runner tests and verify GREEN**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_runner.py tests/test_hermes_book_runtime.py -q --basetemp C:\Users\lojka\AppData\Local\Temp\pytest-hermes-citation-prompt
```

Expected: all pass.

- [ ] **Step 7: Lock the portable provider response mode**

Extend `test_openai_compatible_extracts_message_content` in
`tests/test_hermes_providers.py`:

```python
request_payload = json.loads(requests[0][0].data.decode("utf-8"))
assert request_payload["response_format"] == {"type": "json_object"}
assert request_payload["stream"] is False
assert request_payload["messages"] == [
    {"role": "user", "content": "safe prompt"}
]
```

Run `tests/test_hermes_providers.py`. Expected: pass without a production
provider change. This is a characterization gate preventing accidental
provider-specific `json_schema`, streaming, or prompt mutation in V1.

- [ ] **Step 8: Commit**

```powershell
git add -- research_lab/llm/hypothesis_adapter.py research_lab/hermes/run_hypothesis_generation.py tests/test_hermes_book_runtime.py tests/test_hermes_providers.py
git commit -m "fix: render mandatory Hermes citation prompt"
```

### Task 3: Fail closed if the citation contract is lost before provider invocation

**Files:**
- Modify: `research_lab/hermes/schema.py`
- Modify: `research_lab/hermes/run_hypothesis_generation.py:120-145`
- Test: `tests/test_hermes_book_runtime.py`

- [ ] **Step 1: Add a deterministic contract renderer**

Before changing the runner, write a schema test for:

```python
from research_lab.hermes.schema import citation_contract_text


def test_citation_contract_text_is_deterministic():
    ids = (
        "note-1111111111111111",
        "note-2222222222222222",
    )
    first = citation_contract_text(ids)
    second = citation_contract_text(ids)

    assert first == second
    assert '["note-1111111111111111","note-2222222222222222"]' in first
```

Expected initial failure: `citation_contract_text` does not exist.

- [ ] **Step 2: Extract the exact renderer**

Implement:

```python
def citation_contract_text(required_note_ids: tuple[str, ...]) -> str:
    if not required_note_ids:
        return ""
    allowed_json = json.dumps(
        list(required_note_ids),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "\n".join(
        [
            "MANDATORY CITATION CONTRACT",
            f"Allowed note IDs (exact JSON array): {allowed_json}",
            (
                "Every hypothesis must contain used_note_ids with 1-5 values "
                "taken only from the exact allowed array."
            ),
            (
                "Empty, missing, malformed, invented, or non-allowlisted "
                "note IDs cause rejection."
            ),
        ]
    )
```

Have `schema_prompt_text()` append this helper's result so the prompt and the
postcondition use one byte-identical contract.

- [ ] **Step 3: Write the provider-before-gate regression**

Monkeypatch `build_hermes_prompt` in the runner module to return a prompt that
omits the exact citation block. Use valid canonical fixture notes and a
provider invoker that raises if called. Assert:

```python
import research_lab.hermes.run_hypothesis_generation as runner

index_path = _write_index(tmp_path / "private")
notes_dir = _write_note(tmp_path / "private")
report = tmp_path / "reports" / "daily" / "2026-06-12.md"
report.parent.mkdir(parents=True)
report.write_text(
    "- biggest risk discovered: drawdown\n",
    encoding="utf-8",
)
monkeypatch.setattr(
    runner,
    "build_hermes_prompt",
    lambda *_args, **_kwargs: '{"hypotheses":[]}',
)
provider_called = False

def provider(*_args):
    nonlocal provider_called
    provider_called = True
    raise AssertionError("provider must not run without citation contract")

outcome = runner.run_hypothesis_generation(
    tmp_path,
    env={
        "HERMES_PROVIDER": "command",
        "HERMES_BOOK_INDEX_PATH": str(index_path),
        "HERMES_BOOK_NOTES_DIR": str(notes_dir),
    },
    provider_invoker=provider,
)

assert provider_called is False
assert outcome["status"] == "citation_contract_unavailable"
assert outcome["artifact_phase"] == "no_queue_change"
assert outcome["queue_impact"]["state"] == "unchanged"
assert outcome["rejection_reasons"] == [
    "citation_contract_missing_from_prompt"
]
```

- [ ] **Step 4: Run the regression and verify RED**

Expected: the provider invoker is reached because the runner currently has no
postcondition.

- [ ] **Step 5: Implement the provider-before-gate**

After prompt construction and before `provider_invoker(...)`:

```python
required_contract = citation_contract_text(
    book_context.selected_note_ids
)
if required_contract and required_contract not in prompt:
    return _finish(
        root,
        {
            **base,
            "status": "citation_contract_unavailable",
            "artifact_phase": "no_queue_change",
            "rejection_reasons": [
                "citation_contract_missing_from_prompt"
            ],
        },
        timestamp_utc,
    )
```

Import the helper from `research_lab.hermes.schema`. Do not log the prompt or
selected note content.

- [ ] **Step 5a: Require selected evidence as well as its citation contract**

Add a regression in `tests/test_hermes_book_runtime.py` that substitutes a
prompt containing the exact citation contract and an allowed note ID, but none
of `book_context.prompt`. Assert that the provider is not called and that the
terminal no-queue-change artifact contains:

```text
status: citation_context_unavailable
reason: selected_book_context_missing_from_prompt
```

After prompt construction and before `provider_invoker(...)`, require the exact
non-empty `book_context.prompt` to survive whenever `selected_note_ids` is
non-empty. This prevents a sanitized citation-only fallback from turning an ID
into unsupported evidence. Do not persist or log the selected context.

- [ ] **Step 6: Remove contradictory secondary semantics**

Keep a defensive runner guard for an unexpectedly accepted empty citation, but
the normal rejection reason must now come from
`validate_hypothesis()` as `missing_used_note_ids`. Update tests that currently
expect `book_evidence_not_used` to expect:

```text
hypothesis_N:missing_used_note_ids
```

Do not auto-repair old provider output.

- [ ] **Step 7: Run all focused Hermes tests and verify GREEN**

Run:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_hermes_schema.py tests/test_hermes_artifacts.py tests/test_hermes_providers.py tests/test_hermes_runner.py tests/test_hermes_book_runtime.py tests/test_candidate_generation_guidance.py tests/test_daily_experiment_selector.py tests/test_daily_runner_dedupe.py tests/test_hermes_queue_mapping.py -q --basetemp C:\Users\lojka\AppData\Local\Temp\pytest-hermes-citation-focused
```

Expected: all pass and no network call occurs because the suite's provider
guard remains active.

- [ ] **Step 8: Commit**

```powershell
git add -- research_lab/hermes/schema.py research_lab/hermes/run_hypothesis_generation.py tests/test_hermes_book_runtime.py
git commit -m "fix: gate Hermes provider on exact citation contract"
```

### Task 4: Document operator semantics and verify the complete change

**Files:**
- Modify: `docs/hermes_scheduling.md`
- Verify: all changed production and test files

- [ ] **Step 1: Update operator documentation**

Document:

- selected note IDs form an exact per-run allowlist;
- every book-informed hypothesis must cite one to five allowed IDs;
- missing or unknown citations cannot change the queue;
- the provider is called once with no automatic retry;
- EODHD runs only after a new hypothesis is imported.

Do not document secrets, private content, raw prompts, or raw responses.

- [ ] **Step 2: Run syntax and diff checks**

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m py_compile research_lab/hermes/schema.py research_lab/hermes/run_hypothesis_generation.py research_lab/llm/hypothesis_adapter.py
git diff --check
```

Expected: exit code 0.

- [ ] **Step 3: Run the complete focused suite**

Run the Task 3 focused command again. Record the exact pass count.

- [ ] **Step 4: Run full pytest**

Run the complete suite through redirected `Start-Process` output and bounded
polling if necessary:

```powershell
& C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest -q --basetemp C:\Users\lojka\AppData\Local\Temp\pytest-hermes-citation-full
```

Expected: zero failures. Existing resource warnings may remain and must be
reported exactly rather than hidden.

- [ ] **Step 5: Commit documentation**

```powershell
git add -- docs/hermes_scheduling.md
git commit -m "docs: document Hermes citation contract"
```

### Task 5: Strict review, publication, synchronization, and one bounded live run

**Files:**
- Review: exact `e5f055938e0fd43666d545cd65d248e77f17203a..HEAD`
- Runtime: existing repository scripts and systemd services only

- [ ] **Step 1: Request independent strict review**

Provide the reviewer the design, plan, exact base/head SHAs, focused/full test
evidence, and ask for P0/P1/P2 findings covering:

- citation allowlist bypasses;
- prompt fallback losing the contract;
- provider invocation before the gate;
- automatic or forged attribution;
- raw prompt, response, private-path, or secret persistence;
- queue mutation on invalid output;
- retries, fallback models, broker, promotion, deployment, or risk changes.

Do not publish until the verdict is PASS with no unresolved P0/P1/P2.

- [ ] **Step 2: Push and create a draft PR**

Push only `codex/hermes-citation-adherence-v1`. Create a draft PR to `main`
whose body records the root cause, exact reviewed head, tests, and safety
boundaries.

- [ ] **Step 3: Perform GitHub review and exact-head merge**

Verify:

- PR head equals the strict-reviewed SHA;
- merge state is `CLEAN`;
- checks and review threads have no unresolved failures;
- branch protection/ruleset state is reported honestly.

Mark ready and merge with an exact-head match. Record PR and merge SHAs.

- [ ] **Step 4: Fast-forward the separate clean main worktree**

In `C:\Users\lojka\trading\research-lab-volume-lineage-fix`:

```powershell
git fetch origin main
git merge --ff-only origin/main
git diff --check
```

Prove local `main == origin/main`.

- [ ] **Step 5: Safe-sync Hetzner**

Use only:

```powershell
ssh hetzner-research "cd /opt/trading/research-lab && bash scripts/run_safe_sync_with_preflight.sh"
```

Run the same focused suite on Hetzner before any provider call. Prove remote
HEAD equals local/origin and tracked status is clean.

- [ ] **Step 6: Snapshot safe live state**

Read only:

- Hermes and daily service active states;
- queue row count and SHA-256;
- canonical book index/notes availability;
- current Git SHA.

Do not print `.env`, credentials, raw prompts, raw responses, or book text.

- [ ] **Step 7: Start Hermes exactly once**

Start the existing `hermes-hypothesis.service` once through systemd and poll to
a terminal state. Do not send a second start, retry, or alternate provider
request.

Inspect only:

- status and artifact phase;
- canonical blocker;
- selected note/book counts;
- generated/imported/rejected counts;
- imported hypothesis IDs;
- bounded rejection reason codes;
- queue row count and SHA-256.

- [ ] **Step 8: Conditionally run EODHD once**

If and only if `imported_hypotheses_count > 0`, start the existing daily
research service once. Verify the imported ID reaches either:

- a persisted research result with normal walk-forward, drawdown, cost, and
  stability fields; or
- an explicit deterministic selection, dedupe, missing-data, or validation
  rejection.

If the import count is zero, do not start EODHD. Report the exact citation
failure and unchanged queue proof.

- [ ] **Step 9: Prove final alignment and safety**

Report:

- PR and merge SHA;
- local/origin/Hetzner SHA equality;
- focused/full/remote test counts;
- strict-review verdict;
- services inactive at handoff;
- final queue count/hash;
- whether EODHD ran and why;
- zero broker, promotion, deployment, or risk-limit actions.
