#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "${script_dir}/.." rev-parse --show-toplevel)"
cd "${repo_root}"

current_branch="$(git branch --show-current)"
current_sha="$(git rev-parse HEAD)"

printf 'Current branch: %s\n' "${current_branch}"
printf 'Current HEAD: %s\n' "${current_sha}"

if [ "${current_branch}" != "main" ]; then
  printf 'Aborting: expected to run on main, got %s\n' "${current_branch}" >&2
  exit 1
fi

tracked_status="$(git status --porcelain --untracked-files=no)"
if [ -n "${tracked_status}" ]; then
  printf 'Aborting: tracked working tree changes are present:\n%s\n' "${tracked_status}" >&2
  exit 1
fi

git fetch origin main

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse origin/main)"
merge_base="$(git merge-base HEAD origin/main)"

if [ "${local_sha}" = "${remote_sha}" ]; then
  printf 'Local main already matches origin/main.\n'
elif [ "${merge_base}" != "${local_sha}" ]; then
  printf 'Aborting: local main cannot fast-forward to origin/main.\n' >&2
  printf 'Local HEAD: %s\n' "${local_sha}" >&2
  printf 'Origin main: %s\n' "${remote_sha}" >&2
  printf 'Merge base: %s\n' "${merge_base}" >&2
  exit 1
fi

git pull --ff-only origin main

final_sha="$(git rev-parse HEAD)"
printf 'Final HEAD: %s\n' "${final_sha}"
printf 'Sync from origin/main completed successfully.\n'
