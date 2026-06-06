#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "${script_dir}/.." rev-parse --show-toplevel)"
cd "${repo_root}"

log_path="logs/hetzner-sync.log"
if [ ! -d "logs" ]; then
  printf 'Aborting: logs directory is missing at %s/logs\n' "${repo_root}" >&2
  exit 1
fi

{
  printf '=== Hetzner sync preflight %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  bash scripts/check_hetzner_sync_readiness.sh
  printf '=== Hetzner safe sync %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  bash scripts/sync_from_github.sh
  printf '=== Hetzner sync complete %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} 2>&1 | tee -a "${log_path}"
