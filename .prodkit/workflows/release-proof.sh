#!/usr/bin/env bash
set -euo pipefail

python_version="${PRODKIT_PROOF_PYTHON_VERSION:?PRODKIT_PROOF_PYTHON_VERSION is required}"
node_version="${PRODKIT_PROOF_NODE_VERSION:?PRODKIT_PROOF_NODE_VERSION is required}"
pnpm_version="${PRODKIT_PROOF_PNPM_VERSION:?PRODKIT_PROOF_PNPM_VERSION is required}"
source_sha="${SOURCE_SHA:?SOURCE_SHA is required}"

test "$python_version" = "3.13"
test "$node_version" = "24"
test "$pnpm_version" = "10.15.0"
command -v uv >/dev/null
command -v node >/dev/null
command -v pnpm >/dev/null
[[ "$(node --version)" == "v${node_version}."* ]]
test "$(pnpm --version)" = "$pnpm_version"

# Permanent exact-SHA CI, Security, and CodeQL are verified by the reusable
# proof before this repository adapter runs. Do not replay their Python/Node
# matrices, PostgreSQL acceptance, dependency audits, typechecks, or builds.
# The reusable proof also runs release-build.sh exactly once when
# prepare_release_payload=true and seals that payload for Release to reuse.
version="$(python3 - <<'PY'
import tomllib
with open('pyproject.toml', 'rb') as handle:
    print(tomllib.load(handle)['project']['version'])
PY
)"
python3 scripts/release_check.py --version "$version"

test "$(git rev-parse HEAD)" = "$source_sha"
git diff --exit-code -- .
git diff --cached --exit-code -- .
