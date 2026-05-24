# IBKR Paper Trading Runbook

IBKR account:

```text
jcbfp583
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

Live IBKR trading is not supported by this scaffold. If this file exists, the gateway refuses to run:

```text
APPROVED_FOR_LIVE_IBKR.md
```

## Server `.env`

```bash
IBKR_ACCOUNT=jcbfp583
IBKR_MODE=paper
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=583
IBKR_READ_ONLY=1
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

## Next Implementation Steps

1. Install IB Gateway on the server or choose a separate always-on machine for IB Gateway.
2. Add a read-only connection test using `ib_insync`.
3. Query account summary and positions from paper account.
4. Add paper order simulation with strict allowlist.
5. Add paper order placement only after explicit approval.

