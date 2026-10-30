#!/usr/bin/env bash
set -euo pipefail

node_version="${PRODKIT_NODE_VERSION:?PRODKIT_NODE_VERSION is required}"
# Expose a writable Corepack-managed pnpm shim to child processes such as Turborepo.
# `corepack pnpm` works as a parent command but leaves `pnpm` undiscoverable on
# clean GitHub-hosted runners, causing Turborepo package-manager detection to fail.
pnpm_shims="$(mktemp -d "${RUNNER_TEMP:-/tmp}/prodkit-pnpm.XXXXXX")"
trap 'rm -rf "$pnpm_shims"' EXIT
corepack enable --install-directory "$pnpm_shims" pnpm
export PATH="$pnpm_shims:$PATH"
pnpm_cmd=(pnpm)

"${pnpm_cmd[@]}" --version
"${pnpm_cmd[@]}" install --frozen-lockfile
"${pnpm_cmd[@]}" typecheck:ts
"${pnpm_cmd[@]}" build:ts
node scripts/check_contract_conformance.mjs
node scripts/check_control_react.mjs

if [[ "$node_version" == "24" ]]; then
  rm -rf .artifacts/npm-release
  mkdir -p .artifacts/npm-release
  for package_dir in packages/typescript/*; do
    test -f "$package_dir/package.json" || continue
    "${pnpm_cmd[@]}" --dir "$package_dir" pack --pack-destination "$GITHUB_WORKSPACE/.artifacts/npm-release"
  done
  test -n "$(find .artifacts/npm-release -maxdepth 1 -type f -name '*.tgz' -print -quit)"
fi
