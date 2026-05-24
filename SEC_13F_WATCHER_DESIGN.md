# SEC 13F Watcher Design

Use SEC EDGAR as the primary unattended source for new filings.

## Why SEC First

- Official source.
- No vendor lock-in.
- Updated throughout the day.
- No API key required for public data.
- Clear fair-access rules.

SEC says its data APIs do not require authentication, and submissions data is updated throughout the day in real time. SEC fair-access guidance limits automated traffic to no more than 10 requests per second and requires a declared User-Agent.

## WhaleWisdom Role

WhaleWisdom is useful for normalized 13F analytics, WhaleScore, backtests, API access, and possibly Enterprise nightly FTP files.

Do not scrape the WhaleWisdom UI. Their subscription information says free access is subject to limits/throttling and no automated scripting. Standard/Pro include API access with limits; Enterprise includes unlimited API and nightly FTP updates.

## Lab Implementation

Hourly source scan checks:

```text
sec_current_13f_hr
sec_current_13f_hr_a
```

These feeds are treated as source events. They generate hypotheses but do not imply a tradable signal.

Next step:

1. Parse SEC filing metadata.
2. Download the XML information table for whitelisted managers only.
3. Convert CUSIP/name holdings to ticker candidates where possible.
4. Feed candidates into the smartmoney swing universe.
5. Let the deterministic runner validate price-based entries.

## Rule

New 13F filing equals attention event.

It does not equal buy signal.

