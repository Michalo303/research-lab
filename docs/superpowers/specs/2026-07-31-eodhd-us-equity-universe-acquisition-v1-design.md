# EODHD US Equity Universe Acquisition V1 Design

**Primary objective:** Produce one immutable, point-in-time-aware local EODHD dataset that is small enough for the existing real-Qlib factor pilot but broad enough to test price/volume edge without present-day survivor selection.

This milestone exists only to unlock the already implemented eight-factor economic screen. It does not add factors, change economic gates, consume sealed OOS, construct a portfolio, or authorize broker, registry, deployment, paper, or live activity.

## 1. Evidence and execution host

The approved interval is 2006-01-01 through 2022-12-31. No response or normalized row from 2023 onward may enter the dataset. Discovery remains 2006-2018, development remains 2019-2022, and sealed OOS remains unavailable.

A bounded live probe on 2026-07-31 established:

- 6,346 unique active and 16,419 unique delisted USD common stocks on NASDAQ, NYSE, AMEX, or NYSE MKT;
- approximately 2.7 GB median and 4.6 GB conservative storage for gzip raw histories plus normalized CSV files;
- 31.1 GB local RAM and 507 GB local free disk, versus about 4 GB Hetzner RAM.

Acquisition and the first Qlib economic run therefore execute locally. Hetzner is not used for the memory-intensive calculation.

## 2. Source requests and hard budget

The acquisition request is closed-world and freezes:

- EODHD as the only provider;
- `EODHD_API_KEY` as the only credential source;
- US common stocks in USD;
- final exchange names `NASDAQ`, `NYSE`, `AMEX`, and `NYSE MKT`;
- normalized MICs `XNAS`, `XNYS`, and `XASE`;
- daily history 2006-01-01 through 2022-12-31;
- price USD 5, prior history 252 sessions, trailing median dollar volume USD 10 million, and daily cap 1,500;
- exactly one retry per provider request;
- at most eight concurrent symbol-history requests;
- a total call-unit ceiling of 90,000.

The request sequence is:

1. active common-stock list;
2. delisted common-stock list;
3. SPY daily history used only to derive the last observed US session of each month;
4. 204 US bulk EOD snapshots for those month-end sessions from 2006 through 2022;
5. one full EOD history request for every unambiguous major-exchange common-stock code.

The nominal consumption is approximately 43,168 call units. Retrying every request once remains below the 90,000 ceiling. The executor stops before a request that would breach the ceiling.

## 3. Identity and point-in-time membership

Active and delisted lists are normalized by provider code, exchange, currency, type, and ISIN. Exact duplicates collapse. A code with more than one distinct identity, conflicting active/delisted status, unsafe characters, non-USD currency, non-common-stock type, or unsupported exchange is excluded and reported; it is never guessed.

Monthly bulk snapshots provide historical `exchange_short_name`. A code is eligible for the final dataset only if it appears on a supported major exchange in at least one snapshot. Its normalized CSV is conservatively trimmed to the interval from its first verified major-exchange month-end through its last verified major-exchange month-end. A material internal membership gap while EOD observations continue rejects that identity as ambiguous. This avoids treating known pre-uplisting or post-delisting rows as major-exchange observations.

Monthly membership is an approximation, not exact daily listing history. The result must disclose this limitation and may not claim exact point-in-time exchange membership. The one-month conservative lag and ambiguity rejection are preferred to inventing missing daily exchange lineage.

## 4. Raw and normalized artifacts

The final directory is outside the repository. It is absent until completion. Work occurs in a deterministic sibling staging directory bound to the canonical request SHA-256.

Artifacts include:

- canonical request and sanitized acquisition plan;
- gzip-compressed raw active, delisted, SPY, bulk, and per-symbol responses;
- normalized per-instrument CSV files with exactly `timestamp,open,high,low,close,adjusted_close,volume`;
- point-in-time membership and rejection reports;
- the selected `eodhd_qlib_dataset_manifest_v1`;
- per-file hashes, aggregate counts, call accounting, and `COMPLETE` written last.

Endpoint identities never contain the API token. Errors, logs, checkpoints, manifests, and returned results never contain credentials or unsanitized URLs.

Every response is validated before normalization: HTTPS host and path, HTTP status, response size, UTF-8 JSON, strictly ordered unique dates, and requested interval. Finite positive OHLC and adjusted close, non-negative volume, and valid OHLC relationships are mandatory for SPY, symbol histories, and bulk rows on supported major exchanges. Bulk rows on excluded exchanges are validated only for identity, uniqueness, and requested date because their prices are never admitted to membership or the dataset. Empty histories are recorded as resolved empty coverage rather than silently omitted. Schema-invalid data inside the admitted universe is rejected.

## 5. Resume and failure behavior

Long acquisition must be safely resumable. Each completed artifact is written to a temporary file, reread, hashed, and atomically renamed. The checkpoint records request ordinal, sanitized endpoint identity, attempts, response hash, normalized hash, and terminal status. Resume first validates the request hash and every existing artifact hash; mismatch stops without redownloading or overwriting evidence.

One retry is allowed for timeout, HTTP 429, and HTTP 5xx. Permanent HTTP errors, invalid schemas, hash conflicts, exhausted call budget, insufficient disk, or request-identity drift fail closed. No completed final directory is published after such a failure. The staging directory remains for inspection and exact resume.

## 6. Memory-bounded universe reduction

All valid full histories are retained as evidence, but the Qlib manifest references only instruments that are point-in-time eligible and enter the daily top 1,500 at least once between 2006 and 2022.

Selection is memory-bounded:

1. read one normalized instrument CSV at a time;
2. compute its prior-session count and 63-session trailing median raw dollar volume;
3. retain qualifying `(date, instrument, liquidity)` rows in a staging SQLite database;
4. use deterministic per-date ordering by descending liquidity and ascending instrument ID;
5. select the daily top 1,500 and form the union of selected instruments;
6. build the final manifest from that union, using relative non-symlink CSV paths and exact SHA-256 values.

The existing offline loader recomputes the same eligibility and daily cap. Acquisition reports candidate rows, selected rows, selected instrument count, active/delisted counts, minimum and median daily cross-section size, rejected identities, empty histories, failures, and all provider call units.

## 7. Acceptance and immediate economic run

The acquisition passes only when:

- no credential appears in source, output, errors, or captured logs;
- call accounting is complete and at most 90,000;
- every retained raw and normalized file passes hash verification;
- no normalized timestamp is later than 2022-12-31;
- the final manifest passes the existing `eodhd_qlib_dataset_v1` schema;
- the selected universe contains at least 30 eligible instruments in every evaluated development cross-section;
- unresolved provider/schema failures are no more than 1% of requested unique identities;
- `COMPLETE` is written last and the staging directory is atomically renamed.

After merge and local acquisition, create one exact hash-bound real-Qlib request and run the existing eight-factor pilot once. That run consumes exactly eight global-ledger trials. Its authoritative result is only `EDGE_CANDIDATE_FOUND`, `NO_PRICE_VOLUME_EDGE`, or a fail-closed status.

If no factor continues, stop price/volume work and move to the already planned Sharadar fundamental branch. If a factor continues, the next milestone must construct an actual 10-20 stock portfolio and report net CAGR and maximum drawdown; the factor-screen active return is not presented as strategy CAGR.

## 8. Non-goals

- no sealed-OOS download or read;
- no fundamentals, news, macro, Knihomol, RD-Agent, or OpenRouter use;
- no factor or threshold search;
- no Qlib-native model tuning;
- no portfolio, broker, paper, live, registry, promotion, or deployment action;
- no promise of profitability.
