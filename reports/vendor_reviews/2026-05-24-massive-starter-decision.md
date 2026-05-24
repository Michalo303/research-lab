# Massive Stocks Starter Decision - 2026-05-24

Screenshot shows `Stocks Starter` active at `$29/m`.

## Decision

Keep Stocks Starter for one month.

## Why

- Basic is too slow and short for autonomous research because it has tight request limits and only short historical depth.
- Starter gives enough access to validate the server data adapter and run real-data smoke tests.
- It should not be treated as final long-term evidence because the plan's stock aggregate history is around 5 years.

## Lab Rule

Strategies using only Starter-depth data can be researched and rejected, but long-term and rotation strategies cannot be promoted above paper research until they have at least 10 years of EOD validation.

## Next Step

Use the Massive API key in the server `.env` only:

```bash
RESEARCH_LAB_DATA_PROVIDER=massive
MASSIVE_API_KEY=your_key_here
MASSIVE_BASE_URL=https://api.massive.com
MASSIVE_START_DATE=2021-05-24
MASSIVE_ADJUSTED=true
```

