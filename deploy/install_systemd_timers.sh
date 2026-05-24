#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/trading/research-lab}"
SYSTEMD_DIR="/etc/systemd/system"

cd "$PROJECT_ROOT"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
pip install -e .

if [[ ! -f ".env" ]]; then
  cp .env.example .env
fi

sudo cp deploy/systemd/trading-research-*.service "$SYSTEMD_DIR/"
sudo cp deploy/systemd/trading-research-*.timer "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable --now trading-research-hourly.timer
sudo systemctl enable --now trading-research-daily.timer
sudo systemctl enable --now trading-research-weekly.timer
sudo systemctl enable --now trading-research-self-improvement.timer

systemctl list-timers 'trading-research-*'

