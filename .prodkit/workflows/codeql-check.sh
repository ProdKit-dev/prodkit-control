#!/usr/bin/env bash
set -euo pipefail

output_dir="${PRODKIT_CODEQL_OUTPUT_DIR:?PRODKIT_CODEQL_OUTPUT_DIR is required}"
python3 scripts/check_codeql_sarif.py "$output_dir"
