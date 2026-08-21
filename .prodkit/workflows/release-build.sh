#!/usr/bin/env bash
set -euo pipefail

output="${RELEASE_OUTPUT_DIR:?RELEASE_OUTPUT_DIR is required}"
version="${RELEASE_VERSION:?RELEASE_VERSION is required}"
python_version="${PRODKIT_RELEASE_PYTHON_VERSION:-3.13}"
pnpm_version="${PRODKIT_RELEASE_PNPM_VERSION:-10.15.0}"

python3 scripts/release_check.py --version "$version"
uv python install "$python_version"
uv sync --all-packages --group dev --locked --python "$python_version"
command -v pnpm >/dev/null
actual_pnpm_version="$(pnpm --version)"
if [[ "$actual_pnpm_version" != "$pnpm_version" ]]; then
  echo "release pnpm version mismatch: $actual_pnpm_version != $pnpm_version" >&2
  exit 2
fi
pnpm install --frozen-lockfile

rm -rf .artifacts/release-build
mkdir -p .artifacts/release-build "$output"
uv build --all-packages --out-dir .artifacts/release-build
pnpm build:ts
for package_dir in packages/typescript/*; do
  test -f "$package_dir/package.json" || continue
  pnpm --dir "$package_dir" pack --pack-destination "$GITHUB_WORKSPACE/.artifacts/release-build"
done

uv run --python "$python_version" --no-sync python scripts/inspect_release_artifacts.py \
  .artifacts/release-build --version "$version"
find .artifacts/release-build -maxdepth 1 -type f -exec cp {} "$output/" \;
test -n "$(find "$output" -maxdepth 1 -type f -print -quit)"
