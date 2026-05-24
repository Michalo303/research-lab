# Hetzner Runbook

Target server path:

```text
/opt/trading/research-lab
```

This project should run independently from existing bots:

- `/opt/eurusd-agent-bot`
- `/opt/trading/crypto-pullback`

The research lab must not edit or restart those services.

## Recommended Schedule

Daily light research:

```text
02:30 UTC every day
```

Weekly deep research:

```text
03:30 UTC every Sunday
```

## Suggested systemd Units

Daily:

```ini
[Unit]
Description=Trading Research Lab daily research
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/trading/research-lab
ExecStart=/usr/bin/hermes --prompt-file /opt/trading/research-lab/HERMES_RESEARCH_LAB_PROMPT.md
```

Timer:

```ini
[Unit]
Description=Run Trading Research Lab daily

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
Unit=trading-research-lab.service

[Install]
WantedBy=timers.target
```

If Hermes is not installed on the server, use the deterministic Python runner first and keep the Hermes prompt as the design contract.

## Python Runner Setup

```bash
cd /opt/trading/research-lab
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
python scripts/run_daily_research.py
```

Optional free EOD data support:

```bash
pip install -e ".[data]"
RESEARCH_LAB_USE_YFINANCE=1 python scripts/run_daily_research.py
```

Without optional data support, the runner uses deterministic synthetic OHLCV data as a smoke test only. Synthetic results must not be promoted to deployment candidates.

## Suggested systemd Unit For Python Runner

```ini
[Unit]
Description=Trading Research Lab deterministic daily research
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/trading/research-lab
EnvironmentFile=-/opt/trading/research-lab/.env
ExecStart=/opt/trading/research-lab/.venv/bin/python /opt/trading/research-lab/scripts/run_daily_research.py
```

## First Deployment Principle

Start in research-only mode.

Do not connect exchange keys.
Do not connect broker keys.
Do not deploy generated strategies into production bots automatically.

The first useful output is not a trade. The first useful output is a ranked strategy registry.
