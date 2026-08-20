#!/usr/bin/env bash
set -euo pipefail

python_version="${PRODKIT_PYTHON_VERSION:?PRODKIT_PYTHON_VERSION is required}"
uv sync --all-packages --group dev --locked --python "$python_version"
uv run --python "$python_version" --no-sync ruff format --check .
uv run --python "$python_version" --no-sync ruff check .
uv run --python "$python_version" --no-sync mypy
uv run --python "$python_version" --no-sync pytest --cov-report=xml
uv run --python "$python_version" --no-sync python scripts/export_schemas.py --check

if [[ "$python_version" == "3.13" ]]; then
  rm -rf .artifacts/python-release
  mkdir -p .artifacts/python-release
  uv build --all-packages --out-dir .artifacts/python-release
  test -n "$(find .artifacts/python-release -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' \) -print -quit)"
fi
