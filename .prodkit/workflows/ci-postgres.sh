#!/usr/bin/env bash
set -euo pipefail

: "${PRODKIT_POSTGRES_HOST:?PRODKIT_POSTGRES_HOST is required}"
: "${PRODKIT_POSTGRES_PORT:?PRODKIT_POSTGRES_PORT is required}"
: "${PRODKIT_POSTGRES_DATABASE:?PRODKIT_POSTGRES_DATABASE is required}"
: "${PRODKIT_POSTGRES_USER:?PRODKIT_POSTGRES_USER is required}"
: "${PRODKIT_POSTGRES_PASSWORD:?PRODKIT_POSTGRES_PASSWORD is required}"

uv python install 3.13
uv sync --all-packages --group dev --locked --python 3.13
uv run --python 3.13 --no-sync python scripts/ci_postgres.py
