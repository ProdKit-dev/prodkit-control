#!/usr/bin/env bash
set -euo pipefail

output="${RELEASE_OUTPUT_DIR:?RELEASE_OUTPUT_DIR is required}"
version="${RELEASE_VERSION:?RELEASE_VERSION is required}"
python_version="${PRODKIT_RELEASE_PYTHON_VERSION:-3.13}"
pnpm_version="${PRODKIT_RELEASE_PNPM_VERSION:-10.15.0}"

python3 scripts/release_check.py --version "$version"
python3 scripts/check_contract_authority.py
uv python install "$python_version"
uv sync --all-packages --group dev --locked --python "$python_version"
uv run --python "$python_version" --no-sync python scripts/check_package_completeness.py
uv run --python "$python_version" --no-sync python scripts/check_public_readiness.py
uv run --python "$python_version" --no-sync python scripts/check_contract_conformance.py

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
uv run --python "$python_version" --no-sync prodkit-control demo --output "$tmp/demo"
uv run --python "$python_version" --no-sync python examples/basic_dry_run.py

command -v pnpm >/dev/null
actual_pnpm_version="$(pnpm --version)"
if [[ "$actual_pnpm_version" != "$pnpm_version" ]]; then
  echo "release pnpm version mismatch: $actual_pnpm_version != $pnpm_version" >&2
  exit 2
fi
pnpm install --frozen-lockfile
pnpm build:ts
node scripts/check_contract_conformance.mjs
node scripts/check_control_react.mjs

rm -rf .artifacts/release-build
mkdir -p .artifacts/release-build "$output"
uv build --all-packages --out-dir .artifacts/release-build
for package_dir in packages/typescript/*; do
  test -f "$package_dir/package.json" || continue
  pnpm --dir "$package_dir" pack --pack-destination "$GITHUB_WORKSPACE/.artifacts/release-build"
done

uv run --python "$python_version" --no-sync python scripts/inspect_release_artifacts.py \
  .artifacts/release-build --version "$version"

# uv may place build-control files (for example .gitignore) beside distributions.
# Publish only package payloads; the central release contract rejects hidden names.
find .artifacts/release-build -maxdepth 1 -type f \
  \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.tgz' \) \
  -exec cp {} "$output/" \;
test -n "$(find "$output" -maxdepth 1 -type f -print -quit)"
