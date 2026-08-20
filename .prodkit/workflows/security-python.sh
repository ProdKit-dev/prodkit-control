#!/usr/bin/env bash
set -euo pipefail

uv python install 3.13
uv sync --all-packages --group dev --locked --python 3.13
uv run --python 3.13 --no-sync --with pip-audit==2.10.1 pip-audit --local --skip-editable
