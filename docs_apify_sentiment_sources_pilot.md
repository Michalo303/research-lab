# Next PR Proposal: Apify Sentiment Sources Pilot

Goal: implement a **small, bounded, auditable** Apify sentiment-source pilot (research-only).

## Scope

- Select concrete Apify actors for:
  - Reddit
  - Stocktwits
  - Google News / public news feed
- Test on a tiny pilot universe:
  - `IREN, CRWV, NBIS, WULF, VRT, CEG, OKLO, SMR`
- Pull limited sample data with strict guardrails.
- Normalize payloads into `sentiment_snapshot` schema.
- Save:
  - raw samples (`registry/sentiment_raw_samples/`)
  - normalized `registry/sentiment_snapshot.csv`
- Expose coverage status in read-only reporting/dashboard section.

## Guardrails (mandatory)

- `max_items` hard limit (default low, e.g. 100).
- `max_cost_usd` soft budget guard.
- No uncontrolled loops.
- No background daemon behavior.
- Fail closed to `coverage_status=missing/partial/error`.

## Non-goals

- No trading signals.
- No paper/live execution changes.
- No broker integrations.
- No deployment-gate changes.

## Acceptance checks

1. Provider adapter returns bounded, normalized records.
2. Missing token/actor fails gracefully.
3. Weekly/reporting remains safe-to-fail.
4. Unit tests cover normalization and guardrail enforcement.

## Phase 1 Fixture Contract

The first implementation uses stored raw payload fixtures only:

- `tests/fixtures/apify_reddit_raw.json`
- `tests/fixtures/apify_stocktwits_saswave_raw.json`
- `tests/fixtures/apify_stocktwits_shahidirfan_raw.json`
- `tests/fixtures/apify_google_news_raw.json`

Normalized rows map into the existing file-adapter shape:

- `ticker`
- `provider=apify_fixture`
- `source`
- `timestamp`
- `title`
- `text`
- `url`
- `author`
- `engagement_score`
- `source_type`

Live Apify remains disabled unless the CLI receives `--live-apify`. Missing token or source actor env returns controlled `coverage_status=missing`.
