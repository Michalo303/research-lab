#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "${script_dir}/.." rev-parse --show-toplevel)"
cd "${repo_root}"

failed=0
remote_fetched=0

mark_fail() {
  failed=1
  printf 'FAIL: %s\n' "$1" >&2
}

redact_remote_url() {
  local url="$1"
  if [[ "${url}" =~ ^([^/:]+://)([^/@]+)@(.+)$ ]]; then
    printf '%s[REDACTED]@%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"
  else
    printf '%s\n' "${url}"
  fi
}

print_owner() {
  local path="$1"
  if [ -e "${path}" ]; then
    stat -c 'Ownership: %U:%G %a %n' "${path}"
  else
    printf 'Ownership: missing %s\n' "${path}"
  fi
}

current_user="$(id -un)"
current_branch="$(git branch --show-current)"
current_sha="$(git rev-parse HEAD)"
origin_url="$(git remote get-url origin 2>/dev/null || true)"

printf 'Current user: %s\n' "${current_user}"
printf 'Repository path: %s\n' "${repo_root}"
printf 'Current branch: %s\n' "${current_branch}"
printf 'Current HEAD: %s\n' "${current_sha}"
if [ -n "${origin_url}" ]; then
  printf 'Origin URL: %s\n' "$(redact_remote_url "${origin_url}")"
else
  printf 'Origin URL: missing\n'
fi

if [ "${current_branch}" != "main" ]; then
  mark_fail "expected branch main, got ${current_branch}"
fi

tracked_status="$(git status --porcelain --untracked-files=no)"
if [ -n "${tracked_status}" ]; then
  mark_fail "tracked working tree changes are present"
  printf '%s\n' "${tracked_status}" >&2
else
  printf 'Tracked working tree: clean\n'
fi

printf 'Ignored runtime artifact status:\n'
runtime_status="$(git status --short --ignored -- data/manifests registry)"
if [ -n "${runtime_status}" ]; then
  printf '%s\n' "${runtime_status}"
else
  printf 'No tracked or ignored runtime artifact status entries under data/manifests or registry.\n'
fi

printf 'Ownership checks:\n'
print_owner "${repo_root}"
print_owner "data/manifests"
print_owner "registry"
print_owner "reports/daily"
print_owner "logs"

if git fetch --quiet origin main; then
  remote_fetched=1
  origin_main_sha="$(git rev-parse origin/main)"
  printf 'Origin main: %s\n' "${origin_main_sha}"
else
  mark_fail "origin/main cannot be fetched"
  origin_main_sha=""
fi

if [ "${remote_fetched}" -eq 1 ]; then
  merge_base="$(git merge-base HEAD origin/main)"
  if [ "${current_sha}" = "${origin_main_sha}" ]; then
    printf 'Fast-forward check: already up to date\n'
  elif [ "${merge_base}" = "${current_sha}" ]; then
    printf 'Fast-forward check: local main can fast-forward to origin/main\n'
  else
    mark_fail "local main has local-only commits or diverged from origin/main"
    printf 'Local HEAD: %s\n' "${current_sha}" >&2
    printf 'Origin main: %s\n' "${origin_main_sha}" >&2
    printf 'Merge base: %s\n' "${merge_base}" >&2
  fi
fi

if [ "${failed}" -eq 0 ]; then
  printf 'PASS: Hetzner sync readiness diagnostics passed.\n'
else
  printf 'FAIL: Hetzner sync readiness diagnostics failed.\n' >&2
fi

exit "${failed}"
