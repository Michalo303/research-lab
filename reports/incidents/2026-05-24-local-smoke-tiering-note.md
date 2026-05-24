# Incident Note - 2026-05-24 Local Smoke Tiering

## Summary

Initial local smoke runs `20260524_001` through `20260524_004` were generated while the runner still capped all synthetic-data results at Tier C before applying rejection gates.

## Impact

Those early local artifacts are preserved for audit history, but their tier labels should not be used for ranking or allocation decisions.

## Correction

The tiering logic now applies hard rejection gates first:

- negative unseen result
- excessive unseen drawdown
- too few trades for trade-based systems
- failed double-cost stress

Only after those gates pass can synthetic or non-production data be capped at Tier C.

## Current Source Of Truth

Use the latest generated files:

- `registry/leaderboard.csv`
- `registry/allocation_model.csv`
- `reports/daily/2026-05-24.md`

Current smoke-test baseline results are rejected and no deployment candidate exists.

