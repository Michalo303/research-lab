# Agent Instructions

## Hetzner sync and deployment constraints

Hetzner production repo:

- Path: `/opt/trading/research-lab`
- Runtime user: `trading`
- Git/sync operations must run as `trading`, not root.
- Root may manage systemd, but must not run repo Git commands directly.

GitHub-to-Hetzner sync is handled by:

- `scripts/check_hetzner_sync_readiness.sh`
- `scripts/sync_from_github.sh`
- `scripts/run_safe_sync_with_preflight.sh`
- `research-lab-sync.service`
- `research-lab-sync.timer`

Safety constraints:

- Do not add `git reset --hard`.
- Do not add `git clean`.
- Do not delete runtime artifacts.
- Do not edit `.env`.
- Do not restart dashboard/research services from sync automation.
- Do not run daily/hourly/weekly/self-improvement research from sync automation.
- Sync must remain preflight-gated and fast-forward only.
- Sync service must run as `User=trading` and `Group=trading`.
- Keep `ProtectSystem=full` and `ProtectHome=true` unless explicitly reviewed.
- Runtime directories must remain owned by `trading:trading`.

Runtime/generated paths must not be committed:

- `registry/`
- `reports/daily/`
- `reports/runs/`
- `data/manifests/`
- `logs/`
