# M31Q Controlled EODHD Search Batch Executor V1 Design

## Scope and fixed authorization

M31Q is an injected, controlled executor for exactly the 15 externally approved
M31P V3 EODHD Search records. It neither creates nor amends authorization. Its
only executable authorization is `AUTHORIZE_CONTROLLED_EODHD_SEARCH_RESOLUTION_V2`
with approval-manifest hash
`3d4e7105b1637c37708fd6462460a3d6e18a686f2ef9ca9addc50ab36d6a4b0c`,
acquisition-plan hash
`dc7c89fb7212dac8c564d3f04759f82b39add96dae090750d2716ab52c995b43`,
M31I hash `d32525c57a865b3d2f4447ff9ac87da0466bb7a1a3096ab49b80eb17d5bd9c02`,
M31N hash `6822c2a00d7365b8f04c43e4e799829ea7eb9e2e9efea99f05c631fb3d07836b`,
and adapter version `eodhd_approval_bound_search_metadata_adapter_v2`.

The executor requires metadata calls max=15, total calls max=15, response
limit=10, all non-metadata budgets=0, retries=0, sequential-only=true,
stop-on-first-failure=true, fallback=false, pagination=0, and health checks=0.
It rejects the superseded approval hashes named in M31P and any hash other than
the fixed authorization above. It never calls SPY or any historical,
corporate-action, dividend, split, calendar, fundamental, price, broker, or
health endpoint. Production runtime remains unsupported.

## Request and immutable upstream binding

`run_controlled_eodhd_search_batch_v1` accepts exactly these fields:
`version`, `execution_request_id`, `mode`, `m31i_manifest`,
`expected_m31i_canonical_manifest_sha256`, `m31n_capability_manifest`,
`expected_m31n_canonical_capability_manifest_sha256`, `m31p_readiness_result`,
`m31p_approval_manifest`, `external_approved_approval_manifest_sha256`,
`external_approved_acquisition_plan_sha256`, `approved_budget_policy`,
`m31o_adapter_contract_version`, `allow_provider_calls`, `journal`,
`result_store`, `provenance`; approved execution additionally requires
`provider_client` and `credential`. Unknown or missing fields are rejected.

The executor deep-copies the request before validation and never mutates input.
It generates no IDs and uses no clock. It recomputes canonical JSON SHA-256 for
M31I, M31N, the M31P approval manifest, every complete-plan record, and the
complete plan. It requires that M31P itself binds the recomputed M31I/M31N
hashes, exact M31O version, its canonical approval hash, and its plan hash.
The approved manifest must contain exactly the authorized subset of the exact
complete plan, with immutable membership, no extras, no omissions, no duplicate
sequence, and no duplicate destination. The schedule is ascending integer
sequence `1..15`; each record must be executable, have `call_count=1`, an exact
`/api/search/{isin}` path, and exact `{exchange,type,limit=10,fmt=json}`
parameters. The executor checks all fixed authorization values before any
private write or credential access.

## Modes and deterministic paths

Only `DRY_RUN` and `APPROVED_EXECUTION` exist. `DRY_RUN` performs all pure
validation, creates an immutable schedule and deterministic audit output, and
performs zero provider calls, credential accesses, journal writes, result-store
writes, or filesystem writes. It cannot receive `allow_provider_calls=true`.

The only journal execution root is
`/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3/run-3d4e7105b1637c37/`.
Its suffix derives solely from the fixed approval hash; it is never clock based.
It holds intent, markers, and summary only. M31P's already-hashed result
destinations are immutable descendants of its
`pending_exact_symbol_resolution_v3/` parent rather than the journal run
directory; the result store may publish only this exact validated set and no
other child of that parent. Every destination must be normalized, unique, and
free of traversal, symlink escape, canonical-snapshot collision, and SPY
collision. No code deletes, cleans up, overwrites, or recreates any artifact.

## Journal and result-store protocols

The injected journal is authoritative and supplies: inspect run existence;
exclusive create of execution intent; inspect a sequence; exclusive create of a
`CALL_STARTED` marker; exclusive create of a `CALL_COMPLETED` marker; exclusive
create of an execution summary; list sequence states; and read persisted
artifacts for reconciliation. A caller ledger is never authoritative; every
M31O call receives a ledger derived from durable completed/started markers and
cannot lower or reset accounting. The real journal uses exclusive create plus
temporary-file, fsync where supported, and atomic rename for complete JSON
publication. Existing artifacts always block; there is no silent overwrite or
delete-and-recreate behavior.

The injected result store exclusively publishes exactly one canonical, redacted
result artifact per approved destination. It rejects overwrite, duplicate path,
unsafe path, outside-root writes, symlink escape, canonical snapshot collision,
and SPY collision. Result publication uses a temporary file and atomic rename;
the raw credential object and its string representation are never serialized,
hashed, logged, or included in raised errors.

## Approved execution order and accounting

After all pure validation succeeds, `APPROVED_EXECUTION` must: (1) inspect and
reconcile journal state; (2) refuse any existing run, intent, completed marker,
or inconsistent state; (3) exclusively create a redacted execution intent;
(4) for each scheduled sequence, re-read journal state and destination absence,
exclusively create `CALL_STARTED`, invoke M31O exactly once, increment attempted
metadata-call accounting exactly once after that attempted call, validate the
adapter result, exclusively publish the result, exclusively create
`CALL_COMPLETED`, and reconcile; then (5) exclusively create the deterministic
execution summary. Intent precedes every request and `CALL_STARTED` precedes its
one HTTP request. `CALL_COMPLETED` is written only after one response passes
adapter validation and the result has been persistently published.

The M31O request uses the fixed approval manifest, exact record and sequence,
fixed external hashes, `allow_provider_calls=true`, the injected client and
credential, and journal-derived consumed count. It has zero retry, fallback,
pagination, alternate query/exchange, or health check paths. A returned
`FAILED_VALIDATION`, malformed response, response above limit, or transport
exception fails closed. Persisted `REVIEW_REQUIRED_NO_EXACT_MATCH`,
`REVIEW_REQUIRED_AMBIGUOUS_EXACT_MATCH`,
`REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY`, and
`REVIEW_REQUIRED_PROVIDER_NAMESPACE` are valid completed outcomes and permit
the next independently approved record, but are never labelled resolved.

## Crash windows and failure states

Before intent creation, no call could have occurred and the run may be started.
After intent but before `CALL_STARTED`, the existing intent blocks execution and
requires manual inspection; no automatic resume is permitted. After a started
marker but before the request, during the request, after the response but before
completion, after result publication but before completion, or after completion
but before summary, reconciliation fails closed. Any `CALL_STARTED` without the
matching `CALL_COMPLETED` always returns
`MANUAL_REVIEW_REQUIRED_POSSIBLE_CALL_ALREADY_CONSUMED` and never retries or
resumes that sequence. A missing summary after all complete markers is also
manual-review-only, rather than automatically republishing summary evidence.

Structural validation errors, transport errors, malformed provider responses,
exact resolution, review-required no match, review-required ambiguity,
review-required taxonomy, review-required namespace, and uncertain possible
call consumption have distinct stable statuses. The executor stops before any
later provider call for structural, journal, path, budget, credential-leakage,
transport, adapter-validation, result-store, or completed-marker failure.

## Audit output and tests

Audit output contains only redacted deterministic data: fixed input hashes,
schedule, journal-derived attempted/completed counts, completed sequences,
review outcomes, uncalled sequences, raw-response hashes, adapter-result hashes,
and a canonical execution-summary hash. It includes zero retries, fallback,
pagination, health, historical, corporate-action, calendar, broker, Fio, IBKR,
SPY refetch, canonical-snapshot mutation, and production-runtime support.

Tests use fake clients plus in-memory and temporary-filesystem journals/stores.
They first prove strict validation and zero-side-effect `DRY_RUN`; then prove
intent/start/completion ordering, all crash windows and replay refusal, path and
credential redaction, first-failure stopping, review-required continuation, and
the complete exact 15-call batch. No test makes a network request or handles a
real credential.
