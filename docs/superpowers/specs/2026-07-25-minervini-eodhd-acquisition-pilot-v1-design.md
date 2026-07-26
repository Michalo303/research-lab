# Minervini EODHD Acquisition Pilot V1 Design

## Goal

Prove, with a small bounded provider run, that the existing EODHD subscription
can support a survivorship-aware, split-adjusted, immutable US common-stock
dataset for the frozen Minervini Price/Volume Core V1.

The pilot estimates the exact wide-acquisition request count, call-unit bound,
runtime, storage range, historical coverage, symbol-identity risks, and
corporate-action coverage. It does not download the full universe and cannot
run or promote a performance result.

## Chosen acquisition strategy

The future wide acquisition will use one full-history EOD request per
deduplicated symbol, not one whole-exchange bulk request per trading day.

This is preferred because the verified universe currently contains 18,176
active and 32,371 delisted US common-stock rows. One symbol request returns the
available full EOD history and normally costs one call unit. A daily US bulk
request costs 100 call units, so roughly 4,100 historical trading dates would
cost roughly 410,000 units. The per-symbol path is expected to remain near
50,547 EOD call units before deduplication, although it creates more HTTP
requests and therefore needs resumable acquisition.

Splits will use the historical split-calendar or another measured bounded
range/page path when the pilot proves its coverage. Per-symbol split requests
remain the fail-closed fallback estimate, not an automatically authorized
fallback action.

Two rejected alternatives are:

- daily entire-US bulk history, because it consumes roughly four times as many
  call units even though it uses fewer HTTP requests;
- a current-liquidity or current-membership universe, because that would
  introduce survivorship bias and cannot establish edge.

## Frozen historical interval

The prospective immutable dataset interval is:

- raw acquisition interval: `2010-01-01` through `2025-12-31`;
- indicator warm-up interval: `2010-01-01` through `2012-12-31`;
- frozen primary evaluation interval: `2013-01-02` through `2025-12-31`.

The pilot must not calculate strategy signals, returns, or drawdown. These
dates may not be changed after performance is observed without creating a new
versioned research hypothesis.

## Provider request budget

Live mode performs at most 24 read-only requests, exactly once each and without
retry, fallback, pagination, or health-check calls:

1. active US common-stock exchange list;
2. delisted US common-stock exchange list;
3. US symbol-change history for `2010-01-01` through `2025-12-31`;
4. one historical split-calendar coverage request for the frozen interval;
5. twenty full-history EOD sample requests.

The twenty-symbol sample is deterministic:

- `SPY.US` as the market proxy;
- `AAPL.US` plus eight active common stocks selected by lowest SHA-256 rank of
  the normalized provider identity;
- `ATVI.US` plus nine delisted common stocks selected by the same rule;
- duplicates are removed and the next ranked identity fills the slot.

The result records both request count and documented estimated call units.
Missing provider consumption headers are reported as unavailable and never
invented.

## Command boundary

The CLI defaults to a zero-network, zero-write dry run:

```text
python scripts/run_minervini_eodhd_acquisition_pilot_v1.py
```

Live mode requires all of:

```text
--execute-live
--output-dir <absolute-existing-or-new-local-directory>
--expected-provider-requests 24
```

The credential comes only from `EODHD_API_KEY`. It is never accepted in a
manifest, command-line argument, log, output artifact, exception, or sanitized
endpoint identity.

The output directory must be absolute, local, not a symlink, and dedicated to
one new pilot run. The pilot refuses to overwrite an existing non-empty
directory. Process stdout and stderr redirection targets are operational
evidence, not provider artifacts, and must be created outside the output
directory. This matters for launchers such as PowerShell `Start-Process`,
which create redirection targets before the Python process validates the
directory.

## Immutable artifacts

Each successful HTTP response is written once as the exact response bytes
without request headers or secret-bearing URLs. Every artifact records:

- sanitized endpoint identity;
- request ordinal;
- retrieval timestamp in UTC;
- HTTP status;
- response byte length;
- SHA-256 of exact response bytes;
- parsed row count;
- schema validation result.

The run also writes:

- `active-common-stocks.json`;
- `delisted-common-stocks.json`;
- `symbol-change-history.json`;
- `split-calendar-sample.json`;
- one EOD response per sampled ticker;
- an append-only request journal;
- a canonical pilot result manifest written atomically last.

If any request or validation fails, already written raw evidence remains
immutable and the failure journal identifies the exact stopping point. A
failed directory is never resumed in place; a later attempt uses a new run
directory.

## Validation

### Universe

- active and delisted payloads must be non-empty arrays;
- every retained row must declare `Type=Common Stock`;
- provider codes are normalized without guessing an exchange suffix;
- duplicates within and across lists are reported;
- active/delisted collisions are blocking;
- the deduplicated identity count and SHA-256 are recorded.

### Symbol changes

- old symbol, new symbol, effective date, and exchange are normalized;
- dates must stay within the requested interval;
- ambiguous chains, cycles, or collisions are blockers;
- no automatic identity merge occurs in the pilot.

### EOD sample

- payload must be a non-empty chronological array;
- timestamps must be unique;
- OHLC values must be finite and internally valid;
- volume must be finite and non-negative;
- `adjusted_close` must be present and positive;
- first date, last date, row count, gaps, raw bytes, and SHA-256 are recorded;
- `SPY`, at least one active common stock, and at least one delisted common
  stock must have usable coverage.

Raw OHLC is not falsely labeled split-adjusted. EODHD adjusted close is
adjusted for both splits and dividends, so the wide dataset must reconstruct
split-only OHLC from verified split events. EOD volume may be accepted as
split-adjusted only when the provider contract and pilot evidence support that
classification.

### Splits

- the response schema and date coverage are recorded;
- pagination metadata, total records, and page limit are recorded when
  provided;
- if the single bounded response cannot prove complete interval coverage, the
  wide-acquisition estimate includes the measured page count or the
  per-symbol fallback bound;
- no second split page is fetched by V1 pilot.

## Estimation result

The result computes without downloading the wide dataset:

- exact active, delisted, duplicate, collision, and deduplicated counts;
- full-history EOD request count;
- measured split request/page estimate;
- symbol-change request count;
- total HTTP request and call-unit lower/upper bounds;
- minimum acquisition days under a 100,000-unit daily cap;
- minimum runtime under the 1,000-request-per-minute cap;
- conservative runtime at five requests per second;
- raw and normalized storage lower/upper bounds from sample bytes and rows;
- sampled coverage distribution and blocking failure reasons.

No estimate is presented as exact when the pilot does not provide exact source
metadata.

## Verdicts

`READY_FOR_WIDE_ACQUISITION_APPROVAL` requires:

- all 24 requests attempted exactly once;
- all required capabilities and schemas validated;
- no secret leakage;
- usable active, delisted, SPY, split, and symbol-change evidence;
- no identity collision that prevents a deterministic wide manifest;
- deterministic replay of the pilot manifest from raw artifacts.

Otherwise the result is one of:

- `BLOCKED_PROVIDER_CAPABILITY`;
- `BLOCKED_IDENTITY_AMBIGUITY`;
- `BLOCKED_SPLIT_LINEAGE`;
- `BLOCKED_SAMPLE_COVERAGE`;
- `FAILED_VALIDATION`.

Even a ready verdict does not authorize the wide acquisition. The exact
estimated request/call/storage envelope must be shown to the user for separate
approval.

## Safety invariants

The pilot performs:

- no broker, paper, or live-trading action;
- no strategy evaluation or parameter tuning;
- no registry write or promotion;
- no deployment or service restart;
- no modification of Knihomol, Hermes, or research hypotheses;
- no remote Hetzner write unless a later reviewed PR is merged and synchronized
  through the existing safe-sync wrapper.

Provider calls are read-only and bounded. Raw provider evidence is local,
immutable, secret-free, and review-only.

## Test strategy

Tests inject an HTTP getter and use no network. They cover:

- dry-run zero-call and zero-write behavior;
- exact 24-call order and hard cap;
- deterministic sample selection;
- secret redaction from URLs, errors, journals, and manifests;
- raw-byte hashing and atomic final manifest;
- active/delisted deduplication and collision blockers;
- symbol-change ambiguity and cycle blockers;
- malformed, unordered, duplicate, and incomplete EOD rows;
- split coverage and pagination estimation;
- deterministic estimation and replay;
- fail-closed partial-run artifacts;
- zero broker, registry, promotion, and deployment actions.

After focused tests pass, exactly one live pilot may be run. A second live run
requires a new explicit instruction because V1 has no retries.
