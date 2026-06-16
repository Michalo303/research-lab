# Hetzner Access Runbook

This note records the verified access path and safe verification commands for the
research-lab Hetzner checkout. It intentionally contains no secrets.

## SSH Target

- Verified SSH target: `trading@91.99.99.158`
- Do not rely on SSH alias `hetzner`; it may not exist in the local environment.
- Expected remote user: `trading`

Before any Hetzner action, verify connectivity:

```bash
ssh -o BatchMode=yes trading@91.99.99.158 "hostname && whoami"
```

If the session is already logged in as `trading`, do not use `sudo`.

## Read-Only Sync Verification

Use these commands to verify the server checkout state:

```bash
ssh -o BatchMode=yes trading@91.99.99.158 '
cd /opt/trading/research-lab
git branch --show-current
git rev-parse HEAD
git status --short
'
```

Compare the server HEAD with local `origin/main`:

```bash
git fetch origin main
git rev-parse origin/main
```

If the server HEAD already matches `origin/main`, do not run manual sync.

## Autosync

Autosync is expected to be installed under these systemd units:

- `research-lab-sync.timer`
- `research-lab-sync.service`

The timer was verified enabled and active on 2026-06-06.

Read-only status checks:

```bash
ssh -o BatchMode=yes trading@91.99.99.158 '
systemctl list-unit-files research-lab-sync.service research-lab-sync.timer --no-pager
systemctl list-timers --all --no-pager research-lab-sync.timer
systemctl show research-lab-sync.timer -p LoadState -p ActiveState -p UnitFileState -p LastTriggerUSec -p NextElapseUSecRealtime --no-pager
systemctl show research-lab-sync.service -p LoadState -p ActiveState -p SubState -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp --no-pager
'
```

## Manual Safe Sync

For local preflight and future Hetzner sync preparation on Windows, use native
Git Bash at `/c/Users/lojka/trading/research-lab` with `/mingw64/bin/git`.
Do not use WSL Git through `/mnt/c/...` for this checkout, because it has shown
false tracked-diff noise in `backtests/` and `reports/` due to Git view and
line-ending mismatch.

Only run manual sync when all of these are true:

- the server checkout is on `main`;
- tracked `git status --short` is clean;
- server HEAD is behind `origin/main`;
- a fast-forward from `origin/main` is possible.

Use the documented safe sync wrapper from the server checkout:

```bash
ssh -o BatchMode=yes trading@91.99.99.158 '
cd /opt/trading/research-lab
bash scripts/run_safe_sync_with_preflight.sh
'
```

The wrapper runs `scripts/check_hetzner_sync_readiness.sh` before
`scripts/sync_from_github.sh`, and the sync script only permits a fast-forward
from `origin/main`.

## Forbidden Actions

Do not perform any of these unless explicitly instructed:

- restart production research, sync, or dashboard services;
- edit `.env`, credentials, provider secrets, API keys, or production secrets;
- delete runtime artifacts under `data/`, `registry/`, `reports/`, or `backtests/runs/`;
- run `git reset --hard`;
- run `git clean`;
- deploy or promote strategies by bypassing validation gates.
