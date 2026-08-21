#!/usr/bin/env bash
set -euo pipefail

# Temporary v0.2.0 normalization probe. This runs inside the permanent Security
# workflow with read-only repository permissions and emits the generated diff;
# it must be restored before the release candidate is accepted.
uv python install 3.13
python3 scripts/set_release_version.py 0.2.0
uv lock
uv sync --all-packages --group dev --locked --python 3.13
uv run --python 3.13 --no-sync python scripts/export_schemas.py
uv run --python 3.13 --no-sync ruff format .

echo '=== PRODKIT_V020_GENERATED_DIFF_BEGIN ==='
git diff -- . ':(exclude).prodkit/workflows/security-python.sh'
echo '=== PRODKIT_V020_GENERATED_DIFF_END ==='

uv run --python 3.13 --no-sync --with pip-audit==2.10.1 pip-audit --local --skip-editable
