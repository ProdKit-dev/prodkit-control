#!/usr/bin/env bash
set -euo pipefail

output="${PRODKIT_PROOF_OUTPUT_DIR:?PRODKIT_PROOF_OUTPUT_DIR is required}"
python_version="3.13"
pnpm_version="10.15.0"
version="$(python3 - <<'PY'
import tomllib
with open('pyproject.toml', 'rb') as handle:
    print(tomllib.load(handle)['project']['version'])
PY
)"

mkdir -p "$output/artifacts"

command -v uv >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
command -v docker >/dev/null
uv python install "$python_version"
uv sync --all-packages --group dev --locked --python "$python_version"

uv run --python "$python_version" --no-sync ruff format --check .
uv run --python "$python_version" --no-sync ruff check .
uv run --python "$python_version" --no-sync mypy
uv run --python "$python_version" --no-sync pytest \
  --junitxml="$output/pytest.xml" \
  --cov-report="xml:$output/coverage.xml"
uv run --python "$python_version" --no-sync python scripts/export_schemas.py --check
uv run --python "$python_version" --no-sync --with pip-audit==2.10.1 pip-audit --local --skip-editable
python3 scripts/release_check.py --version "$version"

postgres_name="prodkit-proof-postgres-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
cleanup_postgres() {
  docker rm -f "$postgres_name" >/dev/null 2>&1 || true
}
trap cleanup_postgres EXIT
cleanup_postgres
docker run -d --name "$postgres_name" \
  -e POSTGRES_DB=prodkit_ci \
  -e POSTGRES_USER=prodkit \
  -e POSTGRES_PASSWORD=prodkit \
  -p 127.0.0.1::5432 postgres:18-alpine >/dev/null
for _ in $(seq 1 45); do
  if docker exec "$postgres_name" pg_isready -U prodkit -d prodkit_ci >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$postgres_name" pg_isready -U prodkit -d prodkit_ci >/dev/null
export PRODKIT_POSTGRES_HOST=127.0.0.1
export PRODKIT_POSTGRES_PORT="$(docker port "$postgres_name" 5432/tcp | awk -F: 'END{print $NF}')"
export PRODKIT_POSTGRES_DATABASE=prodkit_ci
export PRODKIT_POSTGRES_USER=prodkit
export PRODKIT_POSTGRES_PASSWORD=prodkit
uv run --python "$python_version" --no-sync python scripts/ci_postgres.py
cleanup_postgres
trap - EXIT

corepack enable
corepack prepare "pnpm@$pnpm_version" --activate
pnpm install --frozen-lockfile
pnpm audit --audit-level high
pnpm typecheck:ts
pnpm build:ts

uv build --all-packages --out-dir "$output/artifacts"
for package_dir in packages/typescript/*; do
  test -f "$package_dir/package.json" || continue
  pnpm --dir "$package_dir" pack --pack-destination "$output/artifacts"
done

uv run --python "$python_version" --no-sync python scripts/inspect_release_artifacts.py \
  "$output/artifacts" --version "$version"

node_version="$(node --version)"
python3 - "$output/proof-summary.json" "$version" "$node_version" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
node_version = sys.argv[3]
artifacts = sorted(p.name for p in (path.parent / 'artifacts').iterdir() if p.is_file())
path.write_text(
    json.dumps(
        {
            'schema_version': 1,
            'version': version,
            'python': '3.13',
            'node': node_version,
            'pnpm': '10.15.0',
            'postgres': '18-alpine',
            'artifact_count': len(artifacts),
            'artifacts': artifacts,
        },
        indent=2,
        sort_keys=True,
    ) + '\n',
    encoding='utf-8',
)
PY
