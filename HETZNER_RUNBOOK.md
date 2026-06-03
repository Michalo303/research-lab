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

## EODHD Long-History EOD Setup

Add only the server-side `.env` values:

```bash
RESEARCH_LAB_DATA_PROVIDER=eodhd
EODHD_API_KEY=your_key_here
EODHD_START_DATE=1990-01-01
```

Run one access diagnostic before the daily runner:

```bash
cd /opt/trading/research-lab
. .venv/bin/activate
python scripts/check_eodhd_access.py --symbol SPY.US --daily-start 1990-01-01
python scripts/run_eodhd_historical_validation.py
```

Run one manual validation:

```bash
cd /opt/trading/research-lab
. .venv/bin/activate
python scripts/run_daily_research.py
cat data/manifests/daily_universe.json
```

The manifest must show `"source": "eodhd"` and a history length matching the available EODHD coverage. If the daily report shows `massive`, systemd did not receive `EODHD_API_KEY` or the server is on an older checkout.

## Massive Stocks Starter Fallback

Use Massive only as an explicit fallback when EODHD is not configured or an EODHD outage is being investigated:

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

## Git-Based Deployment Hygiene

Hetzner research deployment must be reproducible from git `main`. Do not keep manual patches on the server checkout. The server may hold runtime artifacts under `data/manifests/`, `registry/`, `reports/`, and `backtests/runs/`; deployment hygiene checks must not delete or rewrite those paths.

Run the local hygiene check from an updated local `main` before changing the server:

```bash
python scripts/check_hetzner_deployment.py --host trading@hetzner --repo-path /opt/trading/research-lab
```

The check reports:

- local branch and commit,
- expected local `main` commit,
- server branch and commit,
- server `git status --short`,
- whether the server commit differs from local `main`,
- server `.env` presence for `RESEARCH_LAB_DATA_PROVIDER`, `EODHD_START_DATE`, and presence-only checks for `EODHD_API_KEY` and `MASSIVE_API_KEY`.

The script redacts key-like, token-like, password-like, and known secret values before printing. It reports `EODHD_API_KEY` and `MASSIVE_API_KEY` as booleans only.

To print deployment recommendations, ask for them explicitly:

```bash
python scripts/check_hetzner_deployment.py --host trading@hetzner --repo-path /opt/trading/research-lab --recommend-deploy --dry-run
```

This prints the intended `git checkout main`, `git pull --ff-only origin main`, and systemd restart steps without running them. If the server checkout is dirty, treat that as a deployment blocker unless you have intentionally reviewed the server changes and use the check's `--force-dirty` override.

Manual server deployment, after the hygiene check is clean:

```bash
cd /opt/trading/research-lab
git status --short
git checkout main
git pull --ff-only origin main
. .venv/bin/activate
pip install -e ".[data]"
sudo systemctl daemon-reload
sudo systemctl restart trading-research-daily.timer
```

Do not run `scripts/run_daily_research.py` as part of the hygiene check. Daily research is owned by systemd timers unless you are intentionally performing a separate manual validation.

Optional tiny smoke checks:

```bash
python scripts/check_hetzner_deployment.py --host trading@hetzner --repo-path /opt/trading/research-lab --smoke
```

The optional smoke list is intentionally small:

- import smoke: `python -c "import research_lab"`,
- EODHD tiny access diagnostic: `python scripts/check_eodhd_access.py --symbol SPY.US --daily-start 2026-01-01`,
- systemd timer/service status: `systemctl status trading-research-daily.timer trading-research-daily.service --no-pager`.

## Rollback

Before deployment, record the previous server commit:

```bash
cd /opt/trading/research-lab
git rev-parse HEAD
```

If a rollback is needed:

```bash
cd /opt/trading/research-lab
git status --short
git checkout <previous_commit>
sudo systemctl daemon-reload
sudo systemctl restart trading-research-daily.timer
sudo systemctl status trading-research-daily.timer --no-pager
```

If you take a manual backup, store it outside runtime artifact paths, for example:

```bash
cp -a /opt/trading/research-lab /opt/trading/backups/research-lab-YYYYMMDD-HHMMSS
```

Do not back up secrets into shared logs. Do not print `.env` values during rollback or deployment.

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
