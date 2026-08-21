#!/usr/bin/env bash
set -euo pipefail

node_version="${PRODKIT_NODE_VERSION:?PRODKIT_NODE_VERSION is required}"
pnpm_cmd=(corepack pnpm)

"${pnpm_cmd[@]}" --version
"${pnpm_cmd[@]}" install --frozen-lockfile
"${pnpm_cmd[@]}" typecheck:ts
"${pnpm_cmd[@]}" build:ts

if [[ "$node_version" == "24" ]]; then
  rm -rf .artifacts/npm-release
  mkdir -p .artifacts/npm-release
  for package_dir in packages/typescript/*; do
    test -f "$package_dir/package.json" || continue
    "${pnpm_cmd[@]}" --dir "$package_dir" pack --pack-destination "$GITHUB_WORKSPACE/.artifacts/npm-release"
  done
  test -n "$(find .artifacts/npm-release -maxdepth 1 -type f -name '*.tgz' -print -quit)"
fi
