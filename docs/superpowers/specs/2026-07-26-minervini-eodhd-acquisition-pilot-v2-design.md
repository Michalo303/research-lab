# Minervini EODHD Acquisition Pilot V2 Design

## Goal

Prove that the currently active EOD Historical Data — All World subscription
can supply a bounded, survivorship-aware collection of atomic US ticker
histories for the frozen Minervini Price/Volume Core V1.

V2 is a new contract. It does not rewrite the immutable V1 run, which correctly
proved that `symbol-change-history` is unavailable under the current plan.

## Provider capability boundary

V2 uses only capabilities documented for the current EOD plan:

- active US common-stock exchange list;
- delisted US common-stock exchange list;
- full-history EOD OHLCV for one symbol;
- historical splits for one symbol.

It does not call:

- `symbol-change-history`;
- `calendar/splits`;
- fundamentals, screeners, intraday, news, or technical indicators;
- any broker, registry, promotion, or deployment endpoint.

Ticker renames are not inferred or merged. Each normalized EODHD ticker is an
atomic provider identity. The result must state:

```text
identity_continuity_mode=ATOMIC_PROVIDER_TICKER
rename_continuity_supported=False
```

This is sufficient for a first strategy evaluation over contiguous ticker
histories, but it is not evidence of issuer-level continuity across renames.

## Frozen interval and sample

The dates remain unchanged:

- acquisition and warm-up start: `2010-01-01`;
- evaluation start: `2013-01-02`;
- acquisition and evaluation end: `2025-12-31`.

The deterministic sample contains eleven ticker histories:

- `SPY.US` as the market proxy;
- `AAPL.US` and four additional active common stocks selected by the existing
  SHA-256 identity rank;
- `ATVI.US` and four additional delisted common stocks selected by the same
  rank.

Anchor duplicates are removed and the next ranked identity fills the slot.
Input row ordering must not change the sample.

## Exact request budget and order

Live V2 performs exactly 24 requests, once each, without retries, fallbacks,
pagination, health checks, or automatic resumption:

1. active US common-stock list;
2. delisted US common-stock list;
3. EOD history for sample ticker 1;
4. split history for sample ticker 1;
5. EOD history for sample ticker 2;
6. split history for sample ticker 2;
7. through 24: the same pair for sample tickers 3 through 11.

The CLI remains zero-network and zero-write by default. Live execution requires
all of:

```text
--execute-live
--output-dir <absolute empty local directory>
--expected-provider-requests 24
```

Process logs must be outside `--output-dir`. `EODHD_API_KEY` is read only from
the environment and must never appear in arguments, logs, artifacts, endpoint
identities, exceptions, or manifests.

## Validation

### Universe

V2 reuses the V1 fail-closed normalization:

- only exact `Common Stock` rows are eligible;
- active and delisted lists must be non-empty;
- provider codes and identity metadata are normalized without guessing;
- duplicate identities and active/delisted ticker collisions are recorded;
- an active/delisted collision blocks readiness.

### EOD histories

V2 reuses the V1 EOD validator:

- rows are chronological with unique dates;
- OHLC values are finite and internally consistent;
- volume is finite and non-negative;
- adjusted close is finite and positive;
- first date, last date, row count, gaps, bytes, and SHA-256 are recorded.

At least SPY, one active stock, and one delisted stock must have usable history.

### Per-symbol splits

The splits endpoint must return a JSON array. An empty array is valid evidence
that EODHD reported no split for the sampled identity. Each non-empty row must
contain:

- a unique valid `date` within the frozen interval;
- a positive finite split ratio in EODHD `new/old` form.

Rows must be chronological. V2 records row count, first and last split date,
and the exact raw-response SHA-256.

The pilot does not falsely infer historical split completeness for delisted
symbols when the provider does not supply events. Every valid sample is labeled
`PROVIDER_REPORTED_EVENTS_NOT_COMPLETENESS_PROOF`. V2 does not attempt to infer
missing splits from price discontinuities because EODHD adjusted close also
contains dividend adjustments. Malformed ratios, duplicate dates, unordered
rows, or an unavailable endpoint block split lineage; an empty valid response
is retained as limited evidence rather than converted into a claim that no
historical split occurred.

## Immutable artifacts and replay

V2 reuses `MinerviniPilotArtifactWriterV1` and its offline replay contract.
Every response is stored as exact bytes with:

- request ordinal;
- sanitized endpoint identity;
- HTTP status;
- retrieval timestamp;
- parsed row count;
- schema status;
- byte length and SHA-256.

The append-only journal and atomically finalized manifest are written inside
the dedicated output directory. Replay performs no network and no writes and
must verify both raw artifacts and journal lineage.

Failed runs preserve partial evidence and identify the exact stopping ordinal.
A failed directory is never reused.

## Wide-acquisition estimate

The observed universe contains 50,515 atomic ticker codes. V2 computes the
estimate from the newly retrieved lists rather than trusting this prior count.

The EOD-only wide path is:

```text
2 universe requests
+ 1 EOD request per deduplicated ticker
+ 1 split request per deduplicated ticker
```

For 50,515 tickers this is 101,032 HTTP requests and approximately the same
number of call units. Under the documented 100,000-call daily limit it requires
at least two provider days. V2 also estimates runtime and raw storage from the
eleven-symbol sample. Unknown quantities remain explicitly non-exact.

No result authorizes the wide acquisition.

## Verdicts

`READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL` requires:

- exactly 24 successful requests;
- deterministic active and delisted universe without collisions;
- valid EOD and split schemas for all eleven samples;
- usable SPY, active, and delisted histories;
- valid provider-reported split evidence for every sample, with the explicit
  non-completeness classification preserved;
- verified offline replay;
- zero broker, registry, promotion, and deployment actions.

Other verdicts are:

- `BLOCKED_PROVIDER_CAPABILITY`;
- `BLOCKED_UNIVERSE_IDENTITY`;
- `BLOCKED_SPLIT_LINEAGE`;
- `BLOCKED_SAMPLE_COVERAGE`;
- `FAILED_VALIDATION`.

Even the ready verdict authorizes only a separate human review of the measured
request, time, and storage envelope. It does not authorize the 101,000-plus
request acquisition.

## Implementation structure

V2 is implemented in new versioned files:

- `research_lab/research/minervini_eodhd_acquisition_pilot_v2.py`;
- `scripts/run_minervini_eodhd_acquisition_pilot_v2.py`;
- focused V2 unit, CLI, and synthetic end-to-end tests.

It reuses stable V1 artifact and EOD validation behavior but does not execute
or depend on the unavailable V1 request plan. Small provider-neutral helpers
may be extracted from V1 only when both versions receive focused regression
coverage.

## Safety and publication

Before a live V2 run:

- focused and adjacent tests, compilation, and `git diff --check` must pass;
- dry-run must prove zero network and zero writes;
- the worktree must be clean;
- a new explicit authorization is required for the exact 24-request live run.

The live pilot is local only. Raw responses are never committed. A reviewed PR
may be merged and synchronized to Hetzner through the existing safe wrapper,
but the live pilot is not repeated remotely.
