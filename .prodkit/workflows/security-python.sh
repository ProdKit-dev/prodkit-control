#!/usr/bin/env bash
set -euo pipefail

# Temporary read-only v0.2.0 schema-generation probe. Restore before candidate acceptance.
uv python install 3.13
python3 scripts/set_release_version.py 0.2.0
uv lock
uv sync --all-packages --group dev --locked --python 3.13
uv run --python 3.13 --no-sync python scripts/export_schemas.py
uv run --python 3.13 --no-sync ruff format .

for name in \
  external-audit-event.schema.json \
  external-state-observation.schema.json \
  production-completeness-assessment.schema.json \
  production-completeness-profile.schema.json \
  reconciliation-batch.schema.json \
  reconciliation-cursor.schema.json \
  reconciliation-run-result.schema.json; do
  echo "=== PRODKIT_SCHEMA_BEGIN:${name} ==="
  base64 -w0 "schemas/${name}"
  echo
  echo "=== PRODKIT_SCHEMA_END:${name} ==="
done

uv run --python 3.13 --no-sync --with pip-audit==2.10.1 pip-audit --local --skip-editable
