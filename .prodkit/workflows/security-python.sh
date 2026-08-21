#!/usr/bin/env bash
set -euo pipefail

# One-shot v0.2.0 candidate builder. Copy out of the worktree so the candidate
# can restore the permanent Security contract before validation and commit.
if [[ "${1:-}" != "--inner" ]]; then
  script="${RUNNER_TEMP:?RUNNER_TEMP is required}/prodkit-control-v020-candidate.sh"
  cp "$0" "$script"
  exec bash "$script" --inner
fi

EXPECTED_MAIN=89a7546ec06a2eecc59d2150c037d25c866a1538
CENTRAL_SHA=7f3d25ab467cfef1c1e2bcb397da461964f39204
BRANCH=feat/v0.2.0-delivery-chain-reconciliation
POSTGRES_CONTAINER="prodkit-control-v020-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
trap 'docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true' EXIT

git fetch origin main "$BRANCH" --tags --force
test "$(git rev-parse origin/main)" = "$EXPECTED_MAIN"

git checkout origin/main -- \
  .github/workflows/security.yml \
  .prodkit/workflows/security-python.sh
rm -f .github/workflows/ops-v020-candidate.yml

python3 - <<'PY'
from pathlib import Path

central_old = "645f3c66557e4a673be38c461a762021536fda00"
central_new = "7f3d25ab467cfef1c1e2bcb397da461964f39204"
paths = (
    Path('.github/workflows/ci.yml'),
    Path('.github/workflows/security.yml'),
    Path('.github/workflows/codeql.yml'),
    Path('.github/workflows/trusted-release-proof.yml'),
    Path('.github/workflows/release.yml'),
    Path('.github/workflows/release-metadata.yml'),
    Path('scripts/test_workflow_alignment.py'),
)
for path in paths:
    text = path.read_text(encoding='utf-8')
    count = text.count(central_old)
    if count != 1:
        raise SystemExit(f'{path}: expected one old central pin, found {count}')
    path.write_text(text.replace(central_old, central_new, 1), encoding='utf-8')

engine = Path('packages/python/prodkit-control-core/src/prodkit_control_core/reconciliation/engine.py')
text = engine.read_text(encoding='utf-8')
old = '        tenant_id=profile.tenant_id,\n        assessed_at=assessed_at,\n'
new = '        tenant_id=profile.tenant_id,\n        organization_id=profile.organization_id,\n        assessed_at=assessed_at,\n'
if 'organization_id=profile.organization_id' not in text:
    if text.count(old) != 1:
        raise SystemExit('cannot locate completeness assessment scope return')
    engine.write_text(text.replace(old, new, 1), encoding='utf-8')
PY

python3 scripts/set_release_version.py 0.2.0
python3 - <<'PY'
from pathlib import Path

path = Path('CHANGELOG.md')
text = path.read_text(encoding='utf-8')
anchor = '## [Unreleased]\n'
section = '''## [0.2.0] - 2026-08-22

### Added

- Delivery-chain reconciliation across Git, GitHub, CI/build, registries, deployments, Kubernetes, and database/control-plane evidence.
- Canonical external state/audit contracts, deterministic findings, durable cursors, audit-event deduplication, and organization/tenant production-completeness profiles.
- PostgreSQL schema version 4 with PostgreSQL 18 durability coverage for reconciliation state.
- Configurable polling, freshness, capped exponential backoff, provider-shaped fixtures, and documented reconciliation SLO/escalation policy.

### Changed

- Reconciliation is fail-closed for stale, unavailable, conflicting, missing, and unexpected external evidence.
- Production completeness can require fresh healthy matched evidence from an explicit organization/tenant source set.

### Security

- Delivery-chain activity without a controlled action produces a high-severity `unexpected_external_action` finding.
- Conflicting evidence is explicit `conflicting_evidence`; one observation is never silently selected.

### Release scope

`v0.2.0` is the delivery-chain reconciliation milestone. Signed/interoperable provenance and key management remain `v0.3.0`.

'''
if '## [0.2.0] - ' not in text:
    if anchor not in text:
        raise SystemExit('CHANGELOG Unreleased anchor missing')
    path.write_text(text.replace(anchor, anchor + '\n' + section, 1), encoding='utf-8')
PY

uv python install 3.13
uv lock
uv sync --all-packages --group dev --locked --python 3.13
uv run --python 3.13 --no-sync ruff format .
uv run --python 3.13 --no-sync python scripts/export_schemas.py

bash .prodkit/workflows/ci-hygiene.sh
PRODKIT_PYTHON_VERSION=3.13 bash .prodkit/workflows/ci-python.sh

corepack enable
corepack prepare pnpm@10.15.0 --activate
test "$(pnpm --version)" = "10.15.0"
PRODKIT_NODE_VERSION=24 bash .prodkit/workflows/ci-node.sh

docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$POSTGRES_CONTAINER" \
  -e POSTGRES_DB=prodkit_ci -e POSTGRES_USER=prodkit -e POSTGRES_PASSWORD=prodkit \
  -p 127.0.0.1::5432 postgres:18-alpine >/dev/null
for _ in $(seq 1 45); do
  if docker exec "$POSTGRES_CONTAINER" pg_isready -U prodkit -d prodkit_ci >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$POSTGRES_CONTAINER" pg_isready -U prodkit -d prodkit_ci >/dev/null
PGPORT="$(docker port "$POSTGRES_CONTAINER" 5432/tcp | awk -F: 'END{print $NF}')"
PRODKIT_POSTGRES_HOST=127.0.0.1 \
PRODKIT_POSTGRES_PORT="$PGPORT" \
PRODKIT_POSTGRES_DATABASE=prodkit_ci \
PRODKIT_POSTGRES_USER=prodkit \
PRODKIT_POSTGRES_PASSWORD=prodkit \
bash .prodkit/workflows/ci-postgres.sh

uv run --python 3.13 --no-sync python scripts/release_check.py --version 0.2.0
uv run --python 3.13 --no-sync python scripts/test_workflow_alignment.py
uv run --python 3.13 --no-sync --with pip-audit==2.10.1 pip-audit --local --skip-editable
git diff --check

! grep -R "645f3c66557e4a673be38c461a762021536fda00" \
  .github/workflows/ci.yml \
  .github/workflows/security.yml \
  .github/workflows/codeql.yml \
  .github/workflows/trusted-release-proof.yml \
  .github/workflows/release.yml \
  .github/workflows/release-metadata.yml \
  scripts/test_workflow_alignment.py
grep -q 'organization_id=profile.organization_id' packages/python/prodkit-control-core/src/prodkit_control_core/reconciliation/engine.py
grep -q 'version = "0.2.0"' pyproject.toml
test -f schemas/reconciliation-run-result.schema.json
test -f packages/python/prodkit-control-postgres/src/prodkit_control_postgres/migrations/0004_delivery_chain_reconciliation.sql

git add -- \
  pyproject.toml \
  uv.lock \
  CHANGELOG.md \
  schemas \
  packages/python \
  packages/typescript \
  tests \
  scripts \
  .prodkit/workflows/security-python.sh \
  .github/workflows/ci.yml \
  .github/workflows/security.yml \
  .github/workflows/codeql.yml \
  .github/workflows/trusted-release-proof.yml \
  .github/workflows/release.yml \
  .github/workflows/release-metadata.yml \
  .github/workflows/ops-v020-candidate.yml
git diff --cached --check

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git commit -m 'release: prepare v0.2.0 candidate'
echo "CANDIDATE_SHA=$(git rev-parse HEAD)"
echo "CANDIDATE_TREE=$(git rev-parse HEAD^{tree})"
git push origin HEAD:"$BRANCH"
