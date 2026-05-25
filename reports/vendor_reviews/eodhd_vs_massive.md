# EODHD vs Massive Historical Data Review

Research-only vendor review. This report is factual and does not create trading permission.

## Coverage Snapshot

- EODHD symbols audited: 10
- EODHD available/partial symbols: 10
- EODHD history range across audited symbols: 21.51 to 33.31 years
- Massive current manifest source: synthetic
- Massive current manifest rows: 3600
- Massive current manifest years: 13.79

## Per-Symbol Quality

Missing weekdays are an approximate calendar-gap count and can include exchange holidays; status is only downgraded when gaps exceed the built-in long-history tolerance or other quality checks fail.

| Symbol | Status | Rows | First | Last | Years | Missing weekdays | Extreme returns | Adjusted |
|---|---:|---:|---|---|---:|---:|---:|---|
| SPY.US | available | 8386 | 1993-01-29 | 2026-05-22 | 33.31 | 305 | 0 | adjusted |
| QQQ.US | available | 6844 | 1999-03-10 | 2026-05-22 | 27.2 | 254 | 0 | adjusted |
| IWM.US | available | 6536 | 2000-05-26 | 2026-05-22 | 25.99 | 245 | 0 | adjusted |
| TLT.US | available | 5995 | 2002-07-26 | 2026-05-22 | 23.82 | 221 | 0 | adjusted |
| GLD.US | available | 5411 | 2004-11-18 | 2026-05-22 | 21.51 | 201 | 0 | adjusted |
| XLK.US | available | 6896 | 1998-12-22 | 2026-05-22 | 27.41 | 258 | 0 | adjusted |
| XLF.US | available | 6896 | 1998-12-22 | 2026-05-22 | 27.41 | 258 | 0 | adjusted |
| XLE.US | available | 6896 | 1998-12-22 | 2026-05-22 | 27.41 | 258 | 0 | adjusted |
| SMH.US | available | 6551 | 2000-05-05 | 2026-05-22 | 26.05 | 245 | 0 | adjusted |
| SOXX.US | available | 6252 | 2001-07-13 | 2026-05-22 | 24.86 | 234 | 0 | adjusted |

## Strategy Validation Scope

Target strategies for longer-history validation:
- ROTATION_ETF_1D_QUEUE_MOM_DD
- ROTATION_ETF_1D_DUAL_MOMENTUM
- LONGTERM_ETF_1D_QUEUE_VOL_TARGET

The scaffold validates EODHD history availability and quality before strategy promotion decisions. It does not modify paper/live execution, broker code, or deployment gates.
