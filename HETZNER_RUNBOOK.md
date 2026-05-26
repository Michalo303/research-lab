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
RESEARCH_LAB_DATA_PROVIDER=yfinance python scripts/run_daily_research.py
```

Without optional data support, the runner uses deterministic synthetic OHLCV data as a smoke test only. Synthetic results must not be promoted to deployment candidates. Real-provider failures are fail-fast by default; set `RESEARCH_LAB_ALLOW_SYNTHETIC_FALLBACK=1` only for intentional local smoke tests.

## Massive Stocks Starter Setup

Add only the server-side `.env` values:

```bash
RESEARCH_LAB_DATA_PROVIDER=massive
MASSIVE_API_KEY=your_key_here
MASSIVE_BASE_URL=https://api.massive.com
MASSIVE_START_DATE=2021-05-24
MASSIVE_ADJUSTED=true
```

Run one manual validation:

```bash
cd /opt/trading/research-lab
. .venv/bin/activate
python scripts/run_daily_research.py
```

Massive's Stocks Starter history is currently suitable for validating the data adapter and medium-term experiments. Long-term and rotation strategies still require 10+ years of EOD evidence before promotion above paper research.

## Suggested systemd Unit For Python Runner

```ini
[Unit]
Description=Trading Research Lab deterministic daily research
After=network-online.target

[Service]
Type=oneshot
User=trading
Group=trading
WorkingDirectory=/opt/trading/research-lab
EnvironmentFile=-/opt/trading/research-lab/.env
ExecStart=/usr/bin/flock -n /opt/trading/research-lab/tmp/research.lock /opt/trading/research-lab/.venv/bin/python /opt/trading/research-lab/scripts/run_daily_research.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/trading/research-lab
TimeoutStartSec=1200
```

## 24/7 Autonomous Research Timers

Use timers instead of one endless process. If the machine reboots, systemd resumes the schedule and writes each run as normal files in the project.

Hourly source scan and hypothesis queue:

```ini
[Unit]
Description=Trading Research Lab hourly source scan
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/trading/research-lab
EnvironmentFile=-/opt/trading/research-lab/.env
ExecStart=/opt/trading/research-lab/.venv/bin/python /opt/trading/research-lab/scripts/run_hourly_research.py
```

```ini
[Unit]
Description=Run Trading Research Lab hourly source scan

[Timer]
OnCalendar=hourly
Persistent=true
Unit=trading-research-hourly.service

[Install]
WantedBy=timers.target
```

Daily deterministic validation:

```ini
[Unit]
Description=Trading Research Lab daily deterministic validation
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/trading/research-lab
EnvironmentFile=-/opt/trading/research-lab/.env
ExecStart=/opt/trading/research-lab/.venv/bin/python /opt/trading/research-lab/scripts/run_daily_research.py
```

```ini
[Unit]
Description=Run Trading Research Lab daily deterministic validation

[Timer]
OnCalendar=*-*-* 02:30:00 UTC
Persistent=true
Unit=trading-research-daily.service

[Install]
WantedBy=timers.target
```

Self-improvement review:

```ini
[Unit]
Description=Trading Research Lab self-improvement cycle
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/trading/research-lab
EnvironmentFile=-/opt/trading/research-lab/.env
ExecStart=/opt/trading/research-lab/.venv/bin/python /opt/trading/research-lab/scripts/run_self_improvement.py
```

```ini
[Unit]
Description=Run Trading Research Lab self-improvement cycle

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true
Unit=trading-research-self-improvement.service

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trading-research-hourly.timer
sudo systemctl enable --now trading-research-daily.timer
sudo systemctl enable --now trading-research-self-improvement.timer
```

Weekly deep research also runs a limited Apify Dataroma holdings import when `APIFY_TOKEN` is set in `/opt/trading/research-lab/.env`. Keep it bounded:

```bash
APIFY_DATAROMA_MAX_RESULTS=200
```

At the actor's public example rate of $2 per 1000 holding rows, 200 rows is about $0.40 per weekly run before any platform-level charges.

Optional network source scanning:

```bash
RESEARCH_LAB_NETWORK=1
```

Keep this disabled until you are comfortable with source volume and logs. Forum sources are watchlists by default; do not run aggressive scrapers against public forums.

The same units are also stored under `deploy/systemd/`. To install them from the server checkout:

```bash
cd /opt/trading/research-lab
bash deploy/install_systemd_timers.sh
```

## First Deployment Principle

Start in research-only mode.

Do not connect exchange keys.
Do not connect broker keys.
Do not deploy generated strategies into production bots automatically.

The first useful output is not a trade. The first useful output is a ranked strategy registry.
