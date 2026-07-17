# M31Q Controlled EODHD Search Batch Executor V1 Design

## Scope

M31Q executes only the externally authorized M31P V3 EODHD Search records. It
does not construct, amend, retry, paginate, or otherwise expand that call set.
The reusable module has no credential discovery and accepts its provider client,
credentials, journal, and result store by injection.

## Architecture

The module has three strict layers:

1. Pure validation and schedule construction recompute the canonical hashes,
   validate the exact 15-record M31P plan, sequence, budgets, request shapes,
   and destinations, and return immutable schedule data. `DRY_RUN` ends here.
2. The journal layer owns atomic exclusive-create artifacts under only the
   injected execution root. It creates intent before any request, then a
   per-sequence started marker before its one possible request, and never
   overwrites or replays existing state.
3. The coordinator supplies the current journal-backed ledger to M31O once per
   scheduled record, persists only validated results, and stops before another
   request on structural, journal, or transport failure. Review-required M31O
   results are persisted and permit the next independently authorized record.

## Execution Modes and Safety

`DRY_RUN` is the default and performs zero provider calls, credential accesses,
and private writes. `APPROVED_EXECUTION` requires all fixed authorization
hashes, the fixed adapter version and zero non-metadata budgets, explicitly
injected client and credentials, `allow_provider_calls=True`, a complete
unchanged M31P approval manifest, and no existing run directory or intent.

The deterministic run directory suffix derives from the approval hash. All
artifacts are canonical JSON written atomically and contain redacted request
data only. Credentials are passed solely to the injected transport/M31O call;
they are never serialized, hashed, logged, or included in exceptions.

## Failure Semantics

An existing intent, started marker, completed marker, result destination,
unsafe path, ledger discrepancy, request mismatch, M31O validation failure, or
transport failure blocks the next request. A started marker without its
completed marker returns
`MANUAL_REVIEW_REQUIRED_POSSIBLE_CALL_ALREADY_CONSUMED`; it is never resumed.
The in-memory counter is derived from durable markers and cannot be lowered by a
caller-provided ledger.

## Tests

Tests use only fake clients and temporary injected stores. They cover pure
validation and deterministic dry runs first, then journal ordering/replay
protection, one-call M31O delegation and artifact redaction, review continuation,
and first-failure stopping. The suite also proves absence of all forbidden call
classes and writes outside the injected root.

## Non-goals

M31Q does not authorize or make historical, corporate-action, dividend, split,
calendar, fundamental, price, broker, SPY, retry, fallback, pagination, or
health-check calls. It does not promote results or enable production runtime.
