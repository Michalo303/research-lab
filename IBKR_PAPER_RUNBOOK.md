# IBKR Paper Trading Runbook

IBKR account:

```text
Set `IBKR_ACCOUNT` only in the server `.env`; do not commit account identifiers.
```

This project remains research-first. IBKR integration starts as paper-only and read-only.

## Intended Architecture

```text
research-lab
  -> approved paper signal file
  -> execution gateway
  -> IB Gateway / TWS paper session
  -> IBKR paper account
```

The research runner must not place orders directly.

## Safety Gates

Default:

```text
IBKR_MODE=paper
IBKR_READ_ONLY=1
```

Paper order placement is blocked unless this file exists:

```text
APPROVED_FOR_PAPER_IBKR_ORDERS.md
```

Paper order placement also requires all of these settings:

```text
IBKR_MODE=paper
IBKR_READ_ONLY=0
RESEARCH_LAB_ALLOW_PAPER_ORDERS=YES_I_UNDERSTAND
IBKR_ACCOUNT=<paper account>
```

If the account is not clearly DU-prefixed, add it to `IBKR_PAPER_ACCOUNT_ALLOWLIST` in `.env`.

Live IBKR trading is not supported by this scaffold. If this file exists, the gateway refuses to run:

```text
APPROVED_FOR_LIVE_IBKR.md
```

## Server `.env`

```bash
IBKR_ACCOUNT=
IBKR_MODE=paper
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
IBKR_READ_ONLY=1
RESEARCH_LAB_ALLOW_PAPER_ORDERS=
IBKR_PAPER_ACCOUNT_ALLOWLIST=
```

Do not store IBKR passwords or 2FA secrets in this repository.

## IB Gateway Requirement

IBKR API access requires IB Gateway or TWS to be running and logged in.

Default ports:

- TWS paper: `7497`
- TWS live: `7496`
- IB Gateway paper: commonly `4002`
- IB Gateway live: commonly `4001`

Use paper only.

## First Check

```bash
cd /opt/trading/research-lab
. .venv/bin/activate
set -a
. ./.env
set +a
python scripts/check_ibkr_paper_config.py
```

This does not connect to IBKR or place orders. It validates the local execution configuration and writes:

```text
reports/execution/ibkr_paper_config_check.json
```

## Read-Only Connection Snapshot

After IB Gateway or TWS paper is running, use:

```bash
python -m pip install ".[ibkr]"
python scripts/run_ibkr_paper_read_only.py
```

This connects with `readonly=True`, requests the configured market data mode, reads managed accounts, account summary, and current positions, then disconnects. It writes:

```text
reports/execution/ibkr_paper_read_only_snapshot.json
```

The snapshot also requests read-only quote checks for `SPY`, `QQQ`, `TLT`, and `GLD`. Quote status is reported as `bid`, `ask`, `last`, `delayed`, `frozen`, `missing`, or `error`. It never falls back to yfinance or another data provider.

If `ib_insync` is not installed or Gateway is not reachable, the script writes a failed/missing-dependency status instead of falling back to any order-capable path.

## Ledger Reconciliation

After a read-only snapshot exists, compare the local paper ledger target positions to IBKR read-only positions:

```bash
python scripts/reconcile_ibkr_paper_positions.py
```

This writes audit-only outputs:

```text
reports/execution/ibkr_reconciliation_<date>.csv
reports/execution/ibkr_reconciliation_<date>.json
```

The reconciliation does not create broker orders and does not auto-correct positions.

## Local Paper Order Simulator

Before any future broker order work, run the local simulator with explicit candidate and price inputs:

```bash
python scripts/run_paper_order_simulator.py --candidates-json tmp/paper_candidates.json --prices-json tmp/latest_prices.json --equity 100000
```

The simulator is local only and writes append-only records:

```text
registry/paper_order_simulations.jsonl
```

Required simulator guardrail:

```bash
PAPER_ORDER_STRATEGY_ALLOWLIST=STRATEGY_ID_1,STRATEGY_ID_2
```

It applies conservative spread/slippage assumptions and rejects non-allowlisted strategies, excessive notional, missing prices, negative weights when long-only, and non-paper mode.

## Next Implementation Steps

1. Install IB Gateway on the server or choose a separate always-on machine for IB Gateway.
2. Run the read-only connection snapshot until the paper session is stable.
3. Run ledger reconciliation and investigate every missing/extra/diff row.
4. Run paper order simulation with strict allowlist.
5. Keep broker order placement blocked until a separate review explicitly approves it.
